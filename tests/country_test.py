"""Pruebas de país, moneda local, tasa por país e ingreso semanal (sin red)."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="1000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="", SMTP_PASSWORD="")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        fail += 1; print("  FALLA", name, extra)


A.send_verification = lambda user: False
calls = []


def fake_http(url):
    calls.append(url)
    if "open.er-api" in url:
        return {"rates": {"COP": 4123.5, "CLP": 951.2, "PEN": 3.71, "MXN": 18.4}}
    if "ar.dolarapi" in url:
        return [{"fuente": "oficial", "venta": 1400}, {"fuente": "blue", "venta": 1500}]
    return [{"fuente": "oficial", "promedio": 45.5}, {"fuente": "paralelo", "promedio": 50.1}]


A.fetch_rate.__globals__["_http_json"] = fake_http
A.fetch_rate.__globals__["_http_text"] = lambda url: (_ for _ in ()).throw(OSError("sin red en las pruebas"))
cl = A.app.test_client()
H = {"X-Forwarded-For": "198.51.100.7"}
cl.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"}, headers=H)
cl.get("/api/logout")


def register(name, country, ip):
    c = A.app.test_client()
    r = c.post("/api/register", json={"username": name, "email": f"{name}@x.com", "password": "Zorro-Plata-55231x", "accept_terms": True, **({"country": country} if country else {})}, headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.get_json()
    return c, r.get_json()["csrf_token"]


print("== País y moneda ==")
for i, (code, rate) in enumerate([("CO", 4000.0), ("CL", 950.0), ("PE", 3.7), ("VE", 40.0), (None, 40.0), ("ZZ", 40.0)]):
    c, csrf = register(f"user{i}", code, f"198.51.100.{20+i}")
    d = c.get("/api/state").get_json()["data"]
    check(f"registro con país {code}: queda {d['country']} y tasa inicial {d['exchangeRate']}", d["country"] == (code if code in A.COUNTRIES else "VE") and d["exchangeRate"] == rate)
check("un usuario viejo sin país se normaliza a Venezuela", A.normalize_shared_data({"exchangeRate": 55})["country"] == "VE" and A.normalize_shared_data({"exchangeRate": 55})["exchangeRate"] == 55)
check("un país inválido cae a Venezuela", A.normalize_shared_data({"country": "<script>"})["country"] == "VE")
check("sin tasa válida usa la tasa por defecto del país", A.normalize_shared_data({"country": "CO", "exchangeRate": -1})["exchangeRate"] == 4000.0)

print("== Cambiar de país ==")
c, csrf = register("cambio", "VE", "198.51.100.40")
st = c.get("/api/state").get_json()
new = dict(st["data"], country="PE", exchangeRate=3.71)
r = c.put("/api/state", json={"data": new, "base_data": st["data"], "base_revision": st["revision"]}, headers={"X-CSRF-Token": csrf, "X-Forwarded-For": "198.51.100.40"})
check("el país se puede cambiar y se guarda", r.status_code == 200 and c.get("/api/state").get_json()["data"]["country"] == "PE", r.get_json())

print("== Ingreso semanal ==")
st = c.get("/api/state").get_json()
rows = [{"id": "s1", "kind": "sueldo", "name": "Semana", "amount": 100, "currency": "BS", "frequency": "semanal", "payDays": [5]},
        {"id": "s2", "kind": "sueldo", "name": "Raro", "amount": 100, "currency": "USD", "frequency": "semanal", "payDays": [30]},
        {"id": "s3", "kind": "sueldo", "name": "Vacío", "amount": 100, "currency": "USD", "frequency": "semanal", "payDays": []}]
new = dict(st["data"], incomeSources=rows)
r = c.put("/api/state", json={"data": new, "base_data": st["data"], "base_revision": st["revision"]}, headers={"X-CSRF-Token": csrf, "X-Forwarded-For": "198.51.100.40"})
got = {x["id"]: x for x in c.get("/api/state").get_json()["data"]["incomeSources"]}
check("ingreso semanal guarda su día de cobro", got["s1"]["frequency"] == "semanal" and got["s1"]["payDays"] == [5])
check("un día fuera de 1–7 se corrige a viernes", got["s2"]["payDays"] == [5] and got["s3"]["payDays"] == [5])

print("== Tasa por país ==")
for code, expect in [("CO", 4123.5), ("CL", 951.2), ("PE", 3.71), ("MX", 18.4), ("VE", 45.5), ("AR", 1400.0)]:
    j = c.get(f"/api/rate?country={code}").get_json()
    check(f"tasa {code} = {expect}", j.get("rate") == expect, j)
check("Venezuela trae tasa paralela", c.get("/api/rate?country=VE").get_json()["parallel"] == 50.1)
check("los demás países no traen paralela (ni Argentina)", all(c.get(f"/api/rate?country={x}").get_json()["parallel"] is None for x in ("CO", "CL", "PE", "MX", "AR")))
check("sin país usa Venezuela (apps viejas)", c.get("/api/rate").get_json()["rate"] == 45.5)
n = len(calls); c.get("/api/rate?country=CO"); check("la tasa se guarda en caché 30 min", len(calls) == n)
check("lista de países pública", len(A.app.test_client().get("/api/countries").get_json()["countries"]) == 6)
check("país inyectado no llega a la URL", c.get("/api/rate?country=../../x").get_json()["rate"] == 45.5)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
