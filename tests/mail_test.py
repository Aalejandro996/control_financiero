"""Pruebas del diagnóstico de correo (Brevo) y de /api/mail-test, sin red: Brevo se simula."""
import io
import json
import os
import sys
import tempfile
import urllib.error

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="1000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="",
                  SMTP_PASSWORD="", MAIL_FROM="", APP_PUBLIC_ORIGIN="")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        fail += 1; print("  FALLA", name, extra)


sent = []


class Answer(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def brevo_ok(req, timeout=10):
    sent.append((req.full_url, req.headers, json.loads(req.data)))
    return Answer(b'{"messageId":"<abc@brevo>"}')


def brevo_rejects(req, timeout=10):
    sent.append((req.full_url, req.headers, json.loads(req.data)))
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"code":"unauthorized","message":"Key not found"}'))


logs = []
A.app.logger.error = lambda msg, *args: logs.append(msg % args if args else msg)

print("== mail_problem dice qué falta ==")
check("sin clave: falta BREVO_API_KEY", "BREVO_API_KEY" in (A.mail_problem() or ""))
os.environ["BREVO_API_KEY"] = "xkeysib-secreta"
check("con clave y sin remitente: falta MAIL_FROM", "MAIL_FROM" in (A.mail_problem() or ""))
os.environ["MAIL_FROM"] = "Quincena VE <gigadevsc@gmail.com>"
os.environ["RENDER"] = "true"
check("en Render sin dirección pública: falta APP_PUBLIC_ORIGIN", "APP_PUBLIC_ORIGIN" in (A.mail_problem() or ""))
os.environ["APP_PUBLIC_ORIGIN"] = "https://control-financiero-wrm5.onrender.com"
check("con todo configurado: sin problema", A.mail_problem() is None)

print("== Envío por Brevo ==")
A.urllib.request.urlopen = brevo_ok
check("send_mail devuelve True si Brevo acepta", A.send_mail("ana@x.com", "Hola", "texto") is True)
url, headers, payload = sent[-1]
check("usa la API transaccional /v3/smtp/email", url == "https://api.brevo.com/v3/smtp/email")
check("manda la clave en la cabecera api-key", headers.get("Api-key") == "xkeysib-secreta", headers)
check("remitente sale de MAIL_FROM", payload["sender"] == {"name": "Quincena VE", "email": "gigadevsc@gmail.com"})
A.urllib.request.urlopen = brevo_rejects
check("send_mail devuelve False si Brevo rechaza", A.send_mail("ana@x.com", "Hola", "texto") is False)
check("el registro trae el mensaje exacto de Brevo", any("Key not found" in line and "401" in line for line in logs), logs)
check("el registro nunca trae la clave", not any("xkeysib-secreta" in line for line in logs))

print("== /api/mail-test ==")
os.environ.pop("RENDER")
A.send_verification = lambda user: False
admin = A.app.test_client()
csrf = admin.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"},
                  headers={"X-Forwarded-For": "198.51.100.7"}).get_json()["csrf_token"]
admin_post = lambda path, body: admin.post(path, json=body, headers={"X-CSRF-Token": csrf})
check("sin sesión: 401", A.app.test_client().post("/api/mail-test", json={"to": "a@x.com"}).status_code == 401)
member = A.app.test_client()
member.post("/api/register", json={"username": "ivan", "email": "ivan@x.com", "password": "Zorro-Plata-55231x", "accept_terms": True},
            headers={"X-Forwarded-For": "198.51.100.28", "X-App-Version": "100000"})
check("un miembro: 403", member.post("/api/mail-test", json={"to": "a@x.com"}).status_code == 403)
check("correo inválido: 400", admin_post("/api/mail-test", {"to": "no-es-correo"}).status_code == 400)
A.urllib.request.urlopen = brevo_ok
r = admin_post("/api/mail-test", {"to": "prueba@x.com"})
j = r.get_json()
check("admin con Brevo bien: 200 y enviado", r.status_code == 200 and j["sent"] is True, j)
check("devuelve la configuración detectada", j["config"]["brevo_api_key"] is True and j["config"]["mail_from"] == "Quincena VE <gigadevsc@gmail.com>"
      and j["config"]["app_public_origin"] == "https://control-financiero-wrm5.onrender.com" and j["config"]["problem"] is None, j)
check("devuelve el messageId de Brevo", "<abc@brevo>" in j["detail"], j)
check("la respuesta nunca trae la clave", "xkeysib-secreta" not in r.get_data(as_text=True))
A.urllib.request.urlopen = brevo_rejects
r = admin_post("/api/mail-test", {"to": "prueba@x.com"})
j = r.get_json()
check("admin con Brevo rechazando: 502 con el motivo", r.status_code == 502 and j["sent"] is False and "Key not found" in j["detail"], j)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
