const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(require('path').join(__dirname, '..', 'static', 'index.html'), 'utf8');
const start = html.indexOf('        window.monthlyRecurringIncomeUSD = () => {');
const end = html.indexOf('        const renderDebtPlan = () => {', start);
if (start < 0 || end < 0) throw new Error('No se encontró el planificador en index.html');
const source = html.slice(start, end);
const window = {
  state: {
    income: { amount: 0, currency: 'USD' },
    incomeSources: [{ amount: 1500, currency: 'USD', frequency: 'quincenal' }],
    debtPlan: { monthlyEssentials: 1500, monthlyCushion: 300, paymentPercent: 60, strategy: 'avalanche', configured: true },
    debts: [
      { id: 'loan1', entity: 'Prestamista A', description: 'Préstamo', amount: 5000, paid: 0, currency: 'USD', minPayment: 100, interestRate: 3, interestPeriod: 'mensual', interestKnown: true },
      { id: 'card1', entity: 'Banco B', description: 'Tarjeta', amount: 1000, paid: 0, currency: 'USD', minPayment: 50, interestRate: 0, interestPeriod: 'mensual', interestKnown: true }
    ]
  },
  convertToUSD(amount, currency) { return currency === 'BS' ? Number(amount) / 40 : Number(amount); }
};
vm.runInNewContext(source, { window, Math, Number, Set, Map, JSON });
const calc = window.calculateDebtPlan();
if (calc.income !== 3000) throw new Error(`Ingreso mensual incorrecto: ${calc.income}`);
if (calc.safeCapacity !== 1200) throw new Error(`Capacidad segura incorrecta: ${calc.safeCapacity}`);
if (calc.recommendedPayment !== 720) throw new Error(`Pago recomendado incorrecto: ${calc.recommendedPayment}`);
if (!calc.projections || !Number.isInteger(calc.projections.months) || calc.projections.months < 1) throw new Error('No generó proyección con cuotas y tasas completas');
if (calc.projections.firstMonthPayments.length !== 2) throw new Error('La distribución inicial no incluye ambas deudas');

window.state.debts[1].minPayment = 0;
const missing = window.calculateDebtPlan();
if (missing.missingMinimums.length !== 1 || missing.projections !== null) throw new Error('Debe bloquear la proyección si falta una cuota mínima');

window.state.debts[1].minPayment = 50;
window.state.debtPlan.monthlyEssentials = 2900;
window.state.debtPlan.monthlyCushion = 200;
const exceeded = window.calculateDebtPlan();
if (!exceeded.minimumsExceedCapacity || exceeded.projections !== null) throw new Error('Debe alertar si los mínimos superan la capacidad protegida');
console.log('OK: cálculo mensual, simulación, cuotas mínimas faltantes y capacidad insuficiente.');
