"""Pruebas de seguridad del servidor (sin red, con SQLite temporal).

Ejecutar:  python3 tests/security_test.py
Cubre: inyección SQL, recuperación de clave, correos de seguridad, sesiones, límites de uso, cabeceras y errores.
"""
import json
import os
import sys
import tempfile
import time
import urllib.error

tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
    COOKIE_SECURE="false", APP_PUBLIC_ORIGIN="https://quincena.example", BREVO_API_KEY="", API_DEFAULT_LIMIT="1000 per minute",
    SMTP_USER="", SMTP_PASSWORD="", RATELIMIT_STORAGE_URI="memory://",
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as A  # noqa: E402

PASS, FAIL = 0, []


@A.app.get("/api/_boom")
@A.limiter.exempt
def _boom():
    raise RuntimeError("secreto-interno-DB-password")



def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL.append(name)
        print("  FALLA", name, extra)


REAL_SEND = A.send_mail
sent = []  # correos "enviados"
A.send_mail = lambda to, subject, body, html_body=None: sent.append((to, subject, body)) or True
os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"] = "u@x.com", "p"  # que mail_configured() sea verdadero


def wait_mail(n, secs=2.0):
    t = time.time()
    while len(sent) < n and time.time() - t < secs:
        time.sleep(0.02)


_ip = [10]


def new_ip():
    _ip[0] += 1
    return f"203.0.113.{_ip[0]}"


class C:
    """Cliente con su propia sesión y su propia IP."""
    def __init__(self):
        self.c = A.app.test_client()
        self.ip = new_ip()
        self.csrf = None

    def req(self, method, url, body=None, ip=None, headers=None):
        h = {"X-Forwarded-For": ip or self.ip, **(headers or {})}
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        r = self.c.open(url, method=method, json=body, headers=h)
        try:
            j = r.get_json(silent=True)
        except Exception:
            j = None
        if isinstance(j, dict) and j.get("csrf_token"):
            self.csrf = j["csrf_token"]
        return r.status_code, j or {}, r


def setup_admin():
    c = C()
    s, j, _ = c.req("POST", "/api/setup", {"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"})
    assert s == 200, (s, j)
    return c


print("== Inyección SQL y entradas hostiles ==")
admin = setup_admin()
payloads = ["' OR '1'='1", "admin'--", "\" OR \"\"=\"", "'; DROP TABLE user;--", "admin' UNION SELECT 1,2,3,4--", "%", "\\", "\x00", "a" * 5000]
c = C()
codes = []
for i, pl in enumerate(payloads):
    s, j, _ = c.req("POST", "/api/login", {"username": pl, "password": pl}, ip=new_ip())
    codes.append(s)
check("login con cargas SQL → 401/400 y nunca 500", all(x in (400, 401) for x in codes), codes)
s, j, _ = c.req("POST", "/api/forgot-password", {"email": "x@y.com' OR 1=1--"}, ip=new_ip())
check("forgot con carga SQL → respuesta normal", s == 200 and j == {"ok": True})
s, j, _ = c.req("POST", "/api/register", {"username": "bob'; DROP TABLE user;--", "email": "b@b.com", "password": "Clave-Bob-71829x", "accept_terms": True}, ip=new_ip())
check("registro con usuario malicioso → rechazado", s == 400, (s, j))
s, j, _ = admin.req("GET", "/api/users")
check("la tabla user sigue existiendo", s == 200 and j["users"][0]["username"] == "admin")

print("== Login: no se adivinan usuarios ni se guardan claves pegadas por error ==")
c = C()
s1, j1, _ = c.req("POST", "/api/login", {"username": "admin", "password": "incorrecta-123"}, ip=new_ip())
s2, j2, _ = c.req("POST", "/api/login", {"username": "no-existe", "password": "incorrecta-123"}, ip=new_ip())
check("mismo mensaje exista o no el usuario", s1 == s2 == 401 and j1 == j2)
secret_typed = "MiClaveSecreta-QWERTY-4411"
c.req("POST", "/api/login", {"username": secret_typed, "password": "x"}, ip=new_ip())
s, j, _ = admin.req("GET", "/api/audit-log")
check("la auditoría no guarda lo escrito como usuario inexistente", secret_typed not in json.dumps(j))
t0 = time.time(); c.req("POST", "/api/login", {"username": "no-existe", "password": "x" * 20}, ip=new_ip()); t_no = time.time() - t0
t0 = time.time(); c.req("POST", "/api/login", {"username": "admin", "password": "x" * 20}, ip=new_ip()); t_yes = time.time() - t0
check("tiempo parecido con usuario real e inexistente", t_no > t_yes * 0.4, (round(t_no, 3), round(t_yes, 3)))
s, j, _ = c.req("POST", "/api/login", {"username": "admin", "password": "x" * 5000}, ip=new_ip())
check("clave gigante se rechaza sin gastar CPU", s == 401)
c = C()
codes = [c.req("POST", "/api/login", {"username": "victima", "password": "mal"}, ip=new_ip())[0] for _ in range(32)]
check("30 intentos por hora al mismo usuario desde IPs distintas → 429", codes[:30].count(401) == 30 and codes[30] == 429, codes[28:])
s, j, _ = c.req("POST", "/api/login", {"username": "victima", "password": "mal"}, ip=new_ip())
check("el 429 llega como JSON en español", s == 429 and "Demasiados" in j.get("error", ""))

print("== Registro, confirmación de correo y recuperación de clave ==")
sent.clear()
u = C()
s, j, _ = u.req("POST", "/api/register", {"username": "ana", "email": "Ana@Correo.com", "password": "Zorro-Plata-55231x", "accept_terms": True})
check("registro correcto", s == 200, (s, j))
wait_mail(1)
vmail = next((m for m in sent if "Confirme" in m[1]), None)
check("correo de confirmación enviado", vmail is not None)
check("el enlace de confirmación usa # (no llega al servidor)", vmail and "/app#verify=" in vmail[2] and "?verify=" not in vmail[2])
vtoken = vmail[2].split("#verify=")[1].split()[0] if vmail else ""
s, j, _ = u.req("POST", "/api/verify-email", {"token": vtoken})
check("confirma el correo", s == 200)
s, j, _ = u.req("POST", "/api/verify-email", {"token": vtoken})
check("el enlace de confirmación no sirve dos veces", s == 400)

sent.clear()
f = C()
s, j, _ = f.req("POST", "/api/forgot-password", {"email": "desconocido@correo.com"})
unk = (s, j)
s, j, _ = f.req("POST", "/api/forgot-password", {"email": "ana@correo.com"})
check("misma respuesta con correo conocido y desconocido", unk == (s, j) == (200, {"ok": True}), (unk, s, j))
wait_mail(1)
check("solo se envía correo al correo real", len(sent) == 1 and sent[0][0] == "ana@correo.com", sent)
rmail = sent[0][2] if sent else ""
check("el enlace de recuperación usa # y el origen configurado", "https://quincena.example/app#reset=" in rmail and "?reset=" not in rmail)
rtoken = rmail.split("#reset=")[1].split()[0] if rmail else ""
for _ in range(2):
    f.req("POST", "/api/forgot-password", {"email": "ana@correo.com"}, ip=new_ip())
s, j, _ = f.req("POST", "/api/forgot-password", {"email": "ana@correo.com"}, ip=new_ip())
check("máximo 3 correos por hora a una misma dirección", s == 429)
wait_mail(3)
check("al pedir otro enlace, el anterior deja de servir", f.req("POST", "/api/reset-password", {"token": rtoken, "new_password": "Zorro-Plata-00000q"}, ip=new_ip())[0] == 400)
rmail = [m for m in sent if "#reset=" in m[2]][-1][2]
rtoken = rmail.split("#reset=")[1].split()[0]

print("== El enlace se arma con el origen configurado, no con la cabecera Host ==")
with A.app.test_request_context("/", headers={"Host": "sitio-falso.example", "X-Forwarded-Host": "sitio-falso.example"}):
    check("con APP_PUBLIC_ORIGIN ignora el Host del atacante", A.public_origin() == "https://quincena.example")
    old = os.environ.pop("APP_PUBLIC_ORIGIN"); os.environ["RENDER"] = "1"
    check("en producción sin origen configurado no inventa uno", A.public_origin() == "")
    del os.environ["RENDER"]; os.environ["APP_PUBLIC_ORIGIN"] = old

print("== Sesiones: cambiar o restablecer la clave cierra las demás ==")
sent.clear()
a1, a2 = C(), C()
a1.req("POST", "/api/login", {"username": "ana", "password": "Zorro-Plata-55231x"}); a2.req("POST", "/api/login", {"username": "ana", "password": "Zorro-Plata-55231x"})
check("las dos sesiones web funcionan", a1.req("GET", "/api/state")[0] == 200 and a2.req("GET", "/api/state")[0] == 200)
s, j, _ = a1.req("POST", "/api/change-password", {"old_password": "Zorro-Plata-55231x", "new_password": "Otro-Zorro-Plata-9034y"})
check("cambio de clave correcto", s == 200, (s, j))
check("la sesión que cambió la clave sigue abierta", a1.req("GET", "/api/state")[0] == 200)
check("la otra sesión web se cerró", a2.req("GET", "/api/state")[0] == 401)
wait_mail(1)
check("llegó el aviso de cambio de contraseña", any("cambiada" in m[1] for m in sent))

mob = C()
s, j, _ = mob.req("POST", "/api/mobile/login", {"username": "ana", "password": "Otro-Zorro-Plata-9034y", "device": "Pixel <script>"})
tok = j.get("token")
wait_mail(2)
check("la app móvil avisa por correo del dispositivo nuevo", any("Nuevo inicio" in m[1] for m in sent))
bearer = {"Authorization": f"Bearer {tok}"}
check("el token móvil funciona", C().req("GET", "/api/state", headers=bearer)[0] == 200)
sent.clear()
s, j, _ = f.req("POST", "/api/reset-password", {"token": rtoken, "new_password": "Tercer-Zorro-Plata-7712z"}, ip=new_ip())
check("restablecer con el enlace funciona", s == 200, (s, j))
s, j, _ = f.req("POST", "/api/reset-password", {"token": rtoken, "new_password": "Cuarto-Zorro-Plata-8823z"}, ip=new_ip())
check("el enlace de recuperación no sirve dos veces", s == 400)
check("restablecer cierra la app en los teléfonos", C().req("GET", "/api/state", headers=bearer)[0] == 401)
check("la sesión web de antes tampoco sirve", a1.req("GET", "/api/state")[0] == 401)
wait_mail(1)
check("llegó el aviso de restablecimiento", any("restablecida" in m[1] for m in sent))
s, j, _ = f.req("POST", "/api/reset-password", {"token": "inventado", "new_password": "Quinto-Zorro-Plata-1234z"}, ip=new_ip())
check("un código inventado se rechaza", s == 400)
s, j, _ = f.req("POST", "/api/reset-password", {"token": rtoken, "new_password": "corta"}, ip=new_ip())
check("una clave débil se rechaza", s == 400)

print("== Permisos: nadie ve datos de otra persona ni usa funciones de admin ==")
b = C()
b.req("POST", "/api/register", {"username": "beto", "email": "beto@correo.com", "password": "Zorro-Cobre-33145x", "accept_terms": True})
check("un miembro no lista usuarios", b.req("GET", "/api/users")[0] == 403)
check("un miembro no ve la auditoría", b.req("GET", "/api/audit-log")[0] == 403)
check("un miembro no crea usuarios", b.req("POST", "/api/users", {"username": "x1x1x", "role": "admin"})[0] == 403)
check("un miembro no restablece claves ajenas", b.req("POST", "/api/users/1/reset-password")[0] == 403)
check("sin sesión no se ve el estado", C().req("GET", "/api/state")[0] == 401)
check("sin sesión no se ven los dispositivos", C().req("GET", "/api/devices")[0] == 401)
s, j, _ = b.req("PUT", "/api/state", {"data": {"expenses": [{"id": "a1", "amount": 5, "description": "<img src=x onerror=alert(1)>", "date": "2026-10-01", "currency": "USD"}]}, "base_data": {}, "base_revision": 0})
mine = b.req("GET", "/api/state")[1]["data"]
admin_state = admin.req("GET", "/api/state")[1]["data"]
check("los datos de beto no aparecen en la cuenta del admin", admin_state["expenses"] == [] and len(mine["expenses"]) == 1)
check("las cabeceras de CSRF se exigen", C().req("POST", "/api/logout")[0] == 401)
b2 = C(); b2.req("POST", "/api/login", {"username": "beto", "password": "Zorro-Cobre-33145x"}); b2.csrf = "falso"
check("POST sin token CSRF válido → 403", b2.req("POST", "/api/devices/revoke-others")[0] == 403)
s, j, _ = admin.req("POST", "/api/users/%d/reset-password" % 2)
check("el admin restablece la clave de otro y se cierran sus sesiones de app", s == 200 and C().req("GET", "/api/state", headers=bearer)[0] == 401)

print("== Cabeceras y errores ==")
r = C().req("GET", "/api/status")[2]
for h in ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy", "Referrer-Policy", "Permissions-Policy", "Cross-Origin-Opener-Policy"):
    check(f"cabecera {h}", h in r.headers)
check("las respuestas de la API no se guardan en caché", "no-store" in r.headers.get("Cache-Control", ""))
check("no hay CORS abierto", "Access-Control-Allow-Origin" not in r.headers)
s, j, r = C().req("GET", "/api/no-existe")
check("404 de la API en JSON", s == 404 and j.get("error") == "No encontrado.")
s, j, r = C().req("DELETE", "/api/status")
check("405 de la API en JSON", s == 405 and "error" in j)


s, j, r = C().req("GET", "/api/_boom")
check("un error interno no muestra detalles", s == 500 and "secreto-interno" not in r.get_data(as_text=True) and "Traceback" not in r.get_data(as_text=True))

print("== Brevo (API de correo por HTTPS) ==")
calls = []


class FakeResp:
    def __init__(self, status=201): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_urlopen(req, timeout=0):
    calls.append(req)
    if FAKE["error"]:
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    return FakeResp()


FAKE = {"error": False}
real_urlopen = A.urllib.request.urlopen
A.urllib.request.urlopen = fake_urlopen
A.send_mail = REAL_SEND  # la función real, con la red simulada
os.environ["BREVO_API_KEY"] = "xkeysib-TEST-1234567890"
os.environ["MAIL_FROM"] = "Quincena <avisos@correo.com>"
ok = A.send_mail("ana@correo.com", "Asunto", "Texto", "<p>Hola</p>")
req = calls[-1]
body = json.loads(req.data)
check("Brevo: se envía por HTTPS a la API oficial", ok and req.full_url == "https://api.brevo.com/v3/smtp/email" and req.get_method() == "POST")
check("Brevo: la clave va en la cabecera api-key, no en la URL ni el cuerpo", req.get_header("Api-key") == "xkeysib-TEST-1234567890" and "xkeysib" not in req.full_url + req.data.decode())
check("Brevo: remitente, destinatario y contenido correctos", body["sender"] == {"name": "Quincena", "email": "avisos@correo.com"} and body["to"] == [{"email": "ana@correo.com"}] and body["htmlContent"] == "<p>Hola</p>")
FAKE["error"] = True
check("Brevo: un rechazo (401) devuelve False sin romper", A.send_mail("ana@correo.com", "x", "y") is False)
FAKE["error"] = False
os.environ["MAIL_FROM"] = ""
n = len(calls)
check("Brevo: sin remitente verificado no envía", A.send_mail("ana@correo.com", "x", "y") is False and len(calls) == n)
A.urllib.request.urlopen = real_urlopen
os.environ["BREVO_API_KEY"] = ""

print("== Límite general de uso ==")
A.limiter.limit  # noqa
import subprocess  # noqa: E402
code = r"""
import os, sys, tempfile
t = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{t}/l.db", APP_SECRET_KEY="y"*48, API_DEFAULT_LIMIT="5 per minute", COOKIE_SECURE="false")
sys.path.insert(0, sys.argv[1])
import app as A
c = A.app.test_client()
r = [c.get("/api/status").status_code for _ in range(7)]
h = [c.get("/healthz").status_code for _ in range(12)]
i = [c.get("/").status_code for _ in range(12)]
print(r, set(h), set(i))
"""
out = subprocess.run([sys.executable, "-I", "-c", code, os.path.join(os.path.dirname(__file__), "..")], capture_output=True, text=True).stdout.strip()
check("el límite general corta la API tras superarlo", "429" in out.split("]")[0], out)
check("healthz y la página principal quedan exentos", out.endswith("{200}, {200}") or "{200} {200}" in out, out)

print()
print(f"{PASS} pruebas correctas, {len(FAIL)} con falla")
if FAIL:
    print("FALLAS:", FAIL)
    sys.exit(1)
