package app.quincena

import android.app.Notification
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.NotificationManagerCompat
import kotlinx.serialization.json.*
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.TextStyle
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.floor
import kotlin.math.min
import kotlin.math.roundToLong

// =====================================================================
// SUELDO COMPROMETIDO
// =====================================================================

data class Qna(val p: Period, val inc: Double, val com: Double) {
    val pct: Int get() = if (inc > 0) (com / inc * 100).roundToLong().toInt() else 0
}

fun qnaLabel(p: Period) = "${p.half}.ª qna. de ${p.ym.month.getDisplayName(TextStyle.SHORT, Locale("es")).trimEnd('.')}"

/** Lo ya comprometido en una quincena: pagos programados, compromisos pendientes y gastos fijos. */
fun committedIn(d: JsonObject, p: Period): Double {
    var c = dueIn(d, p.from, p.to)
    val last = p.ym.lengthOfMonth()
    for (r in d.arr("recurring")) {
        if (r.bool("active") == false) continue
        val day = min(r.num("day").toInt().coerceAtLeast(1), last)
        if ((p.half == 1) == (day <= 15)) c += usdOf(d, r.num("amount"), r.str("currency"))
    }
    return c
}

fun comprometido(d: JsonObject): List<Qna> =
    generateSequence(currentPeriod()) { it.step(1) }.take(4).map { Qna(it, periodIncome(d, it), committedIn(d, it)) }.toList()

@Composable
private fun pctColor(p: Int): Color = if (p >= 70) pal.bad else if (p >= 40) pal.warn else pal.g

@Composable
fun ComprometidoCard(st: AppState) {
    val d = st.repo.data
    val f = st.fmt
    val qs = comprometido(d)
    QCard {
        B("Tu sueldo comprometido")
        Mut("Lo que ya debes pagar de tus próximas quincenas: cuotas, pagos programados, compromisos y gastos fijos.")
        qs.forEach { q ->
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(qnaLabel(q.p))
                    Mut(f.show(q.com) + if (q.inc > 0) " de ${f.show(q.inc)} · ${q.pct} %" else "")
                }
                Bar(q.pct / 100.0, color = pctColor(q.pct))
            }
        }
        if (qs.all { it.inc <= 0 }) Mut("Agrega tus ingresos para ver qué parte de cada quincena ya está comprometida.")
        Btn("Registrar compra en cuotas", { st.open("cuotas") }, outline = true)
    }
}

@Composable
fun ComprometidoMini(st: AppState) {
    val qs = comprometido(st.repo.data)
    val inc = qs.sumOf { it.inc }
    val com = qs.sumOf { it.com }
    if (com <= 0 || inc <= 0) return
    val pct = (com / inc * 100).roundToLong().toInt()
    QCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) {
                B("Tu sueldo comprometido")
                Rich("De tus próximas 4 quincenas, el ", "$pct %" to true, " ya tiene dueño: ${st.fmt.show(com)}.")
            }
            Btn("Ver", { st.go("pagos") }, small = true, outline = true)
        }
    }
}

// =====================================================================
// COMPRA EN CUOTAS (Cashea y similares)
// =====================================================================

/** Fechas de las cuotas: cada 14 días, cada quincena (15 y fin de mes) o cada mes. */
fun cuotaDates(first: LocalDate, n: Int, freq: String): List<LocalDate> {
    val out = mutableListOf(first)
    while (out.size < n) {
        val x = out.last()
        out += when (freq) {
            "q" -> if (x.dayOfMonth <= 15) YearMonth.from(x).atEndOfMonth() else YearMonth.from(x).plusMonths(1).atDay(15)
            "m" -> first.plusMonths(out.size.toLong())
            else -> x.plusDays(14)
        }
    }
    return out
}

@Composable
fun CuotasForm(st: AppState) {
    var entity by remember { mutableStateOf("Cashea") }
    var desc by remember { mutableStateOf("") }
    var amount by remember { mutableStateOf("") }
    var cur by remember { mutableStateOf("USD") }
    var initial by remember { mutableStateOf("") }
    var n by remember { mutableStateOf("3") }
    var freq by remember { mutableStateOf("14") }
    var first by remember { mutableStateOf(LocalDate.now().plusDays(14).toString()) }
    Title("Compra en cuotas", 28.sp, pal.ink)
    Field("¿Dónde la hiciste?", entity, { entity = it })
    Field("¿Qué compraste? (opcional)", desc, { desc = it }, placeholder = "Nevera, teléfono…")
    MoneyField("Precio total", amount) { amount = it }
    CurrencySelect(cur) { cur = it }
    MoneyField("Inicial que ya pagaste (opcional)", initial) { initial = it }
    Field("Número de cuotas", n, { n = it.filter(Char::isDigit).take(2) }, kind = KeyboardType.Number)
    Select("Cada cuánto pagas", listOf("14" to "Cada 14 días", "q" to "Cada quincena", "m" to "Cada mes"), freq) { freq = it }
    DateField("Fecha de la primera cuota", first, { first = it })
    Mut("Las cuotas se agregan a tus pagos programados y te avisamos antes de cada una.")
    Btn("Guardar", {
        val total = parseMoney(amount)?.takeIf { it > 0 } ?: return@Btn st.toast("Escribe un monto mayor a cero.")
        val ini = parseMoney(initial) ?: 0.0
        val count = n.toIntOrNull() ?: 0
        val firstDay = dateOf(first)
        when {
            ini < 0 || ini >= total -> return@Btn st.toast("La inicial debe ser menor que el precio total.")
            count !in 1..48 -> return@Btn st.toast("Escribe entre 1 y 48 cuotas.")
            firstDay == null -> return@Btn st.toast("Elige la fecha de la primera cuota.")
        }
        val id = newId()
        val rest = total - ini
        val q = floor(rest / count * 100) / 100
        val ent = entity.trim().ifEmpty { "Cuotas" }
        val perMonth = when (freq) { "14" -> 26.0 / 12; "q" -> 2.0; else -> 1.0 }
        val dates = cuotaDates(firstDay!!, count, freq)
        st.close()
        st.act("Guardado") { m ->
            m.addFirst("debts", row(
                "id" to id, "type" to (if (ent.contains("cashea", true)) "cashea" else "otro"), "amount" to total, "currency" to cur,
                "entity" to ent, "description" to desc.trim().ifEmpty { "Compra en $count cuotas" }, "paid" to ini, "date" to nowIso(),
                "minPayment" to Math.round(q * perMonth * 100) / 100.0, "interestKnown" to false, "installments" to count,
            ))
            val planned = m.rows("plannedDebtPayments")
            dates.forEachIndexed { i, day ->
                val a = if (i == count - 1) Math.round((rest - q * (count - 1)) * 100) / 100.0 else q
                planned += row("id" to "${id}_c${i + 1}", "debtId" to id, "entity" to ent,
                    "description" to "Cuota ${i + 1} de $count" + (if (desc.isNotBlank()) " · ${desc.trim()}" else ""),
                    "amount" to a, "currency" to cur, "scheduleType" to "date", "scheduledDate" to day.toString(), "status" to "planned")
            }
            m.setRows("plannedDebtPayments", planned)
            if (ini > 0) m.addFirst("expenses", row("id" to "${id}_ini", "amount" to ini, "currency" to cur, "category" to "deudas",
                "description" to "Inicial: ${desc.trim().ifEmpty { ent }}", "method" to "banco", "bank" to "", "date" to nowIso()))
        }
    })
    Btn("Cancelar", { st.close() }, outline = true)
}

// =====================================================================
// CALCULADORA DE CAJA
// =====================================================================

@Composable
fun CajaRow(st: AppState) {
    QCard {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) { B("¿Pagar en $ o en Bs.?"); Mut("Compara el costo real antes de pagar.") }
            Btn("Calcular", { st.open("caja") }, small = true)
        }
    }
}

@Composable
fun CajaSheet(st: AppState) {
    val d = st.repo.data
    val b = rateOf(d)
    val m = marketRateOf(d)
    val f = Fmt(false, false, 1.0)
    var price by remember { mutableStateOf("") }
    var cur by remember { mutableStateOf("USD") }
    var store by remember { mutableStateOf("") }
    Title("Calculadora de caja", 28.sp, pal.ink)
    Mut("Escribe el precio y te decimos cuánto te cuesta de verdad cada forma de pago.")
    MoneyField("Precio", price) { price = it }
    Select("El precio está en", CURRENCIES, cur) { cur = it }
    Field("Tasa de la tienda (Bs. por dólar)", store, { store = it }, placeholder = "${f.plain(b)} (BCV)", kind = KeyboardType.Decimal)
    val p = parseMoney(price)?.takeIf { it > 0 }
    if (p == null || b <= 0) Mut("Escribe un precio para comparar.")
    else {
        val t = parseMoney(store)?.takeIf { it > 0 } ?: b
        val ops = mutableListOf<Triple<String, Double, String>>()
        if (cur == "USD") {
            val bs = p * t
            ops += Triple("Pagar en dólares", p, "$ " + f.plain(p))
            ops += Triple("Pagar en Bs. si cambiaste a BCV", bs / b, "Bs. " + f.plain(bs))
            if (m > 0) ops += Triple("Pagar en Bs. si cambiaste a USDT", bs / m, "Bs. " + f.plain(bs))
        } else {
            ops += Triple("Con bolívares cambiados a BCV", p / b, "Bs. " + f.plain(p))
            if (m > 0) ops += Triple("Con bolívares cambiados a USDT", p / m, "Bs. " + f.plain(p))
            if (t != b) ops += Triple("Pagar en dólares a la tasa de la tienda", p / t, "$ " + f.plain(p / t))
        }
        val best = ops.minOf { it.second }
        QCard {
            Items(ops, "") { o ->
                Column(Modifier.weight(1f)) { B(o.first); Mut("Pagas ${o.third}") }
                Column(horizontalAlignment = Alignment.End) {
                    Amt("$ " + f.plain(o.second))
                    if (o.second - best < .005) Pill("Te conviene", pal.gs, pal.g) else Mut("+$ " + f.plain(o.second - best))
                }
            }
        }
        Mut("Costo real en dólares según la tasa a la que conseguiste tus bolívares.")
    }
    Btn("Cerrar", { st.close() }, outline = true)
}

// =====================================================================
// PODER DE COMPRA
// =====================================================================

/** Tasa BCV de referencia: el registro más viejo de los últimos 30 días (sin contar hoy). */
fun rateRef(d: JsonObject): Pair<LocalDate, Double>? {
    val today = LocalDate.now()
    val lim = today.minusDays(30)
    return d.arr("rateHistory").mapNotNull { h -> dateOf(h.str("d"))?.let { it to h.num("b") } }
        .filter { (day, b) -> b > 0 && !day.isBefore(lim) && day.isBefore(today) }.minByOrNull { it.first }
}

@Composable
fun PoderCard(st: AppState) {
    val d = st.repo.data
    val sv = d.num("bsSavings")
    val ref = rateRef(d)
    if (sv <= 0 && ref == null) return
    val b = rateOf(d)
    val f = st.fmt
    QCard {
        B("Poder de compra de tus bolívares")
        if (ref == null || b <= 0) Mut("Estamos guardando el dólar de cada día. En unos días verás cuánto valor pierden tus bolívares.")
        else {
            val days = ChronoUnit.DAYS.between(ref.first, LocalDate.now()).toInt()
            val pct = (b / ref.second - 1) * 100
            Text("En $days ${if (days == 1) "día" else "días"} el dólar BCV pasó de Bs. ${f.plain(ref.second)} a Bs. ${f.plain(b)} (${if (pct >= 0) "+" else ""}${f.plain(pct)} %).")
            if (sv > 0) {
                val before = sv / ref.second
                val now = sv / b
                Column {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("Tus Bs. ${if (st.hide) "••••" else f.plain(sv)} valían"); Amt(f.orig(before, "USD")) }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("Hoy valen"); Amt(f.orig(now, "USD")) }
                }
                if (before - now > .005) Pill("Perdieron ${f.orig(before - now, "USD")} de valor", pal.bad.copy(alpha = .15f), pal.bad)
            }
        }
        Text("Solo es información para que decidas tú; no es una recomendación financiera.", fontSize = 12.sp, color = pal.mut)
        Btn(if (sv > 0) "Actualizar mis bolívares" else "¿Cuántos bolívares tienes?", { st.open("bolsa") }, small = true, outline = true, full = true)
    }
}

@Composable
fun BolsaForm(st: AppState) {
    var amount by remember { mutableStateOf(inputMoney(st.repo.data.num("bsSavings"))) }
    Title("Mis bolívares", 28.sp, pal.ink)
    Mut("¿Cuántos bolívares tienes hoy entre cuentas y efectivo? Solo se usa para mostrarte cuánto valor pierden. Puedes dejarlo en 0.")
    MoneyField("Bolívares", amount) { amount = it }
    Btn("Guardar", {
        val v = maxOf(0.0, parseMoney(amount) ?: 0.0)
        st.close(); st.act("Guardado") { it["bsSavings"] = JsonPrimitive(v) }
    })
    Btn("Cancelar", { st.close() }, outline = true)
}

/** Agrega (o actualiza) el registro de tasas de hoy. Máximo 120 días. */
fun addRateHistory(m: MutableMap<String, JsonElement>, bcv: Double, market: Double?) {
    val today = LocalDate.now().toString()
    val h = m.rows("rateHistory").filter { it.str("d") != today }.toMutableList()
    h += row("d" to today, "b" to bcv, "m" to (market ?: 0.0))
    m.setRows("rateHistory", h.sortedBy { it.str("d") }.takeLast(120))
}

// =====================================================================
// GASTOS DETECTADOS EN LAS NOTIFICACIONES DEL BANCO (solo Android)
// =====================================================================

data class Det(val id: String, val amount: Double, val currency: String, val note: String, val time: Long)

/** Lee el texto de una notificación y decide si es un pago que hiciste (no uno que recibiste). */
object PaymentParser {
    private val outgoing = Regex("(?i)(pago\\s*m[oó]vil|pagomovil|pagaste|pago\\s+(realizado|exitoso|enviado)|compra|consumo|d[eé]bito|transferencia\\s+(enviada|realizada|exitosa)|enviaste|retiro|cargo)")
    private val incoming = Regex("(?i)(recibiste|has\\s+recibido|te\\s+(envi|pag|transfiri|abon)|abono\\s+a\\s+tu|cr[eé]dito\\s+a|dep[oó]sito|reintegro|devoluci[oó]n|c[oó]digo|clave|contrase|otp|token|verificaci)")
    private val bs = Regex("(?i)(?:bs\\.?\\s*s?\\.?|ves)\\s*([0-9][0-9.,]*)")
    private val usd = Regex("(?i)(?:\\$|usd)\\s*([0-9][0-9.,]*)")
    private val merchant = Regex("(?:\\ba|\\ben|\\bpara)\\s+([A-ZÁÉÍÓÚÑ0-9][\\wÁÉÍÓÚÑáéíóúñ.&' -]{2,28})")

    fun parse(text: String): Det? {
        if (!outgoing.containsMatchIn(text) || incoming.containsMatchIn(text)) return null
        val mBs = bs.find(text)
        val (cur, raw) = if (mBs != null) "BS" to mBs.groupValues[1] else (usd.find(text)?.let { "USD" to it.groupValues[1] } ?: return null)
        val amount = parseMoney(raw.trimEnd('.', ',')) ?: return null
        if (amount <= 0 || amount > 1e10) return null
        val who = merchant.find(text)?.groupValues?.get(1)
            ?.replace(Regex("(?i)\\s+(ref|referencia|nro|n°|operaci[oó]n|el|por|con)\\b.*$"), "")?.trim()?.trimEnd('.', ',')
            ?.takeIf { it.length >= 3 }
        return Det("", amount, cur, who?.let { "Pago a $it" } ?: "Pago detectado", 0)
    }
}

/** Gastos detectados que esperan confirmación. Se guardan cifrados solo en el teléfono. */
object Detected {
    private const val KEY = "detectados"
    fun load(ctx: Context): List<Det> = runCatching {
        Json.parseToJsonElement(SecureStore(ctx).get(KEY) ?: "[]").jsonArray.map { it.jsonObject }.map {
            Det(it.str("id") ?: "", it.num("amount"), it.str("currency") ?: "BS", it.str("note") ?: "", it.num("time").toLong())
        }
    }.getOrDefault(emptyList())

    private fun save(ctx: Context, list: List<Det>) = SecureStore(ctx).put(KEY, JsonArray(list.takeLast(30).map {
        row("id" to it.id, "amount" to it.amount, "currency" to it.currency, "note" to it.note, "time" to it.time)
    }).toString())

    @Synchronized fun add(ctx: Context, d: Det) { val l = load(ctx); if (l.none { it.id == d.id }) save(ctx, l + d) }
    @Synchronized fun remove(ctx: Context, id: String) = save(ctx, load(ctx).filter { it.id != id })
    fun enabled(ctx: Context) = NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName)
}

class PaymentListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName == packageName) return
        val repoOn = SecureStore(applicationContext).get("pref_detectar") == "1"
        if (!repoOn) return
        val ex = sbn.notification?.extras ?: return
        val text = listOfNotNull(
            ex.getCharSequence(Notification.EXTRA_TITLE),
            ex.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: ex.getCharSequence(Notification.EXTRA_TEXT),
        ).joinToString(" · ").take(400)
        val d = PaymentParser.parse(text) ?: return
        Detected.add(applicationContext, d.copy(id = "n" + (sbn.key + sbn.postTime).hashCode().toUInt().toString(), time = sbn.postTime))
    }
}

@Composable
fun DetectadosCard(st: AppState) {
    val list = st.detected
    if (list.isEmpty()) return
    val ctx = LocalContext.current
    val f = Fmt(false, false, 1.0)
    QCard(bg = pal.gs) {
        B("Gastos detectados (${list.size})")
        Mut("Los encontramos en las notificaciones de tu banco. Confírmalos para registrarlos.")
        Items(list.sortedByDescending { it.time }.take(5), "") { x ->
            Column(Modifier.weight(1f)) {
                Amt(f.orig(x.amount, x.currency))
                Mut(x.note, maxLines = 1)
            }
            Link("Descartar", { Detected.remove(ctx, x.id); st.detected = Detected.load(ctx) }, color = pal.mut)
            Btn("Registrar", { st.open("gasto", "det:" + x.id) }, small = true)
        }
    }
}

@Composable
fun DetectarCard(st: AppState) {
    val ctx = LocalContext.current
    val on = st.repo.pref("detectar") == "1" && st.listenerOn
    QCard {
        B("Registrar pagos automáticamente")
        Mut("Quincena lee las notificaciones de tu banco y de pago móvil, y te propone el gasto ya escrito. Nada se guarda ni se envía hasta que tú lo confirmas.")
        Btn(if (on) "Desactivar" else "Activar", {
            if (on) {
                st.repo.setPref("detectar", null); st.listenerOn = false
                st.toast("Listo. Para quitar el permiso por completo, desactívalo también en Ajustes.")
                ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            } else {
                st.repo.setPref("detectar", "1")
                if (Detected.enabled(ctx)) { st.listenerOn = true; st.toast("Listo: te avisaremos cuando detectemos un pago.") }
                else {
                    st.toast("Busca Quincena en la lista y permítele leer notificaciones.")
                    ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                }
            }
        }, outline = true)
        if (st.repo.pref("detectar") == "1" && !st.listenerOn) Text("Falta darle el permiso en Ajustes.", color = pal.warn, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
    }
}

@Composable
fun BolsaMasCard(st: AppState) {
    val sv = st.repo.data.num("bsSavings")
    QCard {
        B("Mis bolívares guardados")
        Mut("Dinos cuántos bolívares tienes y te mostramos cuánto valor pierden con el tiempo.")
        Btn(if (sv > 0) "Actualizar: Bs. ${if (st.hide) "••••" else Fmt(false, false, 1.0).plain(sv)}" else "Agregar mis bolívares", { st.open("bolsa") }, outline = true)
    }
}
