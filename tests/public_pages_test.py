"""Buscadores, protección perimetral y páginas de error de la web (sin red)."""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="1000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="",
                  SMTP_PASSWORD="", APP_PUBLIC_ORIGIN="https://quincena.example")
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
print("== Buscadores ==")
r = c.get("/")
check("la portada se puede indexar", r.headers.get("X-Robots-Tag", "").startswith("index"), r.headers.get("X-Robots-Tag"))
check("la app no se indexa", "noindex" in c.get("/app").headers.get("X-Robots-Tag", ""))
robots = c.get("/robots.txt").get_data(as_text=True)
check("robots oculta /admin", "Disallow: /admin" in robots)
check("sitemap con lastmod", "<lastmod>" in c.get("/sitemap.xml").get_data(as_text=True))
check("security.txt", "Contact:" in c.get("/.well-known/security.txt").get_data(as_text=True))

print("== Rastreos ==")
for p in ("/.env", "/.git/config", "/wp-login.php", "/phpmyadmin/", "/backup.sql"):
    r = c.get(p); check(f"{p} → 404 corto", r.status_code == 404 and r.data == b"No encontrado")
check("assetlinks sigue publicado", c.get("/.well-known/assetlinks.json").status_code == 200)

print("== Páginas de error ==")
r = c.get("/no-existe"); h = r.get_data(as_text=True)
check("404 web con la marca", r.status_code == 404 and "Página no encontrada" in h and "Quincena VE" in h and r.headers["X-Robots-Tag"] == "noindex, nofollow")
r = c.get("/api/no-existe"); check("404 de la API sigue en JSON", r.status_code == 404 and r.is_json and r.get_json()["error"] == "No encontrado.")
r = c.delete("/terminos"); check("405 web con la marca", r.status_code == 405 and "Acción no permitida" in r.get_data(as_text=True))


def boom():
    raise RuntimeError("clave-secreta-de-la-base")


A.app.view_functions["legal_pages"] = boom
A.app.testing = False; A.app.config["PROPAGATE_EXCEPTIONS"] = False
r = c.get("/terminos"); h = r.get_data(as_text=True)
check("500 sin detalles internos y con referencia", r.status_code == 500 and "clave-secreta" not in h and re.search(r"<code>[0-9A-F]{8}</code>", h) is not None)
A.MAINTENANCE_MODE = True
r = c.get("/"); check("mantenimiento → 503 con Retry-After", r.status_code == 503 and r.headers.get("Retry-After") == "300")
check("healthz responde en mantenimiento", c.get("/healthz").status_code != 503)
A.MAINTENANCE_MODE = False

print(f"\n{ok} ok, {fail} fallas")
sys.exit(1 if fail else 0)
