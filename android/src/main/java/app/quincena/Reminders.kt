package app.quincena

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.util.concurrent.TimeUnit

/**
 * Avisos de pago 7, 4 y 1 día antes y el mismo día (como los recordatorios push de la web).
 * Los calcula el propio teléfono una vez al día; no necesita claves VAPID ni el cron de Render.
 */
object Reminders {
    const val CHANNEL = "pagos"
    private const val WORK = "recordatorios"

    fun enable(ctx: Context) {
        channel(ctx)
        val wm = WorkManager.getInstance(ctx)
        val net = Constraints.Builder().build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<ReminderWorker>(1, TimeUnit.DAYS).setConstraints(net).build())
        wm.enqueue(OneTimeWorkRequestBuilder<ReminderWorker>().build())
    }

    fun disable(ctx: Context) { WorkManager.getInstance(ctx).cancelUniqueWork(WORK) }

    fun channel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Avisos de pago", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }
}

class ReminderWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val store = SecureStore(ctx)
        if (store.get("token") == null) return Result.success()
        // Intenta leer lo último del servidor (solo lectura); si no hay internet usa lo guardado en el teléfono.
        val d: JsonObject = runCatching { Api(store).call("GET", "/api/state")["data"]!!.jsonObject }.getOrNull()
            ?: store.get("data")?.let { runCatching { Json.parseToJsonElement(it).jsonObject }.getOrNull() }
            ?: return Result.success()

        data class Due(val key: String, val title: String, val amount: String, val days: Int)
        val f = Fmt(false, false, rateOf(d))
        val items = mutableListOf<Due>()
        for (p in d.arr("plannedDebtPayments")) if (p.str("status") == "planned") {
            val date = dateOf(p.str("scheduledDate")) ?: continue
            items += Due("debt_${p.id}_$date", p.str("entity") ?: p.str("description") ?: "Pago de deuda", f.orig(p.num("amount"), p.str("currency")), daysUntil(date))
        }
        for (x in d.arr("schoolExpenses")) {
            val date = dateOf(x.str("dueDate")) ?: continue
            val left = schoolLeft(d, x)
            if (left <= 0.005) continue
            items += Due("school_${x.id}_$date", x.str("description") ?: "Compromiso", f.orig(left, x.str("currency")), daysUntil(date))
        }

        val sent = ctx.getSharedPreferences("quincena_avisos", Context.MODE_PRIVATE)
        for (it in items) {
            if (it.days !in 0..7) continue
            val bucket = listOf(0, 1, 4, 7).first { b -> b >= it.days }
            val key = "${it.key}_$bucket"
            if (sent.getBoolean(key, false)) continue
            val body = when (it.days) { 0 -> "Vence hoy: ${it.amount}"; 1 -> "Vence mañana: ${it.amount}"; else -> "Vence en ${it.days} días: ${it.amount}" }
            if (notify(ctx, key.hashCode(), it.title, body)) sent.edit().putBoolean(key, true).apply()
        }
        return Result.success()
    }

    @SuppressLint("MissingPermission")
    private fun notify(ctx: Context, id: Int, title: String, body: String): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        Reminders.channel(ctx)
        val tap = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(ctx, Reminders.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_quincena)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(ctx).notify(id, n)
        return true
    }
}
