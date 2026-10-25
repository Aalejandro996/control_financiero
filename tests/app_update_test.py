"""Pruebas de la actualización obligatoria de la app Android (sin red)."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="1000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="", SMTP_PASSWORD="")
os.environ.pop("ANDROID_MIN_VERSION", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        fail += 1; print("  FALLA", name, extra)


c = A.app.test_client()
latest = A.android_version()["versionCode"]
APP_OLD = {"User-Agent": "okhttp/4.12.0"}                       # app 1.14 o anterior: no manda su versión
app_v = lambda v: {"User-Agent": "okhttp/4.12.0", "X-App-Version": str(v)}
login = {"username": "nadie", "password": "Clave-Equivocada-1"}

print(f"== Por defecto se exige la última versión ({latest}) ==")
r = c.post("/api/login", json=login, headers=APP_OLD)
j = r.get_json()
check("una app vieja (sin X-App-Version) recibe 426", r.status_code == 426 and j["code"] == "update_required", (r.status_code, j))
check("el aviso es claro y dice qué hacer", "Actualiza" in j["error"] and "Inicio" in j["error"] and j["url"].startswith("/descargar/android"))
check("una versión anterior a la última recibe 426", c.post("/api/login", json=login, headers=app_v(latest - 1)).status_code == 426)
check("la última versión pasa (credenciales malas: 401, no 426)", c.post("/api/login", json=login, headers=app_v(latest)).status_code == 401)
check("una versión más nueva que la publicada también pasa", c.post("/api/login", json=login, headers=app_v(latest + 5)).status_code == 401)
check("una cabecera inválida cuenta como versión vieja", c.post("/api/login", json=login, headers=app_v("abc")).status_code == 426)

print("== Lo que una app vieja sigue pudiendo usar ==")
r = c.get("/api/android-app", headers=APP_OLD)
check("puede saber que hay versión nueva", r.status_code == 200 and r.get_json()["minVersionCode"] == latest)
check("puede descargar el APK", c.get("/descargar/android", headers=APP_OLD).status_code == 200)
check("puede consultar el estado del servidor", c.get("/api/status", headers=APP_OLD).status_code == 200)

print("== La web no se ve afectada ==")
check("el navegador entra normal (401 por credenciales, no 426)", c.post("/api/login", json=login, headers={"User-Agent": "Mozilla/5.0"}).status_code == 401)

print("== Configuración en Render (ANDROID_MIN_VERSION) ==")
os.environ["ANDROID_MIN_VERSION"] = "off"
check("off: las versiones viejas pasan", c.post("/api/login", json=login, headers=APP_OLD).status_code == 401)
os.environ["ANDROID_MIN_VERSION"] = str(latest - 1)
check("un número fija esa versión mínima", c.post("/api/login", json=login, headers=app_v(latest - 1)).status_code == 401
      and c.post("/api/login", json=login, headers=app_v(latest - 2)).status_code == 426)
os.environ["ANDROID_MIN_VERSION"] = str(latest + 100)
check("nunca exige una versión que no existe", c.post("/api/login", json=login, headers=app_v(latest)).status_code == 401)
os.environ["ANDROID_MIN_VERSION"] = "latest"
check("latest vuelve a exigir la última", c.post("/api/login", json=login, headers=app_v(latest - 1)).status_code == 426)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
