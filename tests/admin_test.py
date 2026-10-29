"""Pruebas del panel de administración: BD, usuarios, cobros de Negocio Plus, factura en PDF por correo y Binance. Sin red."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="5000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="", SMTP_PASSWORD="",
                  BINANCE_API_KEY="", BINANCE_API_SECRET="")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        fail += 1; print("  FALLA", name, extra)


A.send_verification = lambda user: True
A.notify_security = lambda *a, **k: None
A.fetch_rate = lambda country="VE", force=False: {"rate": 200.0}
SENT = []
A.send_mail = lambda to, subject, body, html=None, attachments=None: SENT.append((to, subject, attachments)) or True


class C:
    def __init__(self):
        self.c, self.csrf = A.app.test_client(), None

    def req(self, method, path, body=None, ip="198.51.100.9", raw=False):
        h = {"X-Forwarded-For": ip, **({"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {})}
        r = self.c.open(path, method=method, json=body, headers=h)
        if raw:
            return r
        j = r.get_json(silent=True) or {}
        if j.get("csrf_token"):
            self.csrf = j["csrf_token"]
        return r.status_code, j


admin = C()
admin.req("POST", "/api/setup", {"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"})
ana = C()
ana.req("POST", "/api/register", {"username": "ana", "email": "ana@x.com", "password": "Zorro-Plata-55231x", "accept_terms": True, "business_name": "Bodega Ana"}, ip="198.51.100.20")

print("== Acceso ==")
check("/admin manda a la app a quien no es admin", ana.req("GET", "/admin", raw=True).status_code == 302)
check("/admin abre para el admin", admin.req("GET", "/admin", raw=True).status_code == 200)
for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/payments", "/api/admin/settings", "/api/admin/binance"):
    check(f"{path} es solo para admin", ana.req("GET", path)[0] == 403)

print("== Resumen y usuarios ==")
code, o = admin.req("GET", "/api/admin/overview")
check("resumen: 1 cuenta con negocio, sin Plus", code == 200 and o["users"]["total"] == 1 and o["users"]["business"] == 1 and o["users"]["plus"] == 0, o)
check("resumen: tablas con filas", any(t["name"] == "user" and t["rows"] == 2 for t in o["database"]["tables"]), o["database"])
code, u = admin.req("GET", "/api/admin/users")
row = next(x for x in u["users"] if x["username"] == "ana")
check("usuarios: correo, negocio y sin confirmar", row["email"] == "ana@x.com" and row["business"] == "Bodega Ana" and row["verified"] is False, row)
check("reenviar confirmación", admin.req("POST", f"/api/admin/users/{row['id']}/resend-verification") == (200, {"ok": True, "sent": True}))

print("== Datos de facturación ==")
code, s = admin.req("GET", "/api/admin/settings")
check("el emisor por defecto es Gigadevs con su RIF, IVA 16% e IGTF 3%", s["billing"]["iva_pct"] == 16 and s["billing"]["igtf_pct"] == 3 and s["billing"]["issuer_name"] == "Gigadevs" and s["billing"]["issuer_rif"] == "27344270-3" and "Los Teques" in s["billing"]["issuer_address"], s)
check("rechaza un IVA fuera de rango", admin.req("PUT", "/api/admin/settings", {"billing": {"iva_pct": 300}})[0] == 400)
code, s = admin.req("PUT", "/api/admin/settings", {"billing": {"plus_price_usd": "10", "series": "B-1"}})
check("guarda precio, impuestos y serie limpia", code == 200 and s["billing"]["plus_price_usd"] == 10 and s["billing"]["series"] == "B1", s)

print("== Cobro de Negocio Plus ==")
body = {"username": "ana", "method": "pago_movil", "reference": "123456", "days": 30,
        "client": {"name": "Ana Pérez", "doc": "V-1234567", "address": "Caracas", "phone": "0414", "email": "ana@x.com"}}
check("rechaza un usuario que no existe", admin.req("POST", "/api/admin/payments", {**body, "username": "nadie"})[0] == 404)
check("rechaza una forma de pago desconocida", admin.req("POST", "/api/admin/payments", {**body, "method": "cripto"})[0] == 400)
code, r = admin.req("POST", "/api/admin/payments", body)
p = r.get("payment", {})
check("crea la factura B1-00000001 con el precio guardado", code == 201 and p["doc"] == "B1-00000001" and p["amount_usd"] == 10, r)
check("pago móvil (Bs.): base 2.000 + IVA 16% = Bs 2.320, sin IGTF", p["total_bs"] == 2320 and p["igtf_bs"] == 0 and p["total"] == 11.6, p)
check("se envió por correo con el PDF adjunto", r["emailed"] and SENT[-1][0] == "ana@x.com" and SENT[-1][2][0][1][:4] == b"%PDF" and SENT[-1][2][0][0].endswith(".pdf"), SENT[-1][:2])
code, st = ana.req("GET", "/api/status")
check("la cuenta quedó con Plus", st["user"]["plan"] == "plus", st["user"])
code, r2 = admin.req("POST", "/api/admin/payments", {**body, "days": 10})
check("un segundo cobro alarga el Plus desde donde terminaba", r2["payment"]["period_from"][:16] == p["period_to"][:16] and r2["payment"]["number"] == 2, r2)
pdf = admin.req("GET", f"/api/admin/payments/{p['id']}/pdf", raw=True)
check("PDF descargable", pdf.status_code == 200 and pdf.mimetype == "application/pdf" and pdf.data[:4] == b"%PDF")
check("factura de muestra", admin.req("GET", "/api/admin/sample-invoice", raw=True).data[:4] == b"%PDF")
check("reenviar a otro correo", admin.req("POST", f"/api/admin/payments/{p['id']}/email", {"email": "otro@x.com"})[0] == 200 and SENT[-1][0] == "otro@x.com")

print("== Anular ==")
code, v = admin.req("POST", f"/api/admin/payments/{r2['payment']['id']}/void", {"reason": "error"})
check("anula y el Plus vuelve a terminar donde terminaba antes", code == 200 and v["payment"]["void_at"])
with A.app.app_context():
    user = A.User.query.filter_by(username="ana").first()
    check("plan_until recortado", A._aware(user.plan_until).isoformat()[:16] == p["period_to"][:16], user.plan_until)
check("no se anula dos veces", admin.req("POST", f"/api/admin/payments/{r2['payment']['id']}/void", {})[0] == 400)
code, lst = admin.req("GET", "/api/admin/payments")
check("lista de cobros, la más nueva primero", [x["number"] for x in lst["payments"]] == [2, 1])
code, o = admin.req("GET", "/api/admin/overview")
check("el resumen cuenta solo lo no anulado", o["income"]["total_count"] == 1 and o["income"]["total_usd"] == 11.6, o["income"])

print("== Binance ==")
code, j = admin.req("GET", "/api/admin/binance")
check("sin claves avisa qué falta", code == 400 and "BINANCE_API_KEY" in j["error"], j)
os.environ.update(BINANCE_API_KEY="k", BINANCE_API_SECRET="s")
calls = []


def fake_get(path, params):
    calls.append(path)
    if "pay" in path:
        return {"data": [{"transactionId": "T1", "transactionTime": 1700000000000, "amount": "11.95", "currency": "USDT", "payerInfo": {"name": "Ana"}},
                         {"transactionId": "T2", "transactionTime": 1700000001000, "amount": "5", "currency": "BTC"}]}
    return [{"txId": "0xabc", "insertTime": 1700000002000, "amount": "20", "coin": "USDT", "status": 1, "network": "TRX"}]


A.binance_get = fake_get
code, j = admin.req("GET", "/api/admin/binance")
check("lista ingresos USDT de Pay y depósitos (sin otras monedas)", code == 200 and [i["id"] for i in j["items"]] == ["dep:0xabc", "pay:T1"], j)
code, r3 = admin.req("POST", "/api/admin/payments", {**body, "method": "binance", "binance_tx": "pay:T1"})
check("cobro con un pago de Binance", code == 201 and r3["payment"]["binance_tx"] == "pay:T1")
check("en divisas lleva IGTF 3% aparte: 2.320 + 69,60 = Bs 2.389,60", r3["payment"]["igtf_bs"] == 69.6 and r3["payment"]["total_bs"] == 2389.6 and r3["payment"]["total"] == 11.95, r3["payment"])
check("el mismo pago de Binance no se usa dos veces", admin.req("POST", "/api/admin/payments", {**body, "binance_tx": "pay:T1"})[0] == 409)
code, j = admin.req("GET", "/api/admin/binance")
check("Binance marca el pago como ya usado", next(i for i in j["items"] if i["id"] == "pay:T1")["used"] is True)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
