package app.quincena

import android.content.Context
import android.os.Build
import kotlinx.serialization.json.*

/**
 * Datos del usuario guardados en el teléfono. Se editan sin conexión y se sincronizan con el
 * mismo protocolo de la web: se envían los datos, la copia base y la revisión, y el servidor
 * mezcla fila por fila.
 */
class Repo(ctx: Context) {
    private val store = SecureStore(ctx)
    val api = Api(store)

    private fun load(name: String): JsonObject? = store.get(name)?.let { runCatching { Json.parseToJsonElement(it).jsonObject }.getOrNull() }

    var data: JsonObject = load("data") ?: JsonObject(emptyMap())
        private set
    private var base: JsonObject = load("base") ?: data
    private var rev: Int = store.get("rev")?.toIntOrNull() ?: 0
    val loggedIn get() = api.token != null

    private fun persist() { store.put("data", data.toString()); store.put("base", base.toString()); store.put("rev", rev.toString()) }

    private fun adopt(r: JsonObject) {
        data = r["data"]!!.jsonObject; base = data; rev = r["revision"]!!.jsonPrimitive.int; persist()
    }

    suspend fun login(user: String, pass: String) {
        val r = api.call("POST", "/api/mobile/login", buildJsonObject { put("username", user); put("password", pass); put("device", Build.MODEL) })
        api.token = r["token"]!!.jsonPrimitive.content
        adopt(api.call("GET", "/api/state"))
    }

    /** Devuelve un mensaje para mostrar, o null si todo salió bien. */
    suspend fun refresh(): String? = guard { if (data == base) adopt(api.call("GET", "/api/state")) else push() }

    private suspend fun push() {
        adopt(api.call("PUT", "/api/state", buildJsonObject { put("data", data); put("base_data", base); put("base_revision", rev) }))
    }

    private suspend fun guard(block: suspend () -> Unit): String? = try { block(); null }
    catch (e: Offline) { "Sin conexión. Guardaremos tus cambios cuando vuelva internet." }
    catch (e: ApiError) {
        if (e.status == 401) api.token = null
        runCatching { adopt(api.call("GET", "/api/state")) }
        e.message
    }

    /** Cambia los datos en el teléfono (funciona sin conexión) y trata de sincronizar. */
    suspend fun edit(change: (MutableMap<String, JsonElement>) -> Unit): String? {
        data = JsonObject(data.toMutableMap().also(change)); persist()
        return guard { push() }
    }

    fun pref(k: String): String? = store.get("pref_$k")
    fun setPref(k: String, v: String?) = store.put("pref_$k", v)

    /** Crea o edita un gasto (si [id] es null, crea uno nuevo). */
    suspend fun saveExpense(id: String?, amount: Double, currency: String, category: String, description: String): String? = edit { m ->
        val rows = (m["expenses"]?.jsonArray ?: JsonArray(emptyList())).toMutableList()
        val idx = if (id == null) -1 else rows.indexOfFirst { it.jsonObject.str("id") == id }
        val old = rows.getOrNull(idx)?.jsonObject
        val row = buildJsonObject {
            if (old != null) {
                old.forEach { (k, v) -> if (k != "amount" && k != "currency" && k != "category" && k != "description") put(k, v) }
            } else {
                put("id", "q" + System.currentTimeMillis()); put("method", "efectivo"); put("bank", "")
                put("date", java.time.Instant.now().toString())
            }
            put("amount", amount); put("currency", currency); put("category", category); put("description", description)
        }
        if (idx >= 0) rows[idx] = row else rows.add(0, row)
        m["expenses"] = JsonArray(rows)
    }

    /** Elimina un gasto y devuelve la fila borrada para poder deshacer. */
    suspend fun deleteExpense(id: String): Pair<String?, JsonObject?> {
        var removed: JsonObject? = null
        val msg = edit { m ->
            val rows = (m["expenses"]?.jsonArray ?: JsonArray(emptyList())).toMutableList()
            val idx = rows.indexOfFirst { it.jsonObject.str("id") == id }
            if (idx >= 0) { removed = rows.removeAt(idx).jsonObject; m["expenses"] = JsonArray(rows) }
        }
        return msg to removed
    }

    suspend fun restoreExpense(row: JsonObject): String? = edit { m ->
        m["expenses"] = JsonArray(listOf<JsonElement>(row) + (m["expenses"]?.jsonArray ?: emptyList()))
    }

    suspend fun logout() {
        runCatching { api.call("POST", "/api/mobile/logout") }
        api.token = null
        listOf("data", "base", "rev").forEach { store.put(it, null) }
        data = JsonObject(emptyMap()); base = data; rev = 0
    }
}