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


def bcv_page(value, when):
    return (f'<div id="euro"><strong> 50,00 </strong></div><div id="dolar" class="col-sm-12"><div class="field-content">'
            f'<div class="row recuadrotsmc"><div class="col-sm-6 col-xs-6"><span> USD</span></div><div class="col-sm-6 col-xs-6 centrado">'
            f'<strong> {value} </strong></div></div></div></div><div class="pull-right dinpro center">Fecha Valor: '
            f'<span class="date-display-single" property="dc:date" datatype="xsd:dateTime" content="{when}">Martes, 27 Octubre  2026</span></div>')


SRC = {}


def fake_json(url):
    if isinstance(SRC.get("api"), Exception):
        raise SRC["api"]
    return SRC["api"]


def fake_text(url):
    if isinstance(SRC.get("bcv"), Exception):
        raise SRC["bcv"]
    return SRC["bcv"]


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
v, when = A._bcv_official()
check("lee la tasa con coma decimal", abs(v - 215.6789) < 1e-9, v)
check("lee la fecha valor", when.startswith("2026-10-28"), when)
SRC["bcv"] = bcv_page("1.215,50", "2026-10-28T00:00:00-04:00")
check("entiende el punto de miles", A._bcv_official()[0] == 1215.5)

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

print("== /api/rate ==")
fresh(api(200.0, "2026-10-20T16:00:00.000Z"), bcv_page("215,50", "2026-10-28T00:00:00-04:00"))
cl = A.app.test_client()
cl.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"}, headers={"X-Forwarded-For": "198.51.100.7"})
j = cl.get("/api/rate?country=VE").get_json()
check("devuelve la tasa, su fecha y la fuente", j["rate"] == 215.5 and j["updated_at"].startswith("2026-10-28") and j["source"] == "bcv", j)
check("sin sesión no se puede consultar", A.app.test_client().get("/api/rate?country=VE").status_code == 401)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
