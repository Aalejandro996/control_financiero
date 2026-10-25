# Seguridad de Quincena — guía de puesta en marcha

## 1. Correos (recuperar clave y avisos) con Brevo — gratis, 300 al día
1. Crea cuenta en https://www.brevo.com y entra a **Senders, domains & dedicated IPs → Senders**.
2. Agrega tu correo como remitente y verifícalo con el enlace que te llega.
3. **SMTP & API → API Keys → Generate a new API key** y cópiala (empieza por `xkeysib-`).
4. En Render → servicio → **Environment** agrega:
   - `BREVO_API_KEY` = la clave del paso 3
   - `MAIL_FROM` = `Quincena <tu-correo-verificado@dominio.com>` (debe ser el mismo remitente verificado)
5. Guarda: Render redespliega solo. Prueba con "¿Olvidaste tu clave?" en la web.

Sin `BREVO_API_KEY` la app usa SMTP (solo funciona en planes de pago de Render).
Si el correo no sale, mira los registros de Render: se anota el motivo, sin el contenido del mensaje.

## 2. Qué protege el servidor
- Consultas SQL siempre parametrizadas (ORM): una entrada hostil nunca se interpreta como SQL.
- Texto del usuario escapado al mostrarlo y CSP estricta; cookies `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict` y token CSRF.
- Cada dato financiero está atado al usuario de la sesión; nadie puede pedir los datos de otro.
- Límites: general por IP, de acceso por IP y por usuario, de registro, de recuperación (3/h por correo).
- Claves: mínimo y reglas de calidad, hash con sal, tiempo de respuesta parecido exista o no el usuario.
- Tokens de la app guardados como hash, vencen a los 90 días y se anulan al cambiar la clave.
- Errores genéricos: nunca se muestran rutas, consultas ni claves.

## 3. Probar antes de subir cambios
```
pip install -r requirements.txt
python3 tests/security_test.py     # servidor, sin red
python3 tests/xss_test.py          # necesita playwright + chromium
```
