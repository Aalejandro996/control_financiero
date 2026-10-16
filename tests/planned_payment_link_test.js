const fs = require('fs');
const path = require('path');
const vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');
const start = html.indexOf('        // --- Relación entre la agenda y el saldo real de cada deuda ---');
const end = html.indexOf('        // --- Fechas del planificador sincronizadas con los días de cobro ---', start);
if (start < 0 || end < 0) throw new Error('No se encontraron las funciones de relación pago-deuda.');
const source = html.slice(start, end);
const window = { state: {
  debts: [{ id: 'card-1', amount: 500, paid: 100, currency: 'USD' }],
  plannedDebtPayments: [
    { id: 'plan-active', debtId: 'card-1', amount: 80, status: 'planned' },
    { id: 'plan-paid', debtId: 'card-1', amount: 40, status: 'paid' },
    { id: 'plan-cancelled', debtId: 'card-1', amount: 20, status: 'cancelled' },
    { id: 'plan-other-debt', debtId: 'card-2', amount: 200, status: 'planned' }
  ]
}};
vm.runInNewContext(source, { window, Math, Number, String });
if (window.getDebtOutstandingAmount('card-1') !== 400) throw new Error('Saldo real no respeta los abonos confirmados.');
if (window.getDebtReservedByPlans('card-1') !== 80) throw new Error('El monto reservado no cuenta únicamente planes activos de esa deuda.');
if (window.getDebtAvailableToPlan('card-1') !== 320) throw new Error('El saldo disponible para nuevas planificaciones es incorrecto.');
if (window.getDebtAvailableToPlan('missing') !== 0) throw new Error('Una deuda inexistente debe tener capacidad de planificación cero.');
const scheduledBranch = html.slice(html.indexOf("else if (type === 'payDebt')"), html.indexOf("else if (type === 'incomeSource')"));
if (!scheduledBranch.includes('debtId: debt.id')) throw new Error('La planificación no queda relacionada con la deuda registrada.');
const scheduleCreate = scheduledBranch.slice(scheduledBranch.indexOf('if (isScheduled)'), scheduledBranch.indexOf('} else {'));
if (scheduleCreate.includes('debt.paid =')) throw new Error('Crear un plan no debe reducir el saldo real antes del pago.');
const paidHandler = html.slice(html.indexOf('window.markPlannedPaymentPaid ='), html.indexOf('window.cancelPlannedPayment ='));
if (!paidHandler.includes("plan.status = 'paid'") || !paidHandler.includes('debt.paid =')) throw new Error('Confirmar el pago debe actualizar el saldo y cerrar el plan.');
console.log('OK: el plan queda vinculado a debtId, reserva saldo sin contabilizar pago y reduce la deuda al confirmar pago real.');
