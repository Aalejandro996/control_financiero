# Historial de versiones — Quincena

Cada cambio se registra aquí. La versión de Android sube en `android/app/build.gradle.kts`
(o sola, cuando la compila GitHub Actions: `versionCode` = 100 + número de ejecución).
La web no tiene número propio: cada entrada indica qué cambió en la web y en la app.

Formato: **Agregado** (nuevo), **Cambiado** (mejoras), **Corregido** (errores), **Quitado**.

---

## [Sin publicar · países y cobro semanal] — 25 oct 2026

### Agregado
- **Web y Android — País y moneda:** al crear la cuenta se elige el país (Venezuela, Colombia, Chile, Perú, México o
  Argentina; se sugiere uno según la zona horaria). La app usa la moneda local de ese país (Bs., pesos, soles) y su tipo
  de cambio oficial, que se actualiza solo. Se puede cambiar en Más › País y moneda.
- **Web y Android — Ingreso semanal:** nueva frecuencia "Cada semana" con el día en que cobras. El ingreso de cada
  quincena cuenta los cobros que realmente caen en esas fechas y el calendario marca cada uno.
- **Servidor:** `/api/countries` y `/api/rate?country=XX` (Venezuela y Argentina por dolarapi, el resto por
  open.er-api.com). Pruebas nuevas: `tests/country_test.py` y `tests/country_ui_test.py`.

### Corregido
- **Web:** el botón de moneda del encabezado se partía en dos líneas con "COP $"; ahora muestra el código corto (COP, CLP, S/, MXN, ARS). Los números largos en los mosaicos de Inicio ya no se cortan con "…" y las filas de listas se acomodan mejor en pantallas angostas.

### Cambiado
- Los textos de Venezuela (Bs., bolívares, BCV) se adaptan al país elegido; en pesos colombianos y chilenos los montos
  salen sin decimales. Las cuentas existentes siguen en Venezuela y no cambia nada para ellas.
- Las funciones de tasa paralela (USDT) y la brecha solo aparecen en Venezuela.

### Pendiente
- Los términos y la privacidad siguen citando las leyes de Venezuela: conviene una revisión legal antes de abrir otros países.

## [Sin publicar · seguridad del servidor] — 25 oct 2026

Solo servidor y web. **No hay cambios en la app Android** (no hace falta compilar otro APK).

### Agregado
- **Recuperación de clave por correo:** enlace de un solo uso que vence, el mismo mensaje exista o no el correo
  (no se puede averiguar quién tiene cuenta) y máximo 3 correos por hora por dirección.
- **Avisos de seguridad por correo** (solo a correos verificados): cambio o restablecimiento de clave, cierre de
  sesiones en otros dispositivos y inicio de sesión desde un teléfono nuevo.
- **Envío por Brevo (HTTPS):** el plan gratis de Render bloquea SMTP, así que los correos salen por la API de Brevo.
  Se configura con `BREVO_API_KEY` y `MAIL_FROM`.
- **Límite general de uso** de la API y límite de intentos de acceso **por usuario**, además del límite por IP.
- Pruebas automáticas: `tests/security_test.py` (inyección SQL, sesiones, IDOR, CSRF, límites, correos) y
  `tests/xss_test.py` (navegador real).

### Cambiado
- Los enlaces de verificación y de recuperación viajan en el fragmento `#` de la URL, que no llega a los registros
  del servidor ni a otros sitios por la cabecera Referer.
- Al cambiar o restablecer la clave se cierran las demás sesiones web (una vez tras este despliegue, todos volverán a
  iniciar sesión) y, al restablecerla un administrador, también las de la app.
- Comparación de claves con tiempo parecido exista o no el usuario; claves de más de 1000 caracteres se rechazan.
- Los errores 413, 429, 404, 405 y 500 devuelven mensajes genéricos en JSON; nunca detalles internos.
- La dirección IP del registro de auditoría sale de la conexión real validada.
- `.gitignore` ahora excluye `.env`, bases de datos, `instance/`, llaves y `android/app/release/`.

## [1.6.0] — 25 oct 2026 · app 1.9 (versionCode 105, compilada por GitHub)

### Agregado
- **Web y Android — Mis cuentas:** banco, efectivo, digital (Zelle…) y cripto (USDT). Cada gasto, ingreso o préstamo
  puede salir o entrar de una cuenta; el saldo se calcula solo. Se ve en Movimientos › Cuentas, en Inicio ("Tu dinero")
  y en Más.
- **Web y Android — Mover o cambiar dinero** entre cuentas. Si cambias $ por Bs., queda guardada **"Tu cambio"**
  (tasa de los últimos 45 días) y aparece como opción de tasa al registrar un gasto en bolívares.
- **Web y Android:** los **bolívares guardados** se calculan solos con tus cuentas en Bs. (si no tienes, sigue el monto manual).
- **Web y Android — Ingresos recibidos:** botón **"Me pagaron"** en cada ingreso fijo e **"Ingreso extra"**.
  Los extras suman al "Te quedan" del periodo.
- **Web y Android — Movimientos:** gastos **agrupados por día** (Hoy, Ayer…) con total diario, filtros por categoría
  y total del filtro.
- **Web y Android — Presupuesto por quincena:** en Inicio el presupuesto se muestra por quincena (la mitad del mensual)
  y el título dice "Tu quincena". Botón **"Usar los sugeridos"** con el promedio de los últimos 3 meses.
- **Web y Android — Calendario de pagos** en Pagos (pagos, compromisos, gastos fijos y cobros por día).
- **Web y Android — Pagos repetidos:** al programar un pago se puede repetir cada mes o cada quincena (hasta 36 veces).
- **Web y Android — Me deben:** préstamos a familiares o amigos, con "Me pagó" (abonos parciales) y fecha límite.
  Tarjeta "Te deben" en Inicio.
- **Web y Android — Fecha estimada para salir de cada deuda** ("terminas aprox. en dic 2027").
- **Seguridad:** cambiar contraseña desde la app y la web; "Cerrar sesión en mis otros teléfonos";
  el servidor cierra la sesión web tras 30 minutos sin uso; las contraseñas comunes o con tu usuario se rechazan.
- **Legal:** páginas `/terminos`, `/privacidad` y `/cookies` (con derechos según los arts. 28 y 60 de la Constitución),
  aceptación de los términos al entrar (se registra en el historial de seguridad), aviso de **cookies** en la web
  y **Copyright** en Más, entrada y menú lateral.

### Cambiado
- **Servidor:** la cookie de sesión ahora se llama `__Host-quincena` (SameSite=Strict). **Todos tendrán que
  entrar de nuevo una vez.**
- **Servidor:** encabezado `Cross-Origin-Resource-Policy`; límite de 120 guardados por minuto; los datos nuevos
  (cuentas, movimientos entre cuentas, ingresos, préstamos) se validan y se mezclan sin perder cambios.
- **Web:** caché del service worker v14.
- **Pruebas:** la prueba de humo usa contraseñas que cumplen la nueva regla.

### Pendiente por tu parte
- Definir la variable `CONTACT_EMAIL` en Render (correo que aparece en la política de privacidad).
- Revisar los textos legales con un abogado antes de publicar en tiendas.

---

## [Sin publicar]

### Agregado
- **Android — Avisos ampliados** (sin Firebase ni claves: los calcula el teléfono): además de los pagos y compromisos,
  ahora avisa de **cobros de "Me deben"** (el día del cobro, el día anterior y a los 3 días de atraso),
  **presupuesto al 80 % y al 100 %** de cada categoría en la quincena y **nueva versión de la app**.
  En Más › Seguridad y avisos se prende o apaga cada tipo por separado.
- **Android:** la revisión pasa de una vez al día a **cada 6 horas**, y nunca avisa entre las 10 pm y las 7 am
  (lo pendiente sale en la primera revisión de la mañana). Quien ya tenía los avisos activos pasa al ritmo nuevo al abrir la app.
- **GitHub Actions:** paso opcional que **redespliega Render** al publicar el APK (secreto `RENDER_DEPLOY_HOOK`).
  Sin ese secreto no hace nada y avisa cómo lanzarlo a mano.

### Cambiado
- **Aviso de actualización:** el texto sale de `android/NOVEDADES.txt` (lenguaje sencillo).
  Si está vacío o es un mensaje técnico de Git (Merge, Revert…), dice "Mejoras de seguridad y correcciones.".
  El servidor también filtra esos mensajes, así que corrige el aviso de la 1.8 sin recompilar.

### Corregido
- **Servidor y web:** el APK se descarga con la versión en el nombre (`Quincena-1.7.apk`), para no abrir
  por error una descarga vieja (`Quincena.apk`) y quedar con la versión anterior.

---

## [1.5.0] — 23 oct 2026 · versionCode 7 (o el automático de GitHub)

### Agregado
- **Web y Android:** **gastos rápidos**: en Inicio aparecen los gastos que repetiste 2 o más veces
  en los últimos 60 días (máx. 6). Un toque los registra de nuevo, con "Deshacer".
- **Web y Android:** el formulario de gasto nuevo **recuerda** la última moneda, categoría y tasa.
- **Android:** **atajo en el ícono**: mantener presionado Quincena › "Registrar gasto".
- **Web (instalada como app):** el mismo atajo, desde el manifest (`/?nuevo=gasto`).
- **Android:** **recordatorio diario** a la hora que elijas (Más): solo avisa si ese día no registraste nada.
  Los domingos llega además el **resumen de la semana**. Tocar el aviso abre "Nuevo gasto".
- **Web y Android:** tarjeta **"Tu semana"** en Inicio los domingos y lunes.
- **Web:** pantalla de carga con aviso "Despertando el servidor…" si tarda más de 4 segundos.
- **Android:** en la pantalla de entrada, aviso de que la primera conexión puede tardar hasta un minuto.


### Corregido (flujo de GitHub, publicado junto con 1.5.0)
- Compilación automática (GitHub Actions): se quitó el paso `setup-android`, que fallaba al
  instalar el paquete `tools` (ya no existe). Ahora usa el SDK que trae el servidor de GitHub.
- El flujo revisa al inicio que estén la carpeta `android` y los 4 secretos, y avisa en español qué falta.
- Se borra `android/local.properties` antes de compilar en GitHub (apunta a la Mac).

---

## [1.4.0] — 23 oct 2026 · versionCode 6

### Quitado
- **Android:** lector de notificaciones del banco (permiso `BIND_NOTIFICATION_LISTENER_SERVICE`).
  Era la causa probable de que Android y Google Play Protect bloquearan la instalación.

### Agregado
- **Android:** registrar un gasto desde un mensaje del banco con **Compartir › Quincena**
  o con **Más › Pegar mensaje copiado**. Sin permisos especiales.
- **Android:** aviso **"Nueva versión disponible"** en Inicio, con botón Actualizar.
  La app consulta `/api/android-app` cada vez que se abre.
- **Android:** la versión instalada se ve en Más (bajo el nombre de usuario).
- **Servidor:** `downloads/version.json` define la versión publicada; `/api/android-app`
  devuelve `versionCode` y notas.
- **GitHub Actions:** `.github/workflows/android-apk.yml` compila y firma el APK en cada
  cambio dentro de `android/` y lo publica en `downloads/`. Render despliega solo.
- **Android:** firma y número de versión por variables de entorno (para GitHub Actions).
- **Web:** diseño propio para computadora (más de 900 px): barra lateral con
  "+ Registrar gasto", Inicio en columnas, Más en dos columnas, ventanas centradas.
  En el teléfono la web se ve igual que antes.

---

## [1.3.0] — 23 oct 2026 · versionCode 5

### Cambiado
- **Web y Android:** Inicio rediseñado para no tener que bajar tanto (de ~4.000 a ~1.000 px):
  - Tarjeta principal compacta con el selector de periodo (‹ › y 1.ª / 2.ª / Mes) adentro.
  - Seis mosaicos: Próximo pago, Sueldo comprometido, Deudas, Tus tasas, Tus bolívares y Calculadora.
    Cada uno abre su detalle.
  - "Tu mes": en qué se va el dinero + las 3 categorías de presupuesto más usadas, con "Ver todo".

---

## [1.2.0] — 23 oct 2026 · versionCode 4

### Agregado
- **Web y Android:** **Tu sueldo comprometido**: qué parte de las próximas 4 quincenas ya está
  comprometida (cuotas, pagos programados, compromisos y gastos fijos).
- **Web y Android:** **Compra en cuotas** (Cashea y otras): crea la deuda, las cuotas con fecha
  (cada 14 días, quincena o mes) y la inicial como gasto.
- **Web y Android:** **Calculadora de caja**: compara el costo real de pagar en $ o en Bs.
- **Web y Android:** **Poder de compra**: cuánto valor pierden tus bolívares guardados.
- **Servidor:** campos `bsSavings` y `rateHistory` (historial diario de tasas, máx. 120 días).
- **Android:** registro automático leyendo notificaciones del banco *(quitado en 1.4.0)*.

---

## [1.1.0] — 23 oct 2026 · versionCode 3

### Agregado
- **Web y Android:** **Las tres tasas**: al registrar un gasto en bolívares se marca a qué tasa
  se consiguieron (BCV, USDT u otra) y se calcula su costo real en dólares.
- **Web y Android:** tarjeta "Tus tasas este mes" con la brecha BCV/USDT y cuánto ahorraste o perdiste.
- **Servidor:** dólar USDT (paralelo) desde dolarapi; campos `rateType`, `rateUsed`,
  `bcvRate`, `marketRate` en los gastos.

---

## [1.0.0] — 22–23 oct 2026 · versionCode 2

### Agregado
- **Android:** migración completa de la web a app nativa (Kotlin + Compose): Inicio, Movimientos,
  Deudas, Pagos y Más; los formularios de la web; simulador de deudas; gastos fijos;
  compromisos; sincronización sin conexión; entrada con huella o rostro; avisos de pago locales.
- **Android:** diseño de la web (colores, letras Bricolage Grotesque y Figtree, íconos).
- **Android:** ícono adaptativo en todos los tamaños.
- **Web:** modal "Quincena para Android" para descargar el APK: se abre al iniciar sesión,
  aviso en Inicio y tarjeta en Más. Ruta `/descargar/android`.
- **Web:** logo incluido en la página (no depende de `static/icons`).

### Corregido
- **Android:** error de compilación "Conflicting overloads: parseMoney" (función duplicada en
  `MainActivity.kt`).
- **Android:** el modo de pruebas vuelve a usar el servidor local de la Mac (`10.0.2.2:5001`).
- **Android:** los errores de inicio de sesión dicen la causa exacta.
