package app.quincena

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

// BORRADOR: revisar con un abogado antes de publicar (mismo texto de la web).
const val CONTACTO = "[escribe aquí tu correo de contacto]"
val LEGAL = listOf(
    "Qué datos guardamos" to "Tu usuario, tu correo y la información financiera que tú registras: ingresos, gastos, deudas, pagos y compromisos.",
    "Para qué los usamos" to "Solo para que Quincena funcione: mostrarte tus cuentas, enviarte avisos de pago y el correo para recuperar tu contraseña. No vendemos tus datos ni los usamos para publicidad.",
    "Quién más interviene" to "El servicio donde se aloja la app, Gmail para enviar correos y un servicio público para consultar el dólar BCV, que no recibe tus datos personales.",
    "Seguridad" to "Tu contraseña se guarda cifrada y la conexión es segura. Ninguna medida es infalible, por eso te pedimos elegir una contraseña larga y no compartirla.",
    "Tus derechos" to "Puedes descargar tus datos, corregirlos y eliminar tu cuenta cuando quieras desde Más. Al eliminarla, se borran tu cuenta y tus datos.",
    "Importante" to "Quincena es una herramienta para organizarte. No ofrece asesoría financiera, legal ni contable, y sus cálculos son estimados.",
    "Tu cuenta" to "Eres responsable de tu contraseña y de que la información que registras sea correcta.",
    "Cambios y contacto" to "Si cambiamos estos textos, te avisaremos en la app. Para consultas escríbenos a $CONTACTO.",
)

@Composable
fun LegalText() {
    LEGAL.forEach { (t, b) ->
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) { B(t); Mut(b) }
    }
}

private fun friendly(e: Exception): String = when (e) {
    is Offline -> "Sin conexión. Revisa tu internet e inténtalo de nuevo."
    is ApiError -> e.message ?: "El servidor respondió con error ${e.status}."
    else -> "Error en la app: ${e.javaClass.simpleName}${e.message?.let { " – $it" } ?: ""}"
}

/** Entrar, crear cuenta, recuperar contraseña y términos (igual que authView() en la web). */
@Composable
fun AuthScreen(st: AppState) {
    var mode by remember { mutableStateOf("login") }
    var back by remember { mutableStateOf("login") }
    var regOpen by remember { mutableStateOf(false) }
    var user by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var accept by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val p = pal

    LaunchedEffect(Unit) { regOpen = runCatching { st.repo.status().bool("registration_open") == true }.getOrDefault(false) }

    fun submit(block: suspend () -> Unit) {
        busy = true; error = null
        st.scope.launch {
            try { block() } catch (e: Exception) { error = friendly(e) }
            busy = false
        }
    }

    Column(
        Modifier.fillMaxSize().systemBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Column(Modifier.widthIn(max = 380.dp).fillMaxWidth().padding(top = 56.dp, bottom = 32.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (mode == "legal") {
                Title("Términos y privacidad", 28.sp)
                LegalText()
                Btn("Volver", { mode = back })
            } else {
            Title("Quincena", 36.sp)
            Mut(when (mode) {
                "registro" -> "Crea tu cuenta gratis."
                "olvide" -> "Te enviaremos un enlace para elegir una contraseña nueva."
                else -> "Tus finanzas, solo para ti."
            })
            when (mode) {
                "registro" -> {
                    Field("Usuario", user, { user = it })
                    Field("Correo", email, { email = it }, kind = KeyboardType.Email)
                    Field("Contraseña (mínimo 12 caracteres)", pass, { pass = it }, password = true)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(accept, { accept = it }, colors = CheckboxDefaults.colors(checkedColor = p.g))
                        Text("Acepto los términos y la política de privacidad", Modifier.weight(1f))
                    }
                }
                "olvide" -> Field("Correo", email, { email = it }, kind = KeyboardType.Email)
                else -> {
                    Field("Usuario", user, { user = it })
                    Field("Contraseña", pass, { pass = it }, password = true)
                }
            }
            error?.let { Text(it, color = p.bad, fontWeight = FontWeight.SemiBold) }
            // Render (plan gratis) se duerme: avisa si tarda, para que nadie piense que falló.
            var slow by remember { mutableStateOf(false) }
            LaunchedEffect(busy) { slow = false; if (busy) { kotlinx.coroutines.delay(4000); slow = true } }
            if (busy && slow) Mut("Conectando… la primera vez puede tardar hasta un minuto mientras el servidor despierta.")
            when (mode) {
                "registro" -> Btn("Crear mi cuenta", {
                    when {
                        user.isBlank() || email.isBlank() -> error = "Completa usuario y correo."
                        pass.length < 12 -> error = "Usa una contraseña de al menos 12 caracteres."
                        !accept -> error = "Debes aceptar los términos y la política de privacidad para crear tu cuenta."
                        else -> submit { st.repo.register(user, email, pass) }
                    }
                }, enabled = !busy)
                "olvide" -> Btn("Enviarme el enlace", {
                    if (email.isBlank()) error = "Escribe tu correo."
                    else submit {
                        st.repo.forgot(email); mode = "login"
                        st.toast("Si el correo existe, te enviamos un enlace. Ábrelo en el navegador para elegir tu contraseña.")
                    }
                }, enabled = !busy)
                else -> Btn("Entrar", { submit { st.repo.login(user, pass); pass = "" } }, enabled = !busy && user.isNotBlank() && pass.isNotEmpty())
            }
            Column(horizontalAlignment = Alignment.Start) {
                if (mode == "login") {
                    Link("¿Olvidaste tu contraseña?", { error = null; mode = "olvide" })
                    if (regOpen) Link("Crear una cuenta", { error = null; mode = "registro" })
                } else Link(if (mode == "registro") "Ya tengo cuenta" else "Volver", { error = null; mode = "login" })
                Link("Leer términos y privacidad", { back = mode; mode = "legal" })
            }
            }
        }
    }
}
