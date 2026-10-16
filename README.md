# Control Financiero — laptop + Pixel + Render

Esta versión conserva las pantallas funcionales del HTML anterior y agrega un backend Flask, cuentas individuales, permisos administrador/usuario, PostgreSQL con un espacio financiero privado por usuario y una versión instalable como PWA en Chrome para Android. Esta carpeta contiene la actualización de privacidad. Para activarla en una instalación Render existente, incorpora los archivos actualizados a su repositorio y espera a que termine el despliegue. Importante: ejecutar `python app.py` en la laptop usa una base SQLite local separada; para que laptop y Pixel compartan la misma información, abre la URL HTTPS de Render en ambos dispositivos.

## Corrección visual (versión 2)

Si la versión publicada parpadea después de iniciar sesión, publica esta versión actualizada: evita volver a construir toda la pantalla al terminar cada sincronización sin cambios y actualiza la tasa cambiaria sin repintar el dashboard completo cuando la vista está en USD. El service worker usa una nueva versión de caché para que los navegadores actualicen la interfaz. Tras el despliegue, recarga la página con `Cmd + Shift + R` en Mac; en Android, cierra la pestaña y abre de nuevo la URL.

## Qué cambia

- Las ventanas de los formularios se insertan correctamente en pantalla.
- Ingreso con usuario y contraseña; no hay creación pública de cuentas.
- La primera cuenta administradora se crea con un token de configuración de un solo uso. El token deja de servir automáticamente después de crear la primera cuenta.
- El administrador puede autorizar/desactivar usuarios. Las nuevas cuentas reciben una contraseña temporal que se muestra una sola vez y deben cambiar al primer ingreso.
- Cada cuenta ve únicamente sus propios ingresos, presupuestos, gastos, deudas y registros escolares. Los datos de esa cuenta sí se sincronizan entre su laptop y su Pixel.
- Los cambios se guardan en un estado separado por usuario en la misma base de datos; la sincronización utiliza revisión y detección de cambios simultáneos dentro de ese espacio privado.
- Todos los usuarios pueden administrar sus propios registros financieros. Solo el administrador puede gestionar cuentas y contraseñas.
- Contraseñas con hash, sesiones HttpOnly, SameSite, cookie Secure en Render, protección CSRF para solicitudes modificadoras, limitación básica de intentos, límites de tamaño y cabeceras de seguridad.
- Registro de auditoría para inicio de sesión, cambios de datos y gestión de usuarios.
- PWA instalable. El shell puede quedar almacenado, pero las cuentas, lecturas y escrituras de datos financieros requieren conexión al servidor. Las llamadas API no se guardan en la caché.

## Ejecutar en Mac local

Requiere Python 3.11+ (recomendado 3.12).

```bash
cd control_financiero_cloud
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y reemplaza **ambos** valores de ejemplo con cadenas distintas. Puedes generar valores con:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Ejecuta ese comando dos veces: una salida va a `APP_SECRET_KEY` y otra a `BOOTSTRAP_TOKEN`. Para local por HTTP deja `COOKIE_SECURE=false`.

Inicia la aplicación:

```bash
python app.py
```

Abre `http://127.0.0.1:5000`. En la primera pantalla introduce el valor de `BOOTSTRAP_TOKEN`, crea el usuario administrador y elige una contraseña fuerte de al menos 12 caracteres. La base local `control_financiero.db` se crea automáticamente.

## Publicar en Render

1. Crea un repositorio privado en GitHub y sube el contenido de esta carpeta. **No subas `.env` ni una base de datos local**.
2. En Render elige **New → Blueprint** y conecta el repositorio. Render leerá `render.yaml` y propondrá el web service y PostgreSQL. La base PostgreSQL de este blueprint es de pago; confirma el precio que muestra Render antes de crearla. El servicio web está configurado en el plan Free.
3. Cuando Render pida el valor de `BOOTSTRAP_TOKEN`, pega una cadena aleatoria larga generada localmente. No la pongas en el código ni en GitHub. `APP_SECRET_KEY` se genera desde el blueprint y `DATABASE_URL` se enlaza a la base. La base está limitada a conexiones de red internas de Render.
4. Una vez en estado Live, abre la dirección `https://...onrender.com` y completa la configuración inicial. Usa credenciales distintas a las de otros sitios.
5. Inicia sesión y abre **Usuarios** para crear las cuentas de las personas autorizadas. Comparte las contraseñas temporales por un canal privado; cada persona debe cambiarla al entrar.
6. En la pantalla de la base de datos de Render, confirma que las copias de seguridad/retención disponibles en tu plan son adecuadas para la información que vas a guardar. Exporta periódicamente los datos mediante un procedimiento aprobado; el registro de auditoría no sustituye una copia de seguridad.

`render.yaml` crea PostgreSQL en la región Ohio y enlaza la URL interna a la aplicación. El servicio web Free puede suspenderse al quedar inactivo y tardar en despertar. La base de datos usa un plan pagado, no el plan Postgres Free de prueba: las bases Postgres gratuitas de Render vencen a los 30 días y no tienen copias de seguridad administradas; confirma los precios que Render muestre antes de crear los recursos. Si prefieres otra región, cambia `region` antes de crear los recursos. No cargues información real hasta probar el alta de usuario, el inicio de sesión, el cierre de sesión y la sincronización con dos dispositivos.

## Instalar en Google Pixel

1. Despliega la aplicación y ábrela en Chrome del Pixel usando la URL HTTPS de Render.
2. Inicia sesión con la cuenta autorizada.
3. Abre el menú de Chrome (⋮) y elige **Instalar aplicación** o **Añadir a pantalla de inicio** (el texto depende de la versión de Chrome).
4. A partir de entonces, abre el icono de Control Financiero. Necesita internet para cargar o sincronizar los datos; el modo sin conexión sólo conserva el shell de la aplicación, no los datos financieros.

## Datos y seguridad — leer antes de usar

- Cada cuenta tiene sus propios datos financieros privados. El mismo usuario sincroniza su información entre sus dispositivos; no se comparte con otras cuentas.
- Todos los usuarios autenticados pueden gestionar sus propios registros financieros. El rol **Administrador** sirve para crear/desactivar cuentas y restablecer claves; no concede visibilidad sobre las finanzas personales de otras cuentas.
- No se implementan recuperación de contraseña por correo ni invitaciones por email; un administrador debe coordinar el restablecimiento si alguien pierde acceso. No reutilices contraseñas importantes.
- No guardes datos de tarjetas completas, claves bancarias, PIN o credenciales financieras en campos de descripción.
- La tasa BCV conserva el mecanismo remoto de consulta del HTML original y necesita internet. Si falla, se conserva la última tasa en memoria/base de datos.
- El caché del service worker no guarda respuestas `/api`; se requiere conexión para iniciar sesión o consultar/guardar los datos.
- Las limitaciones de login usan almacenamiento en memoria; para una exposición de alto riesgo, cambia `RATELIMIT_STORAGE_URI` a Redis gestionado y agrega políticas de acceso/alertas.
- Los planes y precios de Render cambian; compruébalos en el panel antes de autorizar el despliegue.

## Migrar los datos que estaban guardados en el HTML anterior

El almacenamiento del navegador no se comparte entre `file://` y la dirección `https://...onrender.com`. Si la versión local ya tenía registros, exporta ese contenido antes de dejar de usar el archivo anterior:

1. Abre en Chrome el HTML antiguo que contiene los datos originales.
2. Pulsa `F12` → **Console** y ejecuta `copy(localStorage.getItem("finControlDataLocal"))`.
3. Pega el contenido copiado en un editor de texto en modo texto plano y guárdalo como `respaldo-antiguo.json`.
4. Entra a la versión nueva como administrador → **Usuarios** → **Importar respaldo antiguo** y selecciona el JSON.

La importación solo se acepta si ingresos, presupuestos, gastos y deudas en la nube están vacíos. Comprueba los montos y saldos después de importarlos. El menú de Usuarios permite administrar cuentas y restablecer contraseñas. La importación de datos antiguos está restringida al espacio privado de la cuenta que inicia la importación.

## Prueba de humo (opcional)

Después de instalar las dependencias, ejecuta desde la carpeta del proyecto:

```bash
python tests/smoke_test.py
```

Este script crea una base SQLite temporal y comprueba la configuración inicial, inicio de sesión, contraseña temporal, aislamiento financiero entre cuentas y que los usuarios normales no puedan administrar otras cuentas.

## Nuevas funciones de ingresos y colegio

- En **Ingresos**, registra sueldo, bono, cestatickets u otro ingreso, con monto por pago, moneda, frecuencia quincenal/mensual/ocasional y días de pago. Los ingresos recurrentes quincenales se estiman como dos pagos al mes para el resumen.
- En **Colegio**, registra compromisos por estudiante, concepto, monto, fecha de vencimiento y notas. Registra pagos parciales o totales con fecha, método y referencia; la pantalla calcula el saldo pendiente y conserva el historial.
- Los datos nuevos se guardan en el estado privado del usuario en PostgreSQL, y se sincronizan entre su laptop y Pixel cuando el despliegue actualizado está activo.
- Los pagos escolares también se reflejan como movimientos de gasto para que afecten el cálculo de disponible. No registres el mismo pago manualmente en Gastos, porque se duplicaría.

Tras desplegar, recarga la web para actualizar la PWA. Verifica con un gasto de prueba y elimínalo después, y confirma que se vea en ambos dispositivos antes de cargar datos reales.


## Plan de pagos de deudas sin ahogar el presupuesto

- En **Deudas** aparece **Pagar deudas sin ahogarte**. Configura gastos esenciales mensuales, reserva/colchón y el porcentaje del excedente que quieres destinar a deuda (30–85%; valor inicial 60%). No se recomienda dedicar automáticamente todo el ingreso a las deudas.
- Completa para cada deuda activa su cuota mínima/obligatoria mensual y, si la conoces, la tasa por período (mensual o semanal). El botón **Condiciones** permite actualizar esos datos incluso en deudas ya registradas.
- El simulador primero separa ingreso recurrente, gastos esenciales y reserva; compara las cuotas mínimas con la capacidad protegida; y solo calcula plazo y distribución si se han capturado las cuotas mínimas de todas las deudas activas y caben dentro del presupuesto.
- Estrategia **avalancha** prioriza la tasa conocida más alta; **bola de nieve** prioriza el saldo más pequeño. La proyección puede ser optimista para deudas cuya tasa no se conoce, ya que no inventa intereses. No incluye comisiones, mora, tasas variables ni penalidades y no sustituye negociar con acreedores o recibir asesoría financiera.
- El plan se guarda en el estado JSON privado de cada usuario en PostgreSQL, sin añadir tablas financieras ni eliminar datos existentes. Después de publicar, recarga con `Cmd + Shift + R` en Mac y cierra/reabre la PWA en el Pixel.

Pruebas adicionales del simulador (requiere Node.js):

```bash
node tests/debt_plan_test.js
```


## Actualización: pagos y planificación de deudas

- En tarjetas de crédito, el formulario de abono permite seleccionar monto mínimo, saldo total restante o monto personalizado.
- Para deudas distintas de tarjeta se puede registrar un pago realizado o planificarlo para primera/segunda quincena o una fecha específica. Los pagos planificados se guardan separados y no se descuentan del saldo hasta registrar el pago real.
- Los pagos reales continúan afectando el saldo de la deuda y se reflejan como gastos. Los pagos programados se guardan en el espacio privado de la cuenta y se sincronizan entre sus dispositivos.
- La fecha de corte y fecha límite de pago son recordatorios ingresados por el usuario; esta versión no consulta el banco ni puede detectar automáticamente un pago bancario. Después de registrar un pago mínimo, revisa el estado de cuenta y actualiza el saldo total manualmente.
- Antes de usar con dinero real, prueba con datos ficticios y revisa la pantalla de planificación, en especial las fechas de quincena y la disponibilidad mensual. La actualización de recordatorios descrita al final agrega avisos push diarios para fechas de vencimiento.


## Edición de deudas e ingresos (versión actualizada)

- En **Deudas**, inicia sesión como administrador y usa el botón **Editar deuda** para cambiar acreedor, descripción, monto original, moneda, cuota mínima, fecha de corte/pago, cuotas, factura e interés. Los abonos ya registrados se conservan y el monto original no puede bajar de lo ya pagado.
- En **Ingresos**, el administrador puede pulsar **Editar** para modificar sueldo, bono, cestatickets u otro ingreso recurrente, incluyendo monto, moneda, frecuencia y días de pago. También puede eliminar una fuente incorrecta.
- El lápiz del ingreso base en el resumen solo está visible para el administrador.
- Las cuentas de usuario autorizado pueden seguir registrando gastos y nuevos movimientos, pero no pueden editar/eliminar fuentes de ingresos existentes ni alterar los términos de deudas; el servidor valida esto además de la interfaz.

No borres la base de datos para corregir un monto. Estas funciones permiten ajustar el registro y conservan el historial de abonos. Tras subir los archivos al repositorio conectado a Render, espera a que termine el despliegue y recarga en el navegador con una recarga forzada. El service worker usa una versión nueva de caché para descargar los archivos actualizados.


## Privacidad por usuario (actualización)

- Cada cuenta tiene un estado financiero privado, asociado en el servidor a su identidad autenticada. No se utiliza el identificador del usuario enviado por el navegador para elegir qué datos leer o modificar.
- Un usuario no puede leer, reemplazar ni sincronizar datos de otra cuenta a través de `/api/state` o `/api/import-legacy`. El administrador solo administra cuentas y contraseñas; no puede ver las finanzas personales de otras personas desde la aplicación.
- Al migrar una instalación que tenía un único estado compartido, los datos existentes se conservan en la cuenta del administrador original (el administrador más antiguo). Las demás cuentas empiezan con un estado vacío. Esto evita seguir exponiendo el conjunto antiguo a todos; revisa la asignación histórica después del despliegue. No borra la base de datos ni las filas financieras existentes.
- Cada usuario puede editar y borrar sus propios ingresos, gastos y deudas. La gestión de cuentas continúa reservada al administrador.
- La migración añade `user_id` a `shared_state` y un índice único. Haz una copia de seguridad de PostgreSQL antes de desplegar cambios sobre datos reales.


## Desplegar la actualización de privacidad por usuario

1. Antes del despliegue, crea/descarga una copia de seguridad de PostgreSQL desde las opciones que tenga disponibles tu plan de Render.
2. Copia los archivos de esta versión al repositorio privado que ya está conectado al servicio Render, sustituyendo `app.py`, `static/index.html`, `static/service-worker.js`, `README.md` y `tests/smoke_test.py`. Conserva la configuración existente del servicio y no subas secretos (`.env`, `APP_SECRET_KEY` o `BOOTSTRAP_TOKEN`).
3. Confirma los cambios con Git y haz `git push`; espera a que Render termine el despliegue. Esta versión ejecuta la migración de `shared_state` al iniciar, agregando un propietario por estado financiero.
4. Por compatibilidad, la antigua fila global se conserva bajo la cuenta del administrador más antiguo. Los demás usuarios empezarán con un estado financiero vacío y separado. No se eliminan los registros antiguos, pero como antes pertenecían a una bolsa compartida, no es posible identificar automáticamente qué fila histórica correspondía a qué persona. Exporta/ revisa esa información con cuidado antes de redistribuirla.
5. Prueba con dos cuentas: crea un gasto de prueba con cada cuenta y confirma que cada una solo vea el suyo; valida además que el mismo usuario vea sus propios datos tanto en Mac como en Pixel. Después elimina los gastos de prueba.

La verificación completa de API requiere instalar las dependencias de `requirements.txt`. En el entorno de preparación, la sintaxis Python y JavaScript y las instrucciones SQL de migración SQLite se validaron, pero la prueba integral de Flask no pudo ejecutarse porque Flask no estaba instalado.


## Actualización: planificador, recordatorios push y acceso biométrico

### Planificador de pagos y próximo vencimiento

- La pestaña **Pagos** muestra la agenda de deudas planificadas y los vencimientos escolares que aún tengan saldo pendiente. En el inicio aparece la tarjeta **Próximo pago planificado** con el vencimiento más cercano.
- Cada pago de deuda programado guarda `debtId`, que lo vincula con la deuda específica creada por el usuario. La agenda muestra entidad, descripción, moneda y monto de esa deuda.
- Al crear una programación, el saldo real de la deuda no se reduce todavía: el monto se **reserva** para impedir que se programen pagos por encima de lo pendiente. La tarjeta de deuda muestra saldo real, monto reservado en agenda y capacidad disponible para planificar.
- Al confirmar **Registrar como pagado**, el mismo registro vinculado cambia a `paid`, el importe se suma a `debt.paid` y se agrega a gastos. Si se cancela el plan, no reduce la deuda. Cancelar o borrar una deuda cancela sus programaciones activas para no dejar pagos huérfanos.
- Hay una prueba adicional `node tests/planned_payment_link_test.js` para comprobar esa relación y el comportamiento del saldo. La aplicación debe probarse en Render antes de registrar pagos reales.
- Se muestran cantidades pendientes, vencidas, vencen hoy y el importe de deudas programadas. Los pagos escolares se calculan como saldo pendiente y el servidor evita enviar recordatorios de importes ya cubiertos.

### Notificaciones: 7, 4, 1 día y el día del vencimiento

Los recordatorios push se programan para 7, 4, 1 y 0 días antes del vencimiento de pagos de deudas planificados y compromisos escolares con saldo pendiente. El proceso diario registra las entregas por usuario, tipo de pago, fecha y día de anticipación para evitar duplicarlas. Los avisos se envían a todos los dispositivos con notificaciones activadas para esa cuenta. Si un dispositivo rechaza el permiso, está sin conexión mucho tiempo o invalida su suscripción, el envío puede no mostrarse; el historial de la app sigue siendo la referencia.

Para que funcionen incluso con la PWA cerrada, hay que configurar claves VAPID y un trabajo cron en Render; agregar el botón en la web no basta.

1. En la Mac, genera las claves localmente (se necesita conexión a internet para instalar `cryptography` si no está disponible):

   ```bash
   cd control_financiero_implementacion
   python3 -m venv .venv-keys
   source .venv-keys/bin/activate
   pip install cryptography
   python tools/generate_vapid_keys.py
   ```

   La herramienta imprime `VAPID_PUBLIC_KEY` y `VAPID_PRIVATE_KEY`. El valor privado es un secreto: no lo pegues en GitHub, un documento compartido ni un chat.

2. En las variables de entorno del **servicio web** de Render, establece `VAPID_PUBLIC_KEY` con el valor público que acaba de imprimir la herramienta. El servicio web ya debe tener `APP_PUBLIC_ORIGIN=https://control-financiero-wrm5.onrender.com`, `WEBAUTHN_RP_ID=control-financiero-wrm5.onrender.com` y `APP_TIMEZONE=America/Caracas`. Si tu URL pública cambió, ajusta el origen y RP ID a tu dominio real antes de registrar passkeys.

3. En Render crea el cron `control-financiero-recordatorios` (o sincroniza su definición `render.yaml` si gestionas el servicio con Blueprint). Usa el mismo repositorio y rama, `Build Command: pip install -r requirements.txt`, `Start Command: python send_payment_reminders.py`, y el horario `0 12 * * *` (12:00 UTC, aproximadamente 8:00 a. m. en Caracas). Debe usar el mismo `DATABASE_URL` interno de PostgreSQL que el web service, `APP_TIMEZONE=America/Caracas`, `APP_SECRET_KEY` generado por Render, `VAPID_PRIVATE_KEY` con el valor privado, y `VAPID_CLAIMS_SUB=mailto:tu-correo-real@dominio.com`.

4. En la app, desde cada dispositivo, entra en **Pagos → Activar recordatorios** y permite las notificaciones. Instala la PWA en el Pixel desde Chrome para tener un acceso cómodo; vuelve a activar permiso y suscripción en cada navegador/dispositivo.

5. Ejecuta manualmente el cron una vez desde Render (**Trigger Run**) y revisa sus logs. Si no hay pagos en las ventanas de recordatorio o no hay suscripciones, es normal que envíe cero notificaciones.

**Costo:** Render cobra el uso de cron jobs, con un cargo mensual mínimo documentado de USD 1 por servicio. Revisa el precio/plan que Render muestra antes de crear el cron; el trabajo programado no se crea en una instalación manual solo por hacer `git push`.

### Acceso con huella, rostro o passkey

- Inicia sesión primero con contraseña en el dispositivo donde quieras habilitarlo. En el encabezado, pulsa **Acceso biométrico**, confirma la acción del sistema y asigna un nombre al dispositivo. Cada Pixel, MacBook o navegador requiere su propio registro.
- En el próximo acceso, escribe el nombre de usuario y pulsa **Entrar con huella, rostro o passkey**. Android/Chrome o el sistema operativo ofrecerá la verificación disponible: huella, reconocimiento facial, desbloqueo del dispositivo o una clave de acceso sincronizada. La biometría concreta depende del dispositivo configurado; la web no puede obligar al teléfono a usar una modalidad específica.
- El backend guarda el identificador y la clave pública WebAuthn; la huella/plantilla facial y la clave privada permanecen bajo el control del autenticador del dispositivo. Si se pierde un dispositivo, inicia sesión por contraseña en otro equipo y elimina la credencial correspondiente cuando la pantalla de gestión de passkeys esté disponible, o desactívala desde los ajustes de credenciales del sistema.
- Passkeys usan el dominio HTTPS asociado a la credencial. Cambiar de dominio requiere volver a registrar el acceso biométrico. No elimines las contraseñas: deben quedar como método alternativo de acceso.

### Seguridad, límites y validación

- Las tablas para passkeys, suscripciones push y registro de avisos se crean mediante `db.create_all()` al arrancar la app y no sustituyen ni borran `shared_state`.
- Las notificaciones push requieren permiso por dispositivo; no son un SMS ni garantizan la entrega si el navegador/servicio push no está disponible. El cron diario da una ventana de ejecución aproximada de la hora configurada.
- La sintaxis Python y JavaScript se puede comprobar con:

  ```bash
  python3 -m py_compile app.py send_payment_reminders.py tools/generate_vapid_keys.py
  node --check /tmp/control-financiero-inline.js
  ```

  Para extraer el JavaScript inline actualizado antes de la segunda comprobación:

  ```bash
  python3 - <<'PY'
  from pathlib import Path
  import re
  source = Path('static/index.html').read_text()
  scripts = re.findall(r'<script>(.*?)</script>', source, flags=re.S)
  Path('/tmp/control-financiero-inline.js').write_text('\n'.join(scripts))
  PY
  node --check /tmp/control-financiero-inline.js
  ```

  Estas comprobaciones validan sintaxis, no sustituyen una prueba integral del servidor y el navegador. Antes de usar datos reales, prueba un pago ficticio y confirma que llega una sola notificación en cada uno de los cuatro días configurados.
