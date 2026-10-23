package app.quincena

import kotlinx.serialization.json.*
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.TextStyle
import java.util.Locale
import kotlin.math.ceil
import kotlin.math.max

val CATS = linkedMapOf(
    "comida" to "Comida", "transporte" to "Transporte", "fijo" to "Gastos fijos", "hormiga" to "Gastos pequeños",
    "salud" to "Salud", "hogar" to "Hogar", "ocio" to "Ocio", "educacion" to "Educación", "deudas" to "Pagos de deudas",
)
val CAT_COLORS = mapOf(
    "comida" to 0xFF0B6B4DL, "transporte" to 0xFF2F6FDEL, "fijo" to 0xFFB25E00L, "hormiga" to 0xFFB3261EL,
    "salud" to 0xFF8E3FBFL, "hogar" to 0xFF2E8B9AL, "ocio" to 0xFFC2185BL, "educacion" to 0xFF5D6B1FL, "deudas" to 0xFF6B4EA2L,
)

fun JsonObject.num(k: String) = this[k]?.jsonPrimitive?.doubleOrNull ?: 0.0
fun JsonObject.str(k: String) = this[k]?.jsonPrimitive?.contentOrNull
fun JsonObject.arr(k: String): List<JsonObject> = this[k]?.jsonArray?.map { it.jsonObject } ?: emptyList()

/** Formato de montos: oculta, cambia de moneda y usa coma decimal, igual que la web. */
class Fmt(val hide: Boolean, val bs: Boolean, val rate: Double) {
    private fun n(v: Double) = String.format(Locale("es", "VE"), "%,.2f", v)
    fun show(usd: Double) = if (hide) "••••" else if (bs) "Bs. " + n(usd * rate) else "\$ " + n(usd)
    fun orig(a: Double, cur: String?) = if (hide) "••••" else if (cur == "BS") "Bs. " + n(a) else "\$ " + n(a)
}

/** Lee montos escritos como 12.5, 12,50 o 1.234,56. */
fun parseMoney(s: String): Double? {
    var t = s.trim().replace(" ", "")
    t = if (t.contains(',')) t.replace(".", "").replace(',', '.')
    else if (Regex("^\\d{1,3}(\\.\\d{3})+$").matches(t)) t.replace(".", "") else t
    return t.toDoubleOrNull()
}

fun inputMoney(v: Double): String = String.format(Locale("es", "VE"), "%.2f", v)

data class Period(val ym: YearMonth, val half: Int) {
    val from: LocalDate get() = ym.atDay(if (half == 2) 16 else 1)
    val to: LocalDate get() = if (half == 1) ym.atDay(15) else ym.atEndOfMonth()
    fun step(d: Int): Period {
        if (half == 0) return Period(ym.plusMonths(d.toLong()), 0)
        var h = half + d
        var m = ym
        if (h > 2) { h = 1; m = m.plusMonths(1) }
        if (h < 1) { h = 2; m = m.minusMonths(1) }
        return Period(m, h)
    }
    fun label(): String {
        val month = ym.month.getDisplayName(TextStyle.FULL, Locale("es"))
        val year = if (ym.year != LocalDate.now().year) " ${ym.year}" else ""
        return (if (half > 0) "$half.ª quincena de " else "") + month + year
    }
}

fun currentPeriod(): Period {
    val t = LocalDate.now()
    return Period(YearMonth.from(t), if (t.dayOfMonth <= 15) 1 else 2)
}

fun usdOf(d: JsonObject, a: Double, cur: String?): Double {
    val r = d.num("exchangeRate")
    return if (cur == "BS") a / (if (r > 0) r else 1.0) else a
}

fun dateOf(s: String?): LocalDate? = s?.take(10)?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
fun inRange(s: String?, from: LocalDate, to: LocalDate): Boolean {
    val x = dateOf(s) ?: return false
    return !x.isBefore(from) && !x.isAfter(to)
}

fun monthlyIncome(d: JsonObject): Double {
    val srcs = d.arr("incomeSources")
    if (srcs.isEmpty()) {
        val i = d["income"]?.jsonObject
        return usdOf(d, i?.num("amount") ?: 0.0, i?.str("currency"))
    }
    return srcs.sumOf { s ->
        val a = usdOf(d, s.num("amount"), s.str("currency"))
        when (s.str("frequency")) { "quincenal" -> a * 2; "mensual" -> a; else -> 0.0 }
    }
}

fun periodIncome(d: JsonObject, p: Period): Double {
    val srcs = d.arr("incomeSources")
    if (srcs.isEmpty()) return monthlyIncome(d) * (if (p.half == 0) 1.0 else 0.5)
    var inc = 0.0
    for (s in srcs) {
        val a = usdOf(d, s.num("amount"), s.str("currency"))
        when (s.str("frequency")) {
            "quincenal" -> inc += a * (if (p.half == 0) 2 else 1)
            "mensual" -> {
                val day = s["payDays"]?.jsonArray?.firstOrNull()?.jsonPrimitive?.intOrNull ?: 1
                if (p.half == 0 || (p.half == 1) == (day <= 15)) inc += a
            }
        }
    }
    return inc
}

fun spentIn(d: JsonObject, from: LocalDate, to: LocalDate): Double =
    d.arr("expenses").filter { inRange(it.str("date"), from, to) }.sumOf { usdOf(d, it.num("amount"), it.str("currency")) }

fun spentByCat(d: JsonObject, from: LocalDate, to: LocalDate): Map<String, Double> {
    val m = mutableMapOf<String, Double>()
    for (e in d.arr("expenses")) if (inRange(e.str("date"), from, to)) {
        val k = e.str("category") ?: "fijo"
        m[k] = (m[k] ?: 0.0) + usdOf(d, e.num("amount"), e.str("currency"))
    }
    return m
}

fun dueIn(d: JsonObject, from: LocalDate, to: LocalDate): Double {
    var due = 0.0
    for (p in d.arr("plannedDebtPayments")) if (p.str("status") == "planned" && inRange(p.str("scheduledDate"), from, to)) {
        due += usdOf(d, p.num("amount"), p.str("currency"))
    }
    val pays = d.arr("schoolPayments")
    for (x in d.arr("schoolExpenses")) if (inRange(x.str("dueDate"), from, to)) {
        val paid = pays.filter { it.str("expenseId") == x.str("id") }.sumOf { it.num("amount") }
        due += usdOf(d, max(0.0, x.num("amount") - paid), x.str("currency"))
    }
    return due
}

data class Summary(val income: Double, val spent: Double, val due: Double) { val free get() = income - spent - due }
fun summarize(d: JsonObject, p: Period) = Summary(periodIncome(d, p), spentIn(d, p.from, p.to), dueIn(d, p.from, p.to))

data class DebtTotals(val total: Double, val paid: Double) { val remaining get() = total - paid }
fun debtTotals(d: JsonObject): DebtTotals {
    val ds = d.arr("debts")
    return DebtTotals(ds.sumOf { usdOf(d, it.num("amount"), it.str("currency")) }, ds.sumOf { usdOf(d, it.num("paid"), it.str("currency")) })
}

/** Meses estimados para salir de deudas y pago mensual (cálculo simple; el simulador completo llega con la pantalla Deudas). */
fun monthsEstimate(d: JsonObject): Pair<Int?, Double> {
    val t = debtTotals(d)
    if (t.remaining <= 0.005) return null to 0.0
    val ym = YearMonth.now()
    val left = monthlyIncome(d) - spentIn(d, ym.atDay(1), ym.atEndOfMonth())
    val pct = d["debtPlan"]?.jsonObject?.num("paymentPercent")?.takeIf { it > 0 } ?: 60.0
    val pay = max(0.0, left) * pct / 100
    return (if (pay > 0) ceil(t.remaining / pay).toInt() else null) to pay
}