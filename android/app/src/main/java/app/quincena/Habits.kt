package app.quincena

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import kotlinx.serialization.json.*
import java.time.DayOfWeek
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.concurrent.TimeUnit

// =====================================================================
// REGISTRO EN 3 SEGUNDOS: gastos frecuentes
// =====================================================================

private val AUTO_ID = Regex("_abono$|_paid_|_school$|_ini$|^rec_")

/** Fecha local del gasto (los gastos se guardan en hora UTC). */
fun localDay(e: JsonObject): LocalDate? {
    val s = e.str("date") ?: return null
    return runCatching { Instant.parse(s).atZone(ZoneId.systemDefault()).toLocalDate() }.getOrNull() ?: dateOf(s)
}

/** Gastos que repetiste al menos 2 veces en los últimos 60 días (máx. 6). */
fun frecuentes(d: JsonObject): List<JsonObject> {
    val lim = LocalDate.now().minusDays(60)
    return d.arr("expenses")
        .filter { e -> !AUTO_ID.containsMatchIn(e.id) && (localDay(e)?.isBefore(lim) == false) }
        .groupBy { listOf((it.str("description") ?: "").trim().lowercase(), it.num("amount"), it.str("currency"), it.str("category")) }
        .filter { it.value.size >= 2 }
        .entries.sortedByDescending { it.value.size }.take(6).map { it.value.first() }
}

fun quickAdd(st: AppState, e: JsonObject) {
    val d = st.repo.data
    val id = newId()
    val label = (e.str("description")?.takeIf { it.isNotBlank() } ?: CATS[e.str("category")] ?: "Gasto")
    st.act("Registrado: $label ${Fmt(false, false, 1.0).orig(e.num("amount"), e.str("currency"))}", undo = true) { m ->
        val type = e.str("rateType") ?: "bcv"
        val rf = rateFields(d, e.str("currency") ?: "USD", type, if (type == "otra") inputMoney(e.num("rateUsed")) else "")
        m.addFirst("expenses", row(
            "id" to id, "amount" to e.num("amount"), "currency" to (e.str("currency") ?: "USD"), "category" to (e.str("category") ?: "comida"),
            "description" to (e.str("description") ?: ""), "method" to (e.str("method") ?: "efectivo"), "bank" to "", "date" to nowIso(),
        ).plusFields(*rf))
    }
}

@Composable
fun QuickRow(st: AppState) {
    val list = frecuentes(st.repo.data)
    if (list.isEmpty()) return
    val f = Fmt(false, false, 1.0)
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Mut("Rápido")
        list.forEach { e ->
            val label = (e.str("description")?.takeIf { it.isNotBlank() } ?: CATS[e.str("category")] ?: "Gasto").take(18)
            Chip("$label · ${f.orig(e.num("amount"), e.str("currency"))}", false) { quickAdd(st, e) }
        }
    }
}

// =====================================================================
// HÁBITO: resumen de la semana, recordatorio diario y resumen del domingo
// =====================================================================

data class Semana(val total: Double, val n: Int, val topCat: String?, val topPct: Int)

fun semana(d: JsonObject): Semana {
    val lim = LocalDate.now().minusDays(7)
    val ex = d.arr("expenses").filter { localDay(it)?.isAfter(lim) == true }
    val cat = mutableMapOf<String, Double>()
    var t = 0.0
    for (e in ex) { val a = usdOf(d, e.num("amount"), e.str("currency")); t += a; cat[e.str("category") ?: "fijo"] = (cat[e.str("category") ?: "fijo"] ?: 0.0) + a }
    val top = cat.maxByOrNull { it.value }
    return Semana(t, ex.size, top?.key, if (top != null && t > 0) Math.round(top.value / t * 100).toInt() else 0)
}

/** "Tu semana": se muestra en Inicio los domingos y lunes. */
@Composable
fun SemanaCard(st: AppState) {
    val dw = LocalDate.now().dayOfWeek
    if (dw != DayOfWeek.SUNDAY && dw != DayOfWeek.MONDAY) return
    val s = semana(st.repo.data)
    if (s.n == 0) return
    QCard {
        B("Tu semana")
        Mut("Gastaste ${st.fmt.show(s.total)} en ${s.n} ${if (s.n == 1) "gasto" else "gastos"}. Lo que más: ${CATS[s.topCat] ?: "Otros"} (${s.topPct} %).")
    }
}

object Habits {
    private const val WORK = "habito-diario"

    /** Programa el recordatorio todos los días a la [hour] elegida (hora del teléfono). */
    fun enable(ctx: Context, hour: Int) {
        Reminders.channel(ctx)
        val now = LocalDateTime.now()
        var next = now.toLocalDate().atTime(hour, 0)
        if (!next.isAfter(now)) next = next.plusDays(1)
        val delay = Duration.between(now, next).toMillis()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<HabitWorker>(1, TimeUnit.DAYS).setInitialDelay(delay, TimeUnit.MILLISECONDS).build())
    }

    fun disable(ctx: Context) { WorkManager.getInstance(ctx).cancelUniqueWork(WORK) }
}

/** Cada día: si hoy no registraste nada, te lo recuerda. Los domingos, además, te da el resumen de la semana. */
class HabitWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val store = SecureStore(ctx)
        if (store.get("token") == null || store.get("pref_habito") == null) return Result.success()
        val d = store.get("data")?.let { runCatching { Json.parseToJsonElement(it).jsonObject }.getOrNull() } ?: return Result.success()
        val today = LocalDate.now()
        if (today.dayOfWeek == DayOfWeek.SUNDAY) {
            val s = semana(d)
            if (s.n > 0) notify(ctx, 7001, "Tu semana en Quincena VE",
                "Gastaste ${Fmt(false, false, 1.0).orig(s.total, "USD")} en ${s.n} gastos. Lo que más: ${CATS[s.topCat] ?: "Otros"} (${s.topPct} %).")
        }
        if (d.arr("expenses").none { localDay(it) == today })
            notify(ctx, 7000, "¿Registraste tus gastos de hoy?", "Toma 10 segundos y te ayuda a saber cuánto te queda.")
        return Result.success()
    }

    @SuppressLint("MissingPermission")
    private fun notify(ctx: Context, id: Int, title: String, body: String) {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        Reminders.channel(ctx)
        val tap = PendingIntent.getActivity(ctx, id, Intent(ctx, MainActivity::class.java).setAction(ACTION_NUEVO_GASTO)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        NotificationManagerCompat.from(ctx).notify(id, NotificationCompat.Builder(ctx, Reminders.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_quincena).setContentTitle(title).setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body)).setContentIntent(tap).setAutoCancel(true).build())
    }
}

const val ACTION_NUEVO_GASTO = "app.quincena.NUEVO_GASTO"

/** Parte de la tarjeta Avisos de Más: recordatorio diario a la hora que elijas. */
@Composable
fun HabitoSection(st: AppState) {
    val ctx = LocalContext.current
    var on by remember { mutableStateOf(st.repo.pref("habito") != null) }
    var hour by remember { mutableStateOf(st.repo.pref("habito")?.toIntOrNull() ?: 20) }
    fun activate() {
        st.repo.setPref("habito", hour.toString()); Habits.enable(ctx, hour); on = true
        st.toast("Listo: te recordaremos a las ${hour}:00 si ese día no registraste gastos.")
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) activate() else st.toast("Activa las notificaciones en los ajustes del teléfono.")
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        B("Recordatorio diario")
        Mut("Solo te avisa si ese día no registraste nada. Los domingos, además, te llega el resumen de tu semana.")
        Select("Hora", listOf(18, 19, 20, 21, 22).map { it.toString() to "$it:00" }, hour.toString()) {
            hour = it.toInt()
            if (on) activate()
        }
        Btn(if (on) "Desactivar recordatorio" else "Activar recordatorio", {
            if (on) { Habits.disable(ctx); st.repo.setPref("habito", null); on = false; st.toast("Recordatorio desactivado.") }
            else if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                ask.launch(Manifest.permission.POST_NOTIFICATIONS)
            else activate()
        }, outline = true)
    }
}
