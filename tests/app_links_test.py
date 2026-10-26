"""Pruebas de Android App Links (/.well-known/assetlinks.json), sin red."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, BOOTSTRAP_TOKEN="b" * 32, COOKIE_SECURE="false",
                  RATELIMIT_STORAGE_URI="memory://", ANDROID_CERT_SHA256="")
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
print("== assetlinks.json ==")
r = c.get("/.well-known/assetlinks.json")
j = r.get_json()
check("responde 200 sin sesión y sin redirigir", r.status_code == 200, r.status_code)
check("es JSON (Android lo exige)", r.mimetype == "application/json", r.mimetype)
check("autoriza a la app app.quincena", j[0]["target"] == {"namespace": "android_app", "package_name": "app.quincena", "sha256_cert_fingerprints": [A.ANDROID_CERT_SHA256]}, j)
check("permiso para abrir los enlaces", j[0]["relation"] == ["delegate_permission/common.handle_all_urls"])
check("la huella es la de la llave nueva", A.ANDROID_CERT_SHA256.startswith("74:35:96") and A.ANDROID_CERT_SHA256.endswith("D8:0A"))
os.environ["ANDROID_CERT_SHA256"] = "aa:bb, cc:dd"
fp = c.get("/.well-known/assetlinks.json").get_json()[0]["target"]["sha256_cert_fingerprints"]
check("ANDROID_CERT_SHA256 permite cambiar o sumar llaves", fp == ["AA:BB", "CC:DD"], fp)
check("la app vieja (okhttp sin versión) también lo puede leer", c.get("/.well-known/assetlinks.json", headers={"User-Agent": "okhttp/4.12.0"}).status_code == 200)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
