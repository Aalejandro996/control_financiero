package app.quincena

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

class ApiError(message: String, val status: Int) : Exception(message)
class Offline : Exception()

/** Cliente del servidor actual: mismas rutas que la web, con token en vez de cookie. */
class Api(private val store: SecureStore) {
    private val http = OkHttpClient()
    private val jsonType = "application/json".toMediaType()

    var token: String? = store.get("token")
        set(value) { field = value; store.put("token", value) }

    suspend fun call(method: String, path: String, body: JsonObject? = null): JsonObject = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(BuildConfig.API_BASE + path)
            .method(method, if (method == "GET") null else (body ?: buildJsonObject { }).toString().toRequestBody(jsonType))
            .apply { token?.let { header("Authorization", "Bearer $it") } }
            .build()
        val res = try { http.newCall(req).execute() } catch (e: IOException) { throw Offline() }
        res.use {
            val parsed = runCatching { Json.parseToJsonElement(it.body?.string().orEmpty()).jsonObject }.getOrDefault(JsonObject(emptyMap()))
            if (!it.isSuccessful) {
                throw ApiError(parsed["error"]?.jsonPrimitive?.contentOrNull ?: "Algo salió mal. Inténtalo en unos minutos.", it.code)
            }
            parsed
        }
    }
}
