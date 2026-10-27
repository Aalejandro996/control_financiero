# Publicar Quincena VE en Google Play

Cada vez que GitHub Actions compila la app (un push que toca `android/`), además del APK de la web deja un
**AAB firmado** listo para Google Play: pestaña **Actions** › la ejecución "APK Android" › **Artifacts** ›
`quincena-<versión>-google-play` (se guarda 90 días).

Pasos la primera vez (los hace el dueño de la cuenta):

1. Crear la cuenta de desarrollador en <https://play.google.com/console> (pago único de 25 USD; pide verificar identidad).
2. **Crear app**: nombre en la tienda "Quincena VE" (Google Play no admite emojis en el título), idioma español,
   app gratuita. Ya existe "Quincena: tu plata" de otra empresa: revisar la marca antes de publicar.
3. En **Integridad de la app**, aceptar **Play App Signing**. La llave con que firma GitHub Actions
   (`~/Respaldos/quincena-2026-10-25/quincena-nueva.jks`, alias `quincena`) queda como llave de subida.
   Si Google cambia la llave con que firma la app, agregar su huella SHA-256 a `ANDROID_CERT_SHA256` en Render
   (separada por coma de la actual) para que los enlaces de los correos sigan abriendo la app.
4. Completar la ficha: descripción, capturas, ícono 512×512 (`static/icons/icon-512.png`), política de privacidad
   (`https://control-financiero-wrm5.onrender.com/privacidad`), clasificación de contenido y la sección
   **Seguridad de los datos** (correo, datos financieros que registra la persona, cifrado en tránsito, se pueden borrar).
5. Subir el AAB a **Prueba interna** primero; cuando funcione, pasarlo a **Producción**.

La versión de la tienda y la del APK de la web salen del mismo número (`versionCode` = 100 + número de ejecución),
así que una persona puede pasar de una a otra sin perder datos: la cuenta y los datos están en el servidor.
