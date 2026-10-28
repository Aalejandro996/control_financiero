"""Pruebas de la tasa del dólar de Venezuela: BCV y dolarapi, gana la de fecha más reciente. Sin red."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, BOOTSTRAP_TOKEN="b" * 32, COOKIE_SECURE="false",
                  RATELIMIT_STORAGE_URI="memory://", API_DEFAULT_LIMIT="1000 per minute")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        fail += 1; print("  FALLA", name, extra)


def bcv_page(value, when, euro="250,75"):
    return (f'<div id="euro"><strong> {euro} </strong></div><div id="dolar" class="col-sm-12"><div class="field-content">'
            f'<div class="row recuadrotsmc"><div class="col-sm-6 col-xs-6"><span> USD</span></div><div class="col-sm-6 col-xs-6 centrado">'
            f'<strong> {value} </strong></div></div></div></div><div class="pull-right dinpro center">Fecha Valor: '
            f'<span class="date-display-single" property="dc:date" datatype="xsd:dateTime" content="{when}">Martes, 27 Octubre  2026</span></div>')


SRC = {}


def fake_json(url):
    if url.endswith("/euros"):
        if isinstance(SRC.get("euros"), (list, Exception)) and not isinstance(SRC["euros"], Exception):
            return SRC["euros"]
        raise OSError("sin euros")
    if isinstance(SRC.get("api"), Exception):
        raise SRC["api"]
    return SRC["api"]


CALLS = []


def fake_text(url, context=None):
    CALLS.append(context)
    bcv = SRC.get("bcv")
    if isinstance(bcv, Exception) and not (SRC.get("ssl_once") and context is not None):
        raise bcv
    if SRC.get("ssl_once") and context is not None:
        return SRC["ssl_once"]
    return bcv


G = A.fetch_rate.__globals__
G["_http_json"], G["_http_text"] = fake_json, fake_text
logs = []
A.app.logger.info = lambda msg, *a: logs.append(msg % a)
A.app.logger.error = lambda msg, *a: logs.append("ERROR " + (msg % a))


def fresh(api, bcv):
    A._RATE_CACHE.clear(); logs.clear()
    SRC.update(api=api, bcv=bcv)
    return A.fetch_rate("VE")


def api(value, when, par=60.0):
    return [{"fuente": "oficial", "promedio": value, "fechaActualizacion": when}, {"fuente": "paralelo", "promedio": par, "fechaActualizacion": when}]


print("== Página del BCV ==")
SRC["bcv"] = bcv_page("215,67890000", "2026-10-28T00:00:00-04:00")
v, eur, when = A._bcv_official()
check("lee la tasa con coma decimal", abs(v - 215.6789) < 1e-9, v)
check("lee también el euro BCV", eur == 250.75, eur)
check("lee la fecha valor", when.startswith("2026-10-28"), when)
SRC["bcv"] = bcv_page("1.215,50", "2026-10-28T00:00:00-04:00")
check("entiende el punto de miles", A._bcv_official()[0] == 1215.5)

print("== Certificado incompleto del BCV ==")
import ssl as _ssl, urllib.error as _ue
SRC.update(bcv=_ue.URLError(_ssl.SSLCertVerificationError("unable to get local issuer certificate")), ssl_once=bcv_page("230,10", "2026-10-29T00:00:00-04:00"))
A._BCV_CONTEXT = "contexto-con-intermedio"  # en producción se arma bajando el intermedio (AIA); aquí se simula
CALLS.clear()
v, eur, when = A._bcv_official()
check("si falla el certificado, reintenta con el intermedio y lee la tasa", v == 230.10 and CALLS == [None, "contexto-con-intermedio"], (v, CALLS))
SRC.pop("ssl_once"); A._BCV_CONTEXT = None
SRC["bcv"] = _ue.URLError(ConnectionResetError("caída"))
try:
    A._bcv_official(); otro = False
except _ue.URLError:
    otro = True
check("otros errores de red no intentan el intermedio", otro)

print("== Gana la fuente más reciente ==")
c = fresh(api(200.0, "2026-10-20T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
check("dolarapi atrasada: usa el BCV", c["rate"] == 215.5 and c["source"] == "bcv", c)
check("el paralelo sigue saliendo de dolarapi", c["parallel"] == 60.0)
c = fresh(api(216.0, "2026-10-29T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
check("dolarapi más nueva: usa dolarapi", c["rate"] == 216.0 and c["source"] == "dolarapi", c)
c = fresh(api(215.0, "2026-10-28T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
check("misma fecha: prefiere el BCV", c["rate"] == 215.5 and c["source"] == "bcv", c)

print("== Si una fuente falla ==")
c = fresh(OSError("caída"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
check("dolarapi caída: usa el BCV", c["rate"] == 215.5 and c["source"] == "bcv", c)
c = fresh(api(214.0, "2026-10-27T16:00:00.000Z"), OSError("certificado"))
check("BCV caído: usa dolarapi", c["rate"] == 214.0 and c["source"] == "dolarapi", c)
check("el registro dice qué falló", any("BCV falló" in line and "certificado" in line for line in logs), logs)
c = fresh(api(214.0, "2026-10-27T16:00:00.000Z"), "<html>sin tasa</html>")
check("página del BCV cambiada: usa dolarapi y lo registra", c["source"] == "dolarapi" and any("BCV falló" in line for line in logs), logs)
A._RATE_CACHE["VE"].update(fetched=0)
SRC.update(api=OSError("caída"), bcv=OSError("caída")); logs.clear()
c = A.fetch_rate("VE")
check("las dos caídas: conserva la última tasa", c["rate"] == 214.0)
check("y lo registra como error", any(line.startswith("ERROR Tasa VE sin actualizar") for line in logs), logs)
SRC.update(api=api(1e9, "2026-10-30T00:00:00Z"), bcv=bcv_page("0,00", "2026-10-30T00:00:00-04:00")); A._RATE_CACHE["VE"].update(fetched=0)
check("descarta tasas absurdas", A.fetch_rate("VE")["rate"] == 214.0)

print("== Formatos del certificado intermedio ==")
import datetime as _dt
from cryptography import x509 as _x509
from cryptography.x509.oid import NameOID as _N
from cryptography.hazmat.primitives import hashes as _h, serialization as _s
from cryptography.hazmat.primitives.asymmetric import ec as _ec
from cryptography.hazmat.primitives.serialization import pkcs7 as _p7
_k = _ec.generate_private_key(_ec.SECP256R1()); _n = _x509.Name([_x509.NameAttribute(_N.COMMON_NAME, "Intermedio de prueba")])
_now = _dt.datetime.now(_dt.timezone.utc)
_c = _x509.CertificateBuilder().subject_name(_n).issuer_name(_n).public_key(_k.public_key()).serial_number(1).not_valid_before(_now) \
    .not_valid_after(_now + _dt.timedelta(days=1)).sign(_k, _h.SHA256())
for _name, _raw in (("DER", _c.public_bytes(_s.Encoding.DER)), ("PEM", _c.public_bytes(_s.Encoding.PEM)),
                    ("PKCS#7 DER (.p7c, como el del BCV)", _p7.serialize_certificates([_c], _s.Encoding.DER)),
                    ("PKCS#7 PEM", _p7.serialize_certificates([_c], _s.Encoding.PEM))):
    check(f"lee el intermedio en {_name}", A._load_certs(_raw) == [_c])

print("== Euro y Actualizar ==")
c = fresh(api(200.0, "2026-10-20T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00", euro="248,30"))
check("guarda el euro BCV", c["euro"] == 248.3, c)
SRC["euros"] = [{"fuente": "oficial", "promedio": 249.9, "fechaActualizacion": "2026-10-28T12:00:00Z"}]
c = fresh(api(200.0, "2026-10-28T16:00:00.000Z"), OSError("caída"))
check("sin página del BCV, el euro sale de dolarapi", c["euro"] == 249.9, c)
SRC.pop("euros")
c = fresh(api(216.0, "2026-10-28T16:00:00.000Z"), OSError("caída"))
SRC.update(api=api(217.0, "2026-10-29T16:00:00.000Z"))
check("sin Actualizar, la caché de 30 minutos se respeta", A.fetch_rate("VE")["rate"] == 216.0)
check("Actualizar no consulta de nuevo antes de un minuto", A.fetch_rate("VE", force=True)["rate"] == 216.0)
A._RATE_CACHE["VE"]["fetched"] -= 61
check("Actualizar pasado un minuto trae la tasa nueva", A.fetch_rate("VE", force=True)["rate"] == 217.0)

print("== /api/rate ==")
fresh(api(200.0, "2026-10-20T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
cl = A.app.test_client()
cl.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"}, headers={"X-Forwarded-For": "198.51.100.7"})
j = cl.get("/api/rate?country=VE").get_json()
check("devuelve la tasa, su fecha y la fuente", j["rate"] == 215.5 and j["updated_at"].startswith("2026-10-28") and j["source"] == "bcv", j)
check("devuelve el euro BCV", j["euro"] == 250.75, j)
check("acepta force=1", cl.get("/api/rate?country=VE&force=1").status_code == 200)
check("sin sesión no se puede consultar", A.app.test_client().get("/api/rate?country=VE").status_code == 401)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
