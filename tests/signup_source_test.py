"""Pruebas del origen de cada cuenta nueva (utm de publicaciones y anuncios), sin red."""
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
admin = A.app.test_client()
admin.post("/api/setup", json={"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"}, headers={"X-Forwarded-For": "198.51.100.7"})


def register(name, ip, source=None, agent="Mozilla/5.0"):
    c = A.app.test_client()
    r = c.post("/api/register", json={"username": name, "email": f"{name}@x.com", "password": "Zorro-Plata-55231x", "accept_terms": True, **({"source": source} if source is not None else {})},
               headers={"X-Forwarded-For": ip, "User-Agent": agent})
    assert r.status_code == 200, r.get_json()
    return c


def source_of(name):
    with A.app.app_context():
        return A.User.query.filter_by(username=name).first().signup_source


print("== Origen guardado al registrarse ==")
register("ana", "198.51.100.20", "facebook/post1")
check("con utm queda el origen facebook/post1", source_of("ana") == "facebook/post1")
register("beto", "198.51.100.21", "Instagram/Bio")
check("se guarda en minúsculas", source_of("beto") == "instagram/bio")
register("carla", "198.51.100.22")
check("sin enlace de campaña queda directo (vacío)", source_of("carla") is None)
register("dani", "198.51.100.23", agent="okhttp/4.12.0")
check("desde la app Android sin campaña queda android", source_of("dani") == "android")
register("eva", "198.51.100.24", "facebook/post2", agent="okhttp/4.12.0")
check("si trae campaña, gana la campaña aunque venga de la app", source_of("eva") == "facebook/post2")
register("fran", "198.51.100.25", "<script>alert(1)</script>/post 3")
check("se limpian caracteres peligrosos", source_of("fran") == "scriptalert1/script/post-3" and not set("<>() ") & set(source_of("fran")), source_of("fran"))
register("gabo", "198.51.100.26", "a" * 500)
check("se recorta a un largo seguro", len(source_of("gabo") or "") <= 120)
register("hugo", "198.51.100.27", {"x": 1})
check("un origen que no es texto no rompe el registro y se limpia", source_of("hugo") == "x-1", source_of("hugo"))

print("== Resumen para el administrador ==")
r = admin.get("/api/signups")
j = r.get_json()
rows = {row["source"]: row for row in j["by_source"]}
check("el admin ve el resumen por origen", r.status_code == 200 and j["total"] == 8, j)
check("cuenta los registros de facebook/post1", rows.get("facebook/post1", {}).get("total") == 1)
check("agrupa los registros sin campaña como directo", rows.get("directo", {}).get("total") == 1)
check("cuenta los de los últimos 7 días", rows.get("android", {}).get("last_7_days") == 1)
check("el admin no se cuenta como registro", "admin" not in str(j))
users = admin.get("/api/users").get_json()["users"]
check("la lista de usuarios muestra el origen", any(u["username"] == "ana" and u["signup_source"] == "facebook/post1" for u in users))

print("== Nadie más ve el resumen ==")
check("sin sesión: 401", A.app.test_client().get("/api/signups").status_code == 401)
member = register("ivan", "198.51.100.28")
check("un miembro: 403", member.get("/api/signups").status_code == 403)

print("== Migración de una base vieja ==")
with A.app.app_context():
    A.db.session.execute(A.db.text('ALTER TABLE "user" DROP COLUMN signup_source')); A.db.session.commit()
    sin = "signup_source" not in {c["name"] for c in A.sqlalchemy_inspect(A.db.engine).get_columns("user")}
    A.migrate_user_email()
    con = "signup_source" in {c["name"] for c in A.sqlalchemy_inspect(A.db.engine).get_columns("user")}
    viejos = A.User.query.count()
check("una base sin la columna la recibe al arrancar y conserva las cuentas", sin and con and viejos == 10, (sin, con, viejos))

print(f"\n{ok} pruebas correctas, {fail} con falla")
sys.exit(1 if fail else 0)
