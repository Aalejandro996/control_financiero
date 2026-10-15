# Control Financiero — laptop + Pixel + Render

Esta versión conserva las pantallas funcionales del HTML anterior y agrega un backend Flask, cuentas individuales, permisos administrador/usuario, PostgreSQL para datos compartidos y una versión instalable como PWA en Chrome para Android. La app **no está desplegada todavía**; el código está preparado para probarse localmente y publicar desde GitHub en Render. Importante: ejecutar `python app.py` en la laptop usa una base SQLite local separada; para que laptop y Pixel compartan la misma información, abre la URL HTTPS de Render en ambos dispositivos.

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
# control_financiero
