# Control Financiero — laptop + Pixel + Render

Esta versión conserva las pantallas funcionales del HTML anterior y agrega un backend Flask, cuentas individuales, permisos administrador/usuario, PostgreSQL para datos compartidos y una versión instalable como PWA en Chrome para Android. La app **no está desplegada todavía**; el código está preparado para probarse localmente y publicar desde GitHub en Render. Importante: ejecutar `python app.py` en la laptop usa una base SQLite local separada; para que laptop y Pixel compartan la misma información, abre la URL HTTPS de Render en ambos dispositivos.

## Corrección visual (versión 2)

Si la versión publicada parpadea después de iniciar sesión, publica esta versión actualizada: evita volver a construir toda la pantalla al terminar cada sincronización sin cambios y actualiza la tasa cambiaria sin repintar el dashboard completo cuando la vista está en USD. El service worker usa una nueva versión de caché para que los navegadores actualicen la interfaz. Tras el despliegue, recarga la página con `Cmd + Shift + R` en Mac; en Android, cierra la pestaña y abre de nuevo la URL.

## Qué cambia

- Las ventanas de los formularios se insertan correctamente en pantalla.
- Ingreso con usuario y contraseña; no hay creación pública de cuentas.
- La primera cuenta administradora se crea con un token de configuración de un solo uso. El token deja de servir automáticamente después de crear la primera cuenta.
- El administrador puede autorizar/desactivar usuarios. Las nuevas cuentas reciben una contraseña temporal que se muestra una sola vez y deben cambiar al primer ingreso.
- Los usuarios autorizados ven el mismo conjunto de ingresos, presupuestos, gastos y deudas en laptop y teléfono.
- Los cambios se guardan en una base de datos; la sincronización utiliza revisión y detección de cambios simultáneos para evitar sobrescribir silenciosamente modificaciones ajenas.
- Los usuarios normales no pueden borrar registros ni gestionar cuentas; esas funciones son exclusivas del administrador.
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

- Todas las cuentas activas autorizadas comparten los mismos datos financieros; no hay espacios de datos independientes por usuario en esta versión.
- El rol **Usuario autorizado** puede añadir/actualizar registros, ingreso y presupuesto; el administrador puede además borrar registros y gestionar usuarios.
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

La importación solo se acepta si ingresos, presupuestos, gastos y deudas en la nube están vacíos. Comprueba los montos y saldos después de importarlos. El menú de Usuarios también permite descargar un respaldo JSON de los datos compartidos y restablecer contraseñas temporales.

## Prueba de humo (opcional)

Después de instalar las dependencias, ejecuta desde la carpeta del proyecto:

```bash
python tests/smoke_test.py
```

Este script crea una base SQLite temporal y comprueba la configuración inicial, inicio de sesión, contraseña temporal, acceso compartido y que los usuarios normales no puedan borrar gastos ni administrar cuentas.

## Nuevas funciones de ingresos y colegio

- En **Ingresos**, registra sueldo, bono, cestatickets u otro ingreso, con monto por pago, moneda, frecuencia quincenal/mensual/ocasional y días de pago. Los ingresos recurrentes quincenales se estiman como dos pagos al mes para el resumen.
- En **Colegio**, registra compromisos por estudiante, concepto, monto, fecha de vencimiento y notas. Registra pagos parciales o totales con fecha, método y referencia; la pantalla calcula el saldo pendiente y conserva el historial.
- Los datos nuevos se guardan en el mismo estado compartido de PostgreSQL que gastos y deudas, por lo que se comparten entre laptop y Pixel cuando el despliegue actualizado está activo.
- Los pagos escolares también se reflejan como movimientos de gasto para que afecten el cálculo de disponible. No registres el mismo pago manualmente en Gastos, porque se duplicaría.

Tras desplegar, recarga la web para actualizar la PWA. Verifica con un gasto de prueba y elimínalo después, y confirma que se vea en ambos dispositivos antes de cargar datos reales.


## Plan de pagos de deudas sin ahogar el presupuesto

- En **Deudas** aparece **Pagar deudas sin ahogarte**. Configura gastos esenciales mensuales, reserva/colchón y el porcentaje del excedente que quieres destinar a deuda (30–85%; valor inicial 60%). No se recomienda dedicar automáticamente todo el ingreso a las deudas.
- Completa para cada deuda activa su cuota mínima/obligatoria mensual y, si la conoces, la tasa por período (mensual o semanal). El botón **Condiciones** permite actualizar esos datos incluso en deudas ya registradas.
- El simulador primero separa ingreso recurrente, gastos esenciales y reserva; compara las cuotas mínimas con la capacidad protegida; y solo calcula plazo y distribución si se han capturado las cuotas mínimas de todas las deudas activas y caben dentro del presupuesto.
- Estrategia **avalancha** prioriza la tasa conocida más alta; **bola de nieve** prioriza el saldo más pequeño. La proyección puede ser optimista para deudas cuya tasa no se conoce, ya que no inventa intereses. No incluye comisiones, mora, tasas variables ni penalidades y no sustituye negociar con acreedores o recibir asesoría financiera.
- El plan se guarda en el mismo estado JSON de PostgreSQL, sin añadir tablas ni eliminar datos existentes. Después de publicar, recarga con `Cmd + Shift + R` en Mac y cierra/reabre la PWA en el Pixel.

Pruebas adicionales del simulador (requiere Node.js):

```bash
node tests/debt_plan_test.js
```


## Actualización: pagos y planificación de deudas

- En tarjetas de crédito, el formulario de abono permite seleccionar monto mínimo, saldo total restante o monto personalizado.
- Para deudas distintas de tarjeta se puede registrar un pago realizado o planificarlo para primera/segunda quincena o una fecha específica. Los pagos planificados se guardan separados y no se descuentan del saldo hasta registrar el pago real.
- Los pagos reales continúan afectando el saldo de la deuda y se reflejan como gastos.
- La fecha de corte y fecha límite de pago son recordatorios ingresados por el usuario; esta versión no consulta el banco ni puede detectar automáticamente un pago bancario. Después de registrar un pago mínimo, revisa el estado de cuenta y actualiza el saldo total manualmente.
- Antes de usar con dinero real, probar con datos ficticios y revisar la pantalla de planificación, en especial las fechas de quincena y la disponibilidad mensual. La programación se registra, pero aún no se crea una agenda automática de vencimientos con notificaciones.
