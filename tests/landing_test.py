"""Pruebas de la página de presentación (/), la app en /app y lo que leen los buscadores (sin red)."""
import json
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/t.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true", BOOTSTRAP_TOKEN="b" * 32,
                  COOKIE_SECURE="false", API_DEFAULT_LIMIT="1000 per minute", RATELIMIT_STORAGE_URI="memory://", BREVO_API_KEY="", SMTP_USER="", SMTP_PASSWORD="",
                  APP_PUBLIC_ORIGIN="https://quincena.example")
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
visitor = A.app.test_client()

print("== Página de presentación ==")
r = visitor.get("/")
html = r.get_data(as_text=True)
check("sin sesión, / muestra la presentación", r.status_code == 200 and "Controla tu quincena" in html and "authView" not in html)
check("la respuesta varía según la sesión (Vary: Cookie)", "Cookie" in r.headers.get("Vary", ""))
check("tiene título y descripción para Google", "<title>Quincena:" in html and 'name="description"' in html)
check("tiene un solo h1", len(re.findall(r"<h1[ >]", html)) == 1)
ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
types = {node["@type"] for node in ld["@graph"]}
check("datos estructurados: aplicación, organización y preguntas", {"SoftwareApplication", "Organization", "FAQPage"} <= types, types)
check("enlaza a entrar y a crear cuenta", 'href="/app"' in html and 'href="/app#registro"' in html)
check("enlaza a Gigadevs", "gigadevs-corp.onrender.com" in html)
check("la presentación no depende de scripts externos", not re.search(r'<script[^>]+src="https?://', html))

print("== App ==")
r = visitor.get("/app")
app_html = r.get_data(as_text=True)
check("/app abre la app", r.status_code == 200 and "authView" in app_html)
check("la app no se indexa (noindex)", 'name="robots" content="noindex"' in app_html)
member = A.app.test_client()
member.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"})
check("con sesión, / sigue abriendo la app (PWA instaladas)", "authView" in member.get("/").get_data(as_text=True))
manifest = json.loads(visitor.get("/static/manifest.webmanifest").get_data(as_text=True))
check("la PWA instalada abre /app", manifest["start_url"] == "/app" and manifest["shortcuts"][0]["url"].startswith("/app?"))

print("== Buscadores ==")
robots = visitor.get("/robots.txt").get_data(as_text=True)
check("robots.txt permite la portada y oculta la API y la app", "Allow: /\n" in robots and "Disallow: /api/" in robots and "Disallow: /app" in robots)
check("robots.txt indica el sitemap con el dominio público", "Sitemap: https://quincena.example/sitemap.xml" in robots)
sm = visitor.get("/sitemap.xml")
check("sitemap.xml es XML y lista la portada y las páginas legales",
      sm.mimetype == "application/xml" and all(f"<loc>https://quincena.example{p}</loc>" in sm.get_data(as_text=True) for p in ("/", "/terminos", "/privacidad")))

r = visitor.get("/google65c83ff0e56e9e6a.html")
check("el archivo de verificación de Google responde en la raíz", r.status_code == 200 and r.get_data(as_text=True).strip() == "google-site-verification: google65c83ff0e56e9e6a.html")

print("== Enlaces de correo ==")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py"), encoding="utf-8").read()
check("los correos de verificación y recuperación abren /app", '/app#verify=' in src and '/app#reset=' in src and '}/#verify=' not in src)

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
