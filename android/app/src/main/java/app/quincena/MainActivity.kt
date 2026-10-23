package app.quincena

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.util.Locale
import kotlin.math.abs

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val repo = Repo(applicationContext)
        setContent { QuincenaTheme { App(repo) } }
    }
}

@Composable
fun QuincenaTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme()) {
        darkColorScheme(primary = Color(0xFF4FC79C), onPrimary = Color(0xFF0E1512), background = Color(0xFF0E1512), surface = Color(0xFF16211C), error = Color(0xFFFF8A80))
    } else {
        lightColorScheme(primary = Color(0xFF0B6B4D), onPrimary = Color.White, background = Color(0xFFF3F6F2), surface = Color.White, error = Color(0xFFB3261E))
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

fun money(v: Double): String = String.format(Locale("es", "VE"), "\$ %,.2f", v)

/** Lee montos escritos como 12.5, 12,50 o 1.234,56. */
fun parseMoney(s: String): Double? {
    var t = s.trim().replace(" ", "")
    t = if (t.contains(',')) t.replace(".", "").replace(',', '.')
    else if (Regex("^\\d{1,3}(\\.\\d{3})+$").matches(t)) t.replace(".", "") else t
    return t.toDoubleOrNull()
}

@Composable
fun App(repo: Repo) {
    var logged by remember { mutableStateOf(repo.loggedIn) }
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        if (logged) Inicio(repo) { logged = false } else Login(repo) { logged = true }
    }
}

@Composable
fun Login(repo: Repo, onOk: () -> Unit) {
    val scope = rememberCoroutineScope()
    var user by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp, androidx.compose.ui.Alignment.CenterVertically)) {
        Text("Quincena", fontSize = 36.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
        Text("Tus finanzas, solo para ti.")
        OutlinedTextField(user, { user = it }, label = { Text("Usuario") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(pass, { pass = it }, label = { Text("Contraseña") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(enabled = !busy && user.isNotBlank() && pass.isNotEmpty(), modifier = Modifier.fillMaxWidth().height(48.dp), onClick = {
            busy = true; error = null
            scope.launch {
                try { repo.login(user, pass); onOk() }
                catch (e: ApiError) { error = e.message }
                catch (e: Offline) { error = "Sin conexión. Revisa tu internet e inténtalo de nuevo." }
                busy = false
            }
        }) { Text("Entrar") }
    }
}

@Composable
fun Inicio(repo: Repo, onLogout: () -> Unit) {
    val scope = rememberCoroutineScope()
    var tick by remember { mutableIntStateOf(0) }
    var msg by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { msg = repo.refresh(); if (!repo.loggedIn) onLogout() else tick++ }
    val s = remember(tick) { summarize(repo.data) }

    Scaffold(containerColor = Color.Transparent, floatingActionButton = {
        FloatingActionButton(onClick = { adding = true }) { Text("+", fontSize = 28.sp) }
    }) { pad ->
        Column(Modifier.padding(pad).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Quincena", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
                TextButton(onClick = { scope.launch { repo.logout(); onLogout() } }) { Text("Cerrar sesión") }
            }
            Card(shape = RoundedCornerShape(24.dp), colors = CardDefaults.cardColors(containerColor = if (s.free < 0) Color(0xFF9C2B24) else MaterialTheme.colorScheme.primary)) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    val c = if (s.free < 0) Color.White else MaterialTheme.colorScheme.onPrimary
                    Text(if (s.free < 0) "Te faltan esta quincena" else "Te quedan esta quincena", color = c)
                    Text(money(abs(s.free)), fontSize = 44.sp, fontWeight = FontWeight.Bold, color = c)
                    Text("Gastado ${money(s.spent)} · Ingresos ${money(s.income)}", color = c)
                    if (s.due > 0) Text("Ya descontado: ${money(s.due)} en pagos de este periodo", color = c)
                }
            }
            msg?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }

    if (adding) AddExpense(onDismiss = { adding = false }) { amount, note ->
        adding = false
        scope.launch { msg = repo.addExpense(amount, "USD", "comida", note); tick++ }
    }
}

@Composable
fun AddExpense(onDismiss: () -> Unit, onSave: (Double, String) -> Unit) {
    var amount by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    val value = parseMoney(amount)
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Nuevo gasto") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(amount, { amount = it }, label = { Text("Monto en dólares") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(note, { note = it }, label = { Text("Nota (opcional)") }, singleLine = true)
            }
        },
        confirmButton = { Button(enabled = value != null && value > 0, onClick = { onSave(value!!, note) }) { Text("Guardar") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } })
}
