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
- Antes de usar con dinero real, probar con datos ficticios y revisar la pantalla de planificación, en especial las fechas de quincena y la disponibilidad mensual. La programación se registra, pero aún no se crea una agenda automática de vencimientos con notificaciones.


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


### Corrección del error 500 en `/api/state` (PostgreSQL)

Esta versión incluye una migración de arranque que repara la columna `shared_state.id` en bases PostgreSQL existentes cuando estaba creada como `NOT NULL` sin un `DEFAULT` de secuencia. La migración crea/alinea `shared_state_id_seq`, asigna el valor por encima del ID máximo existente y establece el valor automático de la columna. No borra filas ni reinicia la base de datos.

Al publicar la actualización, Render ejecutará esta migración al iniciar la aplicación. Después, prueba el inicio de sesión con una segunda cuenta y confirma que `/api/state` devuelve HTTP 200. No es necesario borrar usuarios ni recrear PostgreSQL.
