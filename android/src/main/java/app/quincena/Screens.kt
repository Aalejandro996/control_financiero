package app.quincena

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import java.time.YearMonth
import kotlin.math.abs
import kotlin.math.min

/** Texto con partes en negrita: rich("Si pagas ", "$ 10" to true, " al mes"). */
@Composable
fun Rich(vararg parts: Any) = Text(buildAnnotatedString {
    for (x in parts) when (x) {
        is Pair<*, *> -> if (x.second == true) withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(x.first.toString()) } else append(x.first.toString())
        else -> append(x.toString())
    }
})

private fun freqLabel(f: String?) = when (f) { "quincenal" -> "Cada quincena"; "mensual" -> "Una vez al mes"; "ocasional" -> "Solo una vez"; else -> f ?: "" }
private fun meses(n: Int) = "$n ${if (n == 1) "mes" else "meses"}"

// =====================================================================
// INICIO
// =====================================================================

@Composable
fun Inicio(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    val p = pal
    val c = monthSummary(d)
    val per = st.per
    val cp = summarize(d, per)
    val plan = debtPlan(d)

    Head(st)

    // Selector de periodo
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Link("‹ Antes", { st.per = per.step(-1) })
        B(per.label())
        Link("Después ›", { st.per = per.step(1) })
    }
    Chips(center = true) {
        Chip("1.ª quincena", per.half == 1) { st.per = per.copy(half = 1) }
        Chip("2.ª quincena", per.half == 2) { st.per = per.copy(half = 2) }
        Chip("Mes completo", per.half == 0) { st.per = per.copy(half = 0) }
    }

    // Tarjeta principal
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(if (cp.free < 0) HeroNeg else p.g).padding(horizontal = 20.dp, vertical = 22.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ProvideTextStyle(LocalTextStyle.current.copy(color = Color.White)) {
            Text("${if (cp.free < 0) "Te faltan" else "Te quedan"} ${if (per.half == 0) "este mes" else "esta quincena"}")
            val amount = f.show(abs(cp.free))
            Big(amount, size = when { amount.length <= 12 -> 48.sp; amount.length <= 16 -> 38.sp; else -> 30.sp })
            Bar(if (cp.income > 0) cp.spent / cp.income else 0.0, color = Color.White, track = Color.White.copy(alpha = .28f))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Gastado ${f.show(cp.spent)}", fontSize = 14.sp)
                Text("Ingresos ${f.show(cp.income)}", fontSize = 14.sp)
            }
            if (cp.due > 0) Text("Ya descontado: ${f.show(cp.due)} en pagos de este periodo", fontSize = 14.sp)
            val mk = marketRateOf(d)
            Text("Dólar BCV: Bs. ${f.plain(rateOf(d))}${if (mk > 0) " · USDT: Bs. ${f.plain(mk)}" else ""}${st.rateInfo?.let { " · $it" } ?: ""}",
                fontSize = 13.sp, color = Color.White.copy(alpha = .85f))
        }
    }

    // Confirmar correo
    val mail = st.mail
    val email = mail?.first
    if (email != null && mail?.second == false) QCard(bg = p.soft) {
            B("Confirma tu correo")
            Text("Te enviamos un enlace a $email. Lo necesitas para recuperar tu contraseña.")
            Btn("Reenviar enlace", {
                st.scope.launch {
                    try { st.repo.resendVerification(); st.toast("Listo, te enviamos el enlace. Revisa también el spam.") }
                    catch (e: ApiError) { st.toast(e.message ?: "Algo salió mal.") }
                    catch (e: Offline) { st.toast("Sin conexión. Revisa tu internet e inténtalo de nuevo.") }
                }
            }, small = true, outline = true, full = true)
    }

    // Empecemos en 3 pasos
    val n = d.arr("expenses").size + d.arr("incomeSources").size + d.arr("debts").size
    val steps = listOf(
        Triple(d.arr("incomeSources").isNotEmpty(), "Agrega tu ingreso", "ingreso"),
        Triple(d.obj("budgets").keys.any { d.obj("budgets").num(it) > 0 }, "Define tu presupuesto", "presupuesto"),
        Triple(d.arr("expenses").isNotEmpty(), "Registra tu primer gasto", "gasto"),
    )
    if (n < 3 && steps.any { !it.first }) QCard {
        B("Empecemos en 3 pasos")
        steps.forEachIndexed { i, (done, label, kind) ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(if (done) "✓" else "${i + 1}", Modifier.width(24.dp), fontWeight = FontWeight.Bold, color = p.g)
                Text(label, Modifier.weight(1f), color = if (done) p.mut else p.ink, textDecoration = if (done) TextDecoration.LineThrough else null)
                if (!done) Btn("Hacer", { st.open(kind) }, small = true)
            }
        }
    }

    // Próximo pago planificado
    d.arr("plannedDebtPayments").filter { it.str("status") == "planned" && dateOf(it.str("scheduledDate")) != null }
        .minByOrNull { it.str("scheduledDate")!! }?.let { next ->
            val days = daysUntil(dateOf(next.str("scheduledDate"))!!)
            QCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        B(next.str("entity") ?: next.str("description") ?: "Pago")
                        Mut(if (days < 0) "Venció hace ${-days} días" else if (days == 0) "Vence hoy" else "Vence en $days días")
                    }
                    Amt(f.orig(next.num("amount"), next.str("currency")))
                }
            }
        }

    DetectadosCard(st)
    ComprometidoMini(st)
    CajaRow(st)

    // Presupuesto
    val budgets = d.obj("budgets")
    QCard {
        B("Tu presupuesto")
        val withBudget = CATS.keys.filter { budgets.num(it) > 0 }
        if (withBudget.isEmpty()) Mut("Define cuánto quieres gastar en cada categoría desde Más › Mi presupuesto.")
        withBudget.forEach { k ->
            val b = budgets.num(k)
            val sp = c.cat[k] ?: 0.0
            val pct = min(100.0, sp / b * 100)
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(CATS[k]!!)
                    Mut("${f.show(sp)} de ${f.show(b)}")
                }
                Bar(pct / 100, color = if (pct >= 95) p.bad else if (pct >= 80) p.warn else p.g)
            }
        }
    }

    // Tus tasas este mes
    TasasCard(st)
    PoderCard(st)

    // En qué se va tu dinero
    val tot = c.cat.values.sum()
    if (tot > 0) QCard {
        B("En qué se va tu dinero")
        val ks = CATS.keys.filter { (c.cat[it] ?: 0.0) > 0 }
        Row(Modifier.fillMaxWidth().height(14.dp).clip(CircleShape)) {
            ks.forEach { k -> Box(Modifier.weight((c.cat[k]!! / tot).toFloat()).fillMaxHeight().background(Color(CAT_COLORS[k]!!))) }
        }
        ks.forEach { k ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.size(12.dp).clip(CircleShape).background(Color(CAT_COLORS[k]!!)))
                    Text(CATS[k]!!)
                }
                Mut("${Math.round(c.cat[k]!! / tot * 100)} %")
            }
        }
    }

    // Tu camino para salir de deudas
    val dt = debtTotals(d)
    if (dt.total > 0 && c.debt > .005) QCard(bg = p.gs) {
        B("Tu camino para salir de deudas")
        val months = if (plan.configured && plan.months != null) plan.months else c.months
        val pay = if (plan.configured && plan.rec > 0) plan.rec else c.pay
        if (months != null) Column {
            Big(meses(months), size = 36.sp)
            Mut("Aprox. ${monthName(YearMonth.now().plusMonths(months.toLong()), true)}${if (pay > 0) " · pagando ${f.show(pay)} al mes" else ""}")
        } else Text(if (c.left < 0) "Este mes tus gastos superan tus ingresos. Revisemos juntos dónde ajustar." else "Agrega tus ingresos y el pago mínimo de cada deuda para calcular cuándo terminas.")
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Bar(dt.paid / dt.total)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Mut("Pagado ${f.show(dt.paid)}")
                Mut("Falta ${f.show(c.debt)}")
            }
        }
        Btn("Ver plan", { st.go("deudas") }, small = true, outline = true, full = true)
    }
}

/** "Tus tasas este mes": brecha BCV/USDT y cuánto ahorraste o perdiste por la tasa. */
@Composable
fun TasasCard(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    val p = pal
    val m = marketRateOf(d)
    val b = rateOf(d)
    if (m <= 0 || b <= 0) return
    val t = tasasDelMes(d)
    val gap = Math.round((m / b - 1) * 100)
    QCard {
        B("Tus tasas este mes")
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Pill("BCV Bs. ${f.plain(b)}", p.gs, p.g)
            Pill("USDT Bs. ${f.plain(m)}", p.soft, p.warn)
            Mut("Brecha ${if (gap > 0) "+" else ""}$gap %")
        }
        when {
            t.n == 0 -> Mut("Cuando registres un gasto en bolívares, marca a qué tasa los conseguiste y aquí verás cuánto te costó de verdad.")
            t.tagged == 0 -> Mut("Este mes registraste ${t.n} ${if (t.n == 1) "gasto" else "gastos"} en bolívares, todos a tasa BCV. Si cambiaste dólares por USDT u otra tasa, márcalo en el gasto para ver tu costo real.")
            else -> {
                Column {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("Al BCV equivalen a"); Amt(f.orig(t.atBcv, "USD")) }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("Con las tasas que usaste"); Amt(f.orig(t.real, "USD")) }
                }
                when {
                    t.diff > .005 -> Pill("Te ahorraste ${f.orig(t.diff, "USD")} por la tasa", p.gs, p.g)
                    t.diff < -.005 -> Pill("Te costó ${f.orig(-t.diff, "USD")} de más por la tasa", p.bad.copy(alpha = .15f), p.bad)
                    else -> Pill("Sin diferencia por la tasa", p.soft, p.warn)
                }
            }
        }
    }
}

@Composable
fun Pill(text: String, bg: Color, fg: Color) {
    Box(Modifier.clip(CircleShape).background(bg).padding(horizontal = 10.dp, vertical = 3.dp)) {
        Text(text, color = fg, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}

// =====================================================================
// MOVIMIENTOS
// =====================================================================

@Composable
fun Movimientos(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    Head(st)
    Chips {
        Chip("Gastos", st.movSub == "gastos") { st.movSub = "gastos" }
        Chip("Ingresos", st.movSub == "ingresos") { st.movSub = "ingresos" }
        Chip("Fijos", st.movSub == "fijos") { st.movSub = "fijos" }
    }
    when (st.movSub) {
        "fijos" -> {
            QCard {
                Items(d.arr("recurring"), "Aquí van los gastos que se repiten cada mes, como alquiler o internet. Se registran solos el día que indiques.") { r ->
                    Column(Modifier.weight(1f)) {
                        B(r.str("description") ?: "Gasto fijo")
                        Mut("${CATS[r.str("category")] ?: "Otros"} · el día ${r.num("day").toInt()} de cada mes${if (r.bool("active") == false) " · en pausa" else ""}")
                    }
                    Amt(f.orig(r.num("amount"), r.str("currency")))
                    Link("Editar", { st.open("editarr", r.id) })
                }
            }
            Btn("Agregar gasto fijo", { st.open("recurrente") })
        }
        "ingresos" -> {
            QCard {
                Items(d.arr("incomeSources"), "Agrega tu sueldo u otros ingresos para ver cuánto te queda.") { s ->
                    Column(Modifier.weight(1f)) {
                        B(s.str("name") ?: "Ingreso")
                        Mut(freqLabel(s.str("frequency")))
                    }
                    Amt(f.orig(s.num("amount"), s.str("currency")))
                    Link("Editar", { st.open("editari", s.id) })
                }
            }
            Btn("Agregar ingreso", { st.open("ingreso") })
        }
        else -> {
            val now = YearMonth.now().toString()
            val months = (listOf(now) + d.arr("expenses").mapNotNull { it.str("date")?.take(7) }.filter { it.length == 7 }).distinct().sortedDescending()
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    st.q, { st.q = it }, Modifier.weight(1f), singleLine = true, shape = RoundedCornerShape(12.dp), colors = fieldColors(),
                    placeholder = { Text("Buscar gasto", color = pal.mut) },
                )
                Box(Modifier.width(150.dp)) {
                    SelectBox(listOf("" to "Todos") + months.map { it to monthShort(it) }, st.mon) { st.mon = it }
                }
            }
            val qq = st.q.trim().lowercase()
            val list = d.arr("expenses").filter { e ->
                (st.mon.isEmpty() || e.str("date")?.take(7) == st.mon) &&
                    (qq.isEmpty() || (e.str("description") ?: "").lowercase().contains(qq) || (CATS[e.str("category")] ?: "").lowercase().contains(qq))
            }.take(100)
            QCard {
                Items(list, "Aún no registras gastos. Toca + para agregar el primero.") { e ->
                    Column(Modifier.weight(1f)) {
                        B(e.str("description")?.takeIf { it.isNotBlank() } ?: CATS[e.str("category")] ?: "Gasto")
                        val rt = e.str("rateType")
                        val tasa = if (e.str("currency") == "BS" && rt != null && rt != "bcv")
                            " · tasa ${RATE_LABELS[rt] ?: rt} ${f.plain(e.num("rateUsed"))} ≈ ${f.show(realUsd(d, e))}" else ""
                        Mut("${CATS[e.str("category")] ?: "Otros"} · ${dateOf(e.str("date"))?.let { shortDate(it) } ?: ""}$tasa")
                    }
                    Amt(f.orig(e.num("amount"), e.str("currency")))
                    Link("Editar", { st.open("editar", e.id) })
                }
            }
        }
    }
}

private fun monthShort(ym: String): String = runCatching {
    val y = YearMonth.parse(ym)
    y.month.getDisplayName(java.time.format.TextStyle.SHORT, java.util.Locale("es")).trimEnd('.') + " " + y.year
}.getOrDefault(ym)

/** Lista desplegable compacta, sin título. */
@Composable
fun SelectBox(options: List<Pair<String, String>>, value: String, onChange: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val p = pal
    Box {
        TextButton({ expanded = true }, Modifier.fillMaxWidth().heightIn(min = 56.dp), shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.textButtonColors(containerColor = p.card),
            border = androidx.compose.foundation.BorderStroke(1.dp, p.line)) {
            Text(options.firstOrNull { it.first == value }?.second ?: "Todos", Modifier.weight(1f), color = p.ink, maxLines = 1)
            Text("▾", color = p.mut)
        }
        DropdownMenu(expanded, { expanded = false }) {
            options.forEach { (v, l) -> DropdownMenuItem(text = { Text(l) }, onClick = { onChange(v); expanded = false }) }
        }
    }
}

// =====================================================================
// DEUDAS
// =====================================================================

@Composable
fun Deudas(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    val plan = debtPlan(d)
    Head(st)
    QCard {
        B("Tu plan para salir de deudas")
        if (plan.configured) {
            when {
                plan.months != null -> Rich("Si pagas ", f.show(plan.rec) to true, " al mes, terminas en ", meses(plan.months) to true,
                    " (intereses estimados: ${f.show(plan.interest)}).")
                plan.over -> Rich("Si pagas ", f.show(plan.rec) to true, " al mes. Tus cuotas mínimas superan lo que puedes pagar: revisa tus gastos o negocia plazos.")
                else -> Rich("Si pagas ", f.show(plan.rec) to true, " al mes. Agrega el pago mínimo de cada deuda para calcular el plazo.")
            }
        } else Mut("Cuéntanos cuánto necesitas para vivir y te decimos cuánto puedes pagar sin ahogarte.")
        Btn(if (plan.configured) "Ajustar plan" else "Crear mi plan", { st.open("plan") }, outline = true)
    }
    QCard {
        Items(d.arr("debts"), "No tienes deudas registradas.") { x ->
            val amount = x.num("amount")
            val paid = x.num("paid")
            Column(Modifier.weight(1f)) {
                B(x.str("entity") ?: "Deuda")
                Mut("${x.str("description")?.let { "$it " } ?: ""}Debes ${f.orig(amount - paid, x.str("currency"))} de ${f.orig(amount, x.str("currency"))}")
                Spacer(Modifier.height(6.dp))
                Bar(if (amount > 0) paid / amount else 0.0)
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Link("Editar", { st.open("editard", x.id) })
                Btn("Abonar", { st.open("abono", x.id) }, small = true)
            }
        }
    }
    Btn("Agregar deuda", { st.open("deuda") })
}

// =====================================================================
// PAGOS
// =====================================================================

@Composable
fun Pagos(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    Head(st)
    Chips {
        Chip("Próximos pagos", st.pagosSub == "pagos") { st.pagosSub = "pagos" }
        Chip("Compromisos", st.pagosSub == "compromisos") { st.pagosSub = "compromisos" }
    }
    if (st.pagosSub == "compromisos") {
        QCard {
            Items(d.arr("schoolExpenses"), "Aquí van el colegio, el alquiler y otros pagos que repites.") { x ->
                val left = schoolLeft(d, x)
                Column(Modifier.weight(1f)) {
                    B(x.str("description") ?: "Compromiso")
                    val due = dateOf(x.str("dueDate"))
                    val who = listOfNotNull(x.str("student")?.takeIf { it.isNotBlank() }, due?.let { "vence ${shortDate(it)}" }).joinToString(" · ")
                    if (who.isNotEmpty()) Mut(who.replaceFirstChar { it.uppercase() })
                    Mut(if (left > 0) "Falta ${f.orig(left, x.str("currency"))} de ${f.orig(x.num("amount"), x.str("currency"))}" else "Pagado")
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Link("Editar", { st.open("editarc", x.id) })
                    if (left > 0) Btn("Pagar", { st.open("cpago", x.id) }, small = true)
                }
            }
        }
        Btn("Agregar compromiso", { st.open("compromiso") })
    } else {
        val ps = d.arr("plannedDebtPayments").filter { it.str("status") == "planned" }.sortedBy { it.str("scheduledDate") ?: "" }
        QCard {
            Items(ps, "No tienes pagos programados.") { pp ->
                Column(Modifier.weight(1f)) {
                    B(pp.str("entity") ?: pp.str("description") ?: "Pago")
                    val cuota = pp.str("description")?.takeIf { it.startsWith("Cuota ") }
                    dateOf(pp.str("scheduledDate"))?.let { Mut(longDate(it) + (cuota?.let { c -> " · $c" } ?: "")) }
                }
                Amt(f.orig(pp.num("amount"), pp.str("currency")))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Link("Editar", { st.open("editarp", pp.id) })
                    Btn("Ya pagué", { payPlanned(st, pp) }, small = true)
                }
            }
        }
        Btn("Programar un pago", { if (d.arr("debts").isEmpty()) st.toast("Primero agrega una deuda en Deudas.") else st.open("pago") })
        ComprometidoCard(st)
    }
}

/** "Ya pagué": marca el pago, lo descuenta de la deuda y lo registra como gasto (igual que la web). */
fun payPlanned(st: AppState, pp: JsonObject) = st.act("Pago registrado") { m ->
    val rate = rateOf(m.data())
    val usdAmt = usdOf(m.data(), pp.num("amount"), pp.str("currency"))
    m.updateRow("plannedDebtPayments", pp.id) { it.plusFields("status" to "paid") }
    m.updateRow("debts", pp.str("debtId") ?: "") { x ->
        val add = usdAmt * (if (x.str("currency") == "BS") rate else 1.0)
        x.plusFields("paid" to min(x.num("amount"), x.num("paid") + add))
    }
    m.addFirst("expenses", row(
        "id" to "${pp.id}_paid_${System.currentTimeMillis()}", "amount" to (pp["amount"] ?: 0), "currency" to (pp.str("currency") ?: "USD"),
        "category" to "deudas", "description" to "Pago a ${pp.str("entity") ?: "deuda"}", "method" to "banco", "bank" to "", "date" to nowIso(),
    ))
}

// =====================================================================
// MÁS
// =====================================================================

@Composable
fun Mas(st: AppState) {
    val ctx = LocalContext.current
    val repo = st.repo
    var lockOn by remember { mutableStateOf(repo.pref("lock") == "1") }
    var avisosOn by remember { mutableStateOf(repo.pref("avisos") == "1") }

    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri != null) runCatching {
            val text = Json { prettyPrint = true }.encodeToString(JsonElement.serializer(), repo.data)
            ctx.contentResolver.openOutputStream(uri)?.use { it.write(text.toByteArray()) }
        }.onSuccess { st.toast("Listo: tus datos se guardaron.") }.onFailure { st.toast("No pudimos guardar el archivo.") }
    }
    fun turnOnAvisos() { Reminders.enable(ctx); repo.setPref("avisos", "1"); avisosOn = true; st.toast("Listo: te avisaremos antes de cada pago.") }
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) turnOnAvisos() else st.toast("Activa las notificaciones en los ajustes del teléfono.")
    }

    Head(st)
    BolsaMasCard(st)
    DetectarCard(st)
    QCard {
        B("Mi presupuesto")
        Mut("Cuánto quieres gastar al mes, en dólares.")
        Btn("Editar presupuesto", { st.open("presupuesto") }, outline = true)
    }
    QCard {
        B("Seguridad y avisos")
        Btn(if (lockOn) "Quitar entrada con huella o rostro" else "Activar entrada con huella o rostro", {
            if (lockOn) { repo.setPref("lock", null); lockOn = false; st.toast("Listo: ya no se pedirá la huella.") }
            else {
                val can = BiometricManager.from(ctx).canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL)
                if (can == BiometricManager.BIOMETRIC_SUCCESS) { repo.setPref("lock", "1"); lockOn = true; st.toast("Listo: Quincena pedirá tu huella o rostro al abrirse.") }
                else st.toast("Configura una huella, rostro o bloqueo de pantalla en tu teléfono primero.")
            }
        }, outline = true)
        Btn(if (avisosOn) "Desactivar avisos de pago" else "Activar avisos de pago", {
            if (avisosOn) { Reminders.disable(ctx); repo.setPref("avisos", null); avisosOn = false; st.toast("Avisos desactivados.") }
            else if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                askNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
            else turnOnAvisos()
        }, outline = true)
        Mut("Te avisamos 7, 4 y 1 día antes de cada pago programado o compromiso, y el mismo día.")
    }
    QCard {
        B("Eliminar mi cuenta")
        Mut("Borra para siempre tu cuenta y todos tus datos.")
        OutlineDanger("Eliminar mi cuenta") { st.open("borrar") }
    }
    QCard {
        B("Mis datos")
        Btn("Descargar mis datos", { save.launch("quincena-mis-datos.json") }, outline = true)
        Link("Términos y privacidad", { st.open("legal") })
    }
    QCard {
        B(repo.username.ifEmpty { "Mi cuenta" })
        Btn("Cerrar sesión", {
            st.scope.launch { Reminders.disable(ctx); repo.setPref("avisos", null); repo.setPref("lock", null); st.go("inicio"); repo.logout() }
        }, outline = true)
    }
}
