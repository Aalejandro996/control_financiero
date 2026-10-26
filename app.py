from __future__ import annotations

import copy
import hmac
import json
import math
import os
import re
import secrets
import string
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
import hashlib
import smtplib
import ssl
import time
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape as html_escape
from zoneinfo import ZoneInfo
from functools import wraps
from pathlib import Path

from sqlalchemy import inspect as sqlalchemy_inspect

from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json, base64url_to_bytes,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, AttestationConveyancePreference,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement,
    UserVerificationRequirement,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
if os.getenv("RENDER") and not os.getenv("APP_SECRET_KEY"):
    raise RuntimeError("APP_SECRET_KEY must be configured in the production environment.")
if os.getenv("APP_SECRET_KEY") and ("REEMPLAZA" in os.getenv("APP_SECRET_KEY", "") or len(os.getenv("APP_SECRET_KEY", "")) < 32):
    raise RuntimeError("APP_SECRET_KEY must be a unique random value with at least 32 characters.")


def database_url() -> str:
    raw = os.getenv("DATABASE_URL", "sqlite:///control_financiero.db").strip()
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.getenv("APP_SECRET_KEY") or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    # "__Host-" obliga a HTTPS, sin dominio y ruta "/": el navegador la protege mejor.
    SESSION_COOKIE_NAME="__Host-quincena" if os.getenv("COOKIE_SECURE", "true" if os.getenv("RENDER") else "false").lower() == "true" else "quincena_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true" if os.getenv("RENDER") else "false").lower() == "true",
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
db = SQLAlchemy(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[os.getenv("API_DEFAULT_LIMIT", "240 per minute")],
                  storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

# Países soportados. "BS" sigue siendo el código interno de la moneda local (así los datos ya guardados no cambian):
# para cada persona significa "mi moneda local", y el país dice cuál es.
COUNTRIES = {
    "VE": {"name": "Venezuela", "cur": "VES", "sym": "Bs.", "plural": "bolívares", "dec": 2, "official": "BCV", "market": "USDT", "rate": 40.0},
    "CO": {"name": "Colombia", "cur": "COP", "sym": "COP $", "plural": "pesos colombianos", "dec": 0, "official": "TRM", "market": "", "rate": 4000.0},
    "CL": {"name": "Chile", "cur": "CLP", "sym": "CLP $", "plural": "pesos chilenos", "dec": 0, "official": "oficial", "market": "", "rate": 950.0},
    "PE": {"name": "Perú", "cur": "PEN", "sym": "S/", "plural": "soles", "dec": 2, "official": "oficial", "market": "", "rate": 3.7},
    "MX": {"name": "México", "cur": "MXN", "sym": "MX$", "plural": "pesos mexicanos", "dec": 2, "official": "oficial", "market": "", "rate": 18.0},
    "AR": {"name": "Argentina", "cur": "ARS", "sym": "AR$", "plural": "pesos argentinos", "dec": 2, "official": "oficial", "market": "", "rate": 1400.0},
}
DEFAULT_COUNTRY = "VE"


def clean_country(value) -> str:
    code = str(value or "").strip().upper()
    return code if code in COUNTRIES else DEFAULT_COUNTRY


SOURCE_UNSAFE = re.compile(r"[^a-z0-9._-]")


def clean_source(value, user_agent: str = "") -> str | None:
    """Origen del registro según los utm del enlace ("facebook/post1"). Sin campaña: "android" si viene de la app, o None."""
    parts = [SOURCE_UNSAFE.sub("", part.strip().lower().replace(" ", "-"))[:30] for part in str(value or "").split("/")[:4]]
    parts = [part for part in parts if part]
    if parts:
        return "/".join(parts)[:120]
    return "android" if "okhttp" in user_agent.lower() else None


DEFAULT_SHARED_DATA = {
    "country": DEFAULT_COUNTRY,  # país de la persona: define la moneda local, su tasa y cómo se muestran los montos
    "currency": "USD",
    "exchangeRate": 40.0,
    "marketRate": 0.0,  # dólar del mercado (USDT/paralelo) en Bs.; 0 = desconocido
    "bsSavings": 0.0,  # bolívares que la persona dice tener guardados (para el medidor de poder de compra)
    "rateHistory": [],  # [{"d": "AAAA-MM-DD", "b": tasa BCV, "m": tasa USDT}] un registro por día, máx. 120
    "accounts": [],  # cuentas: banco, efectivo, digital (Zelle, Binance…) con saldo inicial
    "transfers": [],  # movimientos entre cuentas (incluye cambios de $ a Bs.)
    "incomes": [],  # ingresos recibidos de verdad (cobros del sueldo, extras, devoluciones)
    "receivables": [],  # "Me deben": préstamos a otras personas
    "termsVersion": "",  # versión de términos aceptada
    "income": {"amount": 0, "currency": "USD"},  # compatibilidad con la versión anterior
    "incomeSources": [],  # sueldo, bono, cestatickets y otros ingresos programados
    "budgets": {"transporte": 0, "comida": 0, "fijo": 0, "hormiga": 0, "salud": 0, "hogar": 0, "ocio": 0, "educacion": 0, "deudas": 0},
    "expenses": [],
    "debts": [],
    "schoolExpenses": [],  # compromisos/cargos del colegio
    "schoolPayments": [],  # pagos parciales o totales de esos compromisos
    "plannedDebtPayments": [],  # agenda personal de pagos futuros de deuda
    "recurring": [],  # gastos fijos que se repiten cada mes
    "debtPlan": {"monthlyEssentials": 0.0, "monthlyCushion": 0.0, "paymentPercent": 60, "strategy": "avalanche", "configured": False},
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(254), nullable=True, index=True)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    role = db.Column(db.String(16), nullable=False, default="member")  # admin | member
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    signup_source = db.Column(db.String(120), nullable=True)  # de qué publicación o anuncio llegó (utm); None = directo


class SharedState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Cada fila contiene los datos financieros privados de exactamente una cuenta.
    # Nullable durante la migración de la antigua fila compartida; la migración
    # asigna esa información histórica al administrador original.
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    data = db.Column(db.JSON, nullable=False, default=lambda: copy.deepcopy(DEFAULT_SHARED_DATA))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False)
    detail = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class UserPasskey(db.Model):
    """Public WebAuthn credential registered by one private account."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False, index=True)
    credential_id = db.Column(db.String(1024), nullable=False, unique=True)
    public_key = db.Column(db.Text, nullable=False)
    sign_count = db.Column(db.BigInteger, nullable=False, default=0)
    label = db.Column(db.String(80), nullable=False, default="Este dispositivo")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class PushSubscription(db.Model):
    """Browser push endpoint scoped to the account that opted in."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = db.Column(db.String(2048), nullable=False, unique=True)
    subscription_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ReminderDelivery(db.Model):
    """Deduplication ledger: one reminder per payment/date/lead time/account."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_key = db.Column(db.String(140), nullable=False)
    due_date = db.Column(db.String(10), nullable=False)
    days_before = db.Column(db.Integer, nullable=False)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint("user_id", "payment_key", "due_date", "days_before", name="uq_reminder_delivery"),)


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

class DeviceToken(db.Model):
    """Sesión de larga duración para la app móvil (se guarda solo el hash del token)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    label = db.Column(db.String(80), nullable=False, default="Android")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)


class EmailVerification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)


def migrate_user_email() -> None:
    """Añade user.email, user.email_verified_at y user.signup_source si faltan. Debe correr antes de cualquier consulta a User."""
    def columns() -> set:
        return {c["name"] for c in sqlalchemy_inspect(db.engine).get_columns("user")}

    for name, ddl in (("email", "VARCHAR(254)"), ("email_verified_at", "TIMESTAMP WITH TIME ZONE"), ("signup_source", "VARCHAR(120)")):
        if name in columns():
            continue
        try:
            db.session.execute(db.text(f'ALTER TABLE "user" ADD COLUMN {name} {ddl}'))
            db.session.commit()
        except Exception:
            db.session.rollback()
            if name not in columns():  # si otro proceso la creó al mismo tiempo, no es un error
                raise


def ensure_user_state(user_id: int) -> SharedState:
    """Return only this account's private financial state, creating it if needed."""
    record = SharedState.query.filter_by(user_id=user_id).first()
    if record is not None:
        return record

    # Recovery for a legacy database whose original singleton row had no owner
    # because it was created before any user existed. Only the sole initial admin
    # may claim it; never hand an orphaned state to an arbitrary later member.
    user = db.session.get(User, user_id)
    orphan = SharedState.query.filter(SharedState.user_id.is_(None)).order_by(SharedState.id.asc()).first()
    if orphan is not None and user and user.role == "admin" and User.query.count() == 1:
        orphan.user_id = user_id
        try:
            db.session.commit()
            return orphan
        except Exception:
            db.session.rollback()
            record = SharedState.query.filter_by(user_id=user_id).first()
            if record is not None:
                return record
            raise

    record = SharedState(user_id=user_id, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA), updated_by=user_id)
    db.session.add(record)
    try:
        db.session.commit()
    except Exception:
        # Multiple workers may initialize the same user simultaneously.
        db.session.rollback()
        record = SharedState.query.filter_by(user_id=user_id).first()
        if record is None:
            raise
    return record


def migrate_shared_state_schema() -> None:
    """Migrate the old single shared record without deleting financial data.

    The old app did not record which account owned each record. To avoid exposing
    that historical common pool to every new account, it is retained under the
    earliest administrator; every other account receives an empty private state.
    """
    inspector = sqlalchemy_inspect(db.engine)
    if "shared_state" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("shared_state")}
    if "user_id" not in columns:
        db.session.execute(db.text('ALTER TABLE shared_state ADD COLUMN user_id INTEGER REFERENCES "user" (id)'))
        db.session.commit()

    first_admin = User.query.filter_by(role="admin").order_by(User.created_at.asc(), User.id.asc()).first()
    if first_admin is None:
        first_admin = User.query.order_by(User.created_at.asc(), User.id.asc()).first()
    if first_admin is not None:
        db.session.execute(
            db.text("UPDATE shared_state SET user_id = :user_id WHERE user_id IS NULL"),
            {"user_id": first_admin.id},
        )
        db.session.commit()

    # Unique owner index prevents accidental duplicate state rows per account.
    db.session.execute(db.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_state_user_id ON shared_state (user_id)"
    ))
    db.session.commit()


def audit(action: str, detail: str = "", user_id: int | None = None) -> None:
    try:
        db.session.add(AuditEvent(
            user_id=user_id if user_id is not None else session.get("uid"),
            action=action[:64], detail=detail[:255] if detail else None,
            ip_address=(request.remote_addr or "")[:64],  # ProxyFix ya tomó la IP real del proxy de Render; X-Forwarded-For lo escribe el cliente
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("No se pudo guardar el evento de auditoría")


COMMON_PASSWORDS = {
    "123456789012", "1234567890123", "contraseña123", "contrasena123", "password1234", "password12345", "qwertyuiop12",
    "venezuela123", "venezuela1234", "123456789abc", "abc123456789", "quincena1234", "quincena12345", "000000000000",
    "111111111111", "iloveyou1234", "admin1234567", "administrador", "caracas12345", "123123123123", "qwerty123456",
}


def pw_stamp(user: "User") -> str:
    """Huella de la contraseña actual. Si la contraseña cambia, las sesiones web abiertas con la anterior dejan de valer."""
    return hashlib.sha256(("qz1:" + user.password_hash).encode()).hexdigest()[:20]


DUMMY_HASH = generate_password_hash("quincena-" + secrets.token_hex(12))


def verify_login(user: "User | None", password: str) -> bool:
    """Comprueba la contraseña tardando lo mismo exista o no el usuario (evita adivinar usuarios por el tiempo de respuesta)."""
    if len(password) > 1000:  # nadie escribe 1000 caracteres; evita gastar CPU con claves enormes
        return False
    ok = check_password_hash(user.password_hash if user else DUMMY_HASH, password)
    return bool(ok and user)


def _login_name_key() -> str:
    body = request.get_json(silent=True) or {}
    return "login:" + str(body.get("username", "")).strip().lower()[:40]


def _forgot_email_key() -> str:
    body = request.get_json(silent=True) or {}
    return "forgot:" + str(body.get("email", "")).strip().lower()[:120]


def password_problem(password: str, username: str = "") -> str | None:
    """Devuelve el motivo por el que una contraseña no sirve, o None si está bien."""
    if len(password) < 12 or len(password) > 200:
        return "Usa una contraseña de 12 a 200 caracteres."
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(password)) < 5:
        return "Esa contraseña es muy fácil de adivinar. Elige otra más única."
    if username and len(username) >= 3 and username.lower() in low:
        return "La contraseña no debe contener tu nombre de usuario."
    return None


def setup_required() -> bool:
    return db.session.query(User.id).first() is None


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def user_from_bearer() -> User | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    row = DeviceToken.query.filter_by(token_hash=hashlib.sha256(header[7:].strip().encode()).hexdigest(), revoked_at=None).first()
    now = datetime.now(timezone.utc)
    if row is None or _aware(row.expires_at) < now:
        return None
    user = db.session.get(User, row.user_id)
    if user is None or not user.active:
        return None
    if row.last_used_at is None or now - _aware(row.last_used_at) > timedelta(hours=1):
        row.last_used_at, row.expires_at = now, now + timedelta(days=90)  # la sesión se renueva al usarla
        db.session.commit()
    g.via_token = True
    return user


SESSION_IDLE = timedelta(minutes=int(os.getenv("SESSION_IDLE_MINUTES", "30") or 30))


def current_user() -> User | None:
    user_id = session.get("uid")
    if not user_id:
        return user_from_bearer()
    now = time.time()
    last = float(session.get("seen", now))
    if now - last > SESSION_IDLE.total_seconds():
        session.clear()  # inactiva demasiado tiempo: hay que volver a entrar
        return None
    if now - last > 60:
        session["seen"] = now
    user = db.session.get(User, user_id)
    if user is None or not user.active:
        session.clear()
        return None
    if session.get("ps") != pw_stamp(user):  # la contraseña cambió después de abrir esta sesión
        session.clear()
        return None
    return user


def current_bearer_hash() -> str | None:
    header = request.headers.get("Authorization", "")
    return hashlib.sha256(header[7:].strip().encode()).hexdigest() if header.startswith("Bearer ") else None


def revoke_other_devices(user_id: int) -> int:
    """Cierra la sesión de la app en todos los teléfonos menos el que hace la petición."""
    keep = current_bearer_hash()
    now = datetime.now(timezone.utc)
    q = DeviceToken.query.filter_by(user_id=user_id, revoked_at=None)
    if keep:
        q = q.filter(DeviceToken.token_hash != keep)
    rows = q.all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


def csrf_ok() -> bool:
    token = session.get("csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    return bool(token and supplied and hmac.compare_digest(token, supplied))


def require_auth(admin: bool = False, allow_password_change: bool = False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify(error="Debes iniciar sesión.", code="unauthorized"), 401
            if admin and user.role != "admin":
                return jsonify(error="Esta acción requiere permisos de administrador."), 403
            if user.must_change_password and not allow_password_change:
                return jsonify(error="Debes cambiar tu contraseña antes de continuar.", code="password_change_required"), 428
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not getattr(g, "via_token", False) and not csrf_ok():
                return jsonify(error="Token de seguridad vencido. Actualiza la página e inicia sesión nuevamente."), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def _rate_or_none(value):
    """Tasa en Bs. por dólar válida, o None."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 < number < 10_000_000 else None


def _clean_rows(raw, limit: int, spec: dict) -> list:
    """Limpia una colección: solo campos conocidos, con su tipo. spec = {campo: tipo}; tipos: id, ref, str:N, num, pos, cur, date, enum:a|b, bool."""
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw[:limit]:
        if not isinstance(row, dict):
            continue
        clean, ok = {}, True
        for field, kind in spec.items():
            value = row.get(field)
            if kind in ("id", "ref"):
                text = str(value or "")
                if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", text):
                    clean[field] = text
                elif kind == "id":
                    ok = False
                else:
                    clean[field] = ""
            elif kind.startswith("str:"):
                clean[field] = str(value or "")[: int(kind[4:])]
            elif kind in ("num", "pos"):
                try:
                    number = float(value or 0)
                except (TypeError, ValueError, OverflowError):
                    number = 0.0
                if not math.isfinite(number):
                    number = 0.0
                number = max(-1_000_000_000_000, min(number, 1_000_000_000_000))
                if kind == "pos":
                    number = max(0.0, number)
                clean[field] = number
            elif kind == "cur":
                clean[field] = "BS" if value in ("BS", "VES") else "USD"
            elif kind == "date":
                clean[field] = str(value or "")[:40]
            elif kind.startswith("enum:"):
                options = kind[5:].split("|")
                clean[field] = value if value in options else options[0]
            elif kind == "bool":
                clean[field] = value is True
        if ok:
            out.append(clean)
    return out


def normalize_shared_data(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Los datos deben tener formato de objeto JSON.")
    result = copy.deepcopy(DEFAULT_SHARED_DATA)
    result["country"] = clean_country(value.get("country"))
    default_rate = COUNTRIES[result["country"]]["rate"]
    result["currency"] = "BS" if value.get("currency") in ("BS", "VES") else "USD"
    try:
        rate = float(value.get("exchangeRate", default_rate))
    except (TypeError, ValueError):
        rate = default_rate
    result["exchangeRate"] = rate if math.isfinite(rate) and 0 < rate < 1_000_000 else default_rate
    result["marketRate"] = _rate_or_none(value.get("marketRate")) or 0.0
    try:
        savings = float(value.get("bsSavings") or 0)
    except (TypeError, ValueError, OverflowError):
        savings = 0.0
    result["bsSavings"] = max(0.0, min(savings, 1_000_000_000_000)) if math.isfinite(savings) else 0.0
    history, seen_days = [], set()
    raw_history = value.get("rateHistory") if isinstance(value.get("rateHistory"), list) else []
    for item in raw_history[-120:]:
        if not isinstance(item, dict):
            continue
        day, bcv = str(item.get("d") or ""), _rate_or_none(item.get("b"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or bcv is None or day in seen_days:
            continue
        seen_days.add(day)
        history.append({"d": day, "b": bcv, "m": _rate_or_none(item.get("m")) or 0.0})
    result["rateHistory"] = sorted(history, key=lambda x: x["d"])
    result["accounts"] = _clean_rows(value.get("accounts"), 50, {
        "id": "id", "name": "str:60", "kind": "enum:banco|efectivo|digital|cripto", "currency": "cur", "initial": "num", "archived": "bool"})
    result["transfers"] = _clean_rows(value.get("transfers"), 5000, {
        "id": "id", "date": "date", "fromId": "ref", "toId": "ref", "amountFrom": "pos", "amountTo": "pos", "note": "str:120"})
    result["incomes"] = _clean_rows(value.get("incomes"), 10000, {
        "id": "id", "date": "date", "amount": "pos", "currency": "cur", "name": "str:80", "sourceId": "ref", "accountId": "ref",
        "kind": "enum:extra|sueldo|cobro"})
    result["receivables"] = _clean_rows(value.get("receivables"), 2000, {
        "id": "id", "person": "str:80", "amount": "pos", "paid": "pos", "currency": "cur", "date": "date", "dueDate": "date", "note": "str:200", "accountId": "ref"})
    for r in result["receivables"]:
        r["paid"] = min(r["paid"], r["amount"])
    result["termsVersion"] = str(value.get("termsVersion") or "")[:20]
    income = value.get("income") if isinstance(value.get("income"), dict) else {}
    try:
        income_amount = float(income.get("amount", 0))
    except (TypeError, ValueError):
        income_amount = 0
    if not math.isfinite(income_amount):
        income_amount = 0
    result["income"] = {"amount": max(0, min(income_amount, 1_000_000_000_000)), "currency": "BS" if income.get("currency") in ("BS", "VES") else "USD"}

    # Parámetros del planificador de deuda: importes mensuales expresados en USD.
    raw_plan = value.get("debtPlan") if isinstance(value.get("debtPlan"), dict) else {}
    def safe_plan_amount(name: str) -> float:
        try:
            amount = float(raw_plan.get(name, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if not math.isfinite(amount):
            amount = 0.0
        return max(0.0, min(amount, 1_000_000_000_000))
    try:
        payment_percent = int(float(raw_plan.get("paymentPercent", 60)))
    except (TypeError, ValueError, OverflowError):
        payment_percent = 60
    result["debtPlan"] = {
        "monthlyEssentials": safe_plan_amount("monthlyEssentials"),
        "monthlyCushion": safe_plan_amount("monthlyCushion"),
        "paymentPercent": max(30, min(payment_percent, 85)),
        "strategy": raw_plan.get("strategy") if raw_plan.get("strategy") in {"avalanche", "snowball"} else "avalanche",
        "configured": raw_plan.get("configured", False) is True,
    }

    budgets = value.get("budgets") if isinstance(value.get("budgets"), dict) else {}
    for key in result["budgets"]:
        try:
            result["budgets"][key] = max(0, min(float(budgets.get(key, 0)), 1_000_000_000_000))
        except (TypeError, ValueError):
            result["budgets"][key] = 0
        if not math.isfinite(result["budgets"][key]):
            result["budgets"][key] = 0
    for key in ("expenses", "debts"):
        raw_rows = value.get(key, [])
        if not isinstance(raw_rows, list) or len(raw_rows) > 10000:
            raise ValueError(f"La colección {key} no es válida o supera el límite permitido.")
        clean_rows = []
        for row in raw_rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            # Keep the existing app's record fields, but discard nested values / very long payloads.
            clean = {}
            for field, field_value in row.items():
                if field not in {"id", "amount", "paid", "currency", "category", "description", "method", "bank", "date", "type", "entity", "minPayment", "cutDate", "payBefore", "installments", "invoice", "interestRate", "interestPeriod", "interestKnown", "rateType", "rateUsed", "bcvRate", "marketRate", "accountId"}:
                    continue
                if isinstance(field_value, (str, int, float, bool)) or field_value is None:
                    if isinstance(field_value, str):
                        clean[field] = field_value[:500]
                    else:
                        clean[field] = field_value
            try:
                clean["amount"] = max(0, min(float(row.get("amount", 0)), 1_000_000_000_000))
                clean["paid"] = max(0, min(float(row.get("paid", 0) or 0), 1_000_000_000_000))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(clean["amount"]) or not math.isfinite(clean["paid"]):
                continue
            item_id = str(row["id"])
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
                continue
            clean["id"] = item_id
            clean["paid"] = min(clean["paid"], clean["amount"])
            clean["currency"] = "BS" if row.get("currency") in ("BS", "VES") else "USD"
            clean["date"] = str(row.get("date") or "")[:40]
            if key == "expenses":
                clean["category"] = row.get("category") if row.get("category") in {"comida", "transporte", "fijo", "hormiga", "salud", "hogar", "ocio", "educacion", "deudas"} else "fijo"
                clean["description"] = str(row.get("description") or "Gasto")[:250]
                clean["bank"] = str(row.get("bank") or "")[:120]
                clean["method"] = row.get("method") if row.get("method") in {"banco", "efectivo", "pago_movil"} else "banco"
                # "Las tres tasas": a qué tasa se consiguieron los bolívares de este gasto.
                for rate_field in ("rateUsed", "bcvRate", "marketRate"):
                    clean.pop(rate_field, None)
                    rate_value = _rate_or_none(row.get(rate_field))
                    if rate_value is not None and clean["currency"] == "BS":
                        clean[rate_field] = rate_value
                account_id = str(row.get("accountId") or "")
                clean.pop("accountId", None)
                if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", account_id):
                    clean["accountId"] = account_id
                clean.pop("rateType", None)
                if clean["currency"] == "BS" and row.get("rateType") in {"bcv", "usdt", "otra"} and "rateUsed" in clean:
                    clean["rateType"] = row.get("rateType")
            else:
                clean["type"] = row.get("type") if row.get("type") in {"tdc", "cashea", "prestamo", "otro"} else "otro"
                clean["entity"] = str(row.get("entity") or "Entidad")[:120]
                clean["description"] = str(row.get("description") or "Deuda")[:250]
                clean["invoice"] = str(row.get("invoice") or "")[:100]
                try:
                    clean["minPayment"] = max(0.0, min(float(row.get("minPayment") or 0), 1_000_000_000_000))
                except (TypeError, ValueError, OverflowError):
                    clean["minPayment"] = 0.0
                if not math.isfinite(clean["minPayment"]):
                    clean["minPayment"] = 0.0
                try:
                    clean["installments"] = max(0, min(int(float(row.get("installments") or 0)), 100000))
                except (TypeError, ValueError, OverflowError):
                    clean["installments"] = 0
                try:
                    clean["interestRate"] = max(0.0, min(float(row.get("interestRate") or 0), 10000))
                except (TypeError, ValueError, OverflowError):
                    clean["interestRate"] = 0.0
                if not math.isfinite(clean["interestRate"]):
                    clean["interestRate"] = 0.0
                clean["interestPeriod"] = row.get("interestPeriod") if row.get("interestPeriod") in {"mensual", "semanal"} else "mensual"
                clean["interestKnown"] = bool(row.get("interestKnown", float(clean["interestRate"] or 0) > 0))
                clean["cutDate"] = str(row.get("cutDate") or "")[:20]
                clean["payBefore"] = str(row.get("payBefore") or "")[:40]
            clean_rows.append(clean)
        result[key] = clean_rows

    # Fuentes de ingresos: el monto es el importe por cada pago, no el total mensual.
    raw_sources = value.get("incomeSources", [])
    if not isinstance(raw_sources, list) or len(raw_sources) > 500:
        raise ValueError("La colección incomeSources no es válida.")
    clean_sources = []
    for row in raw_sources:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        item_id = str(row.get("id"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
            continue
        try:
            amount = float(row.get("amount", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            continue
        kind = row.get("kind") if row.get("kind") in {"sueldo", "bono", "cestatickets", "otro"} else "otro"
        frequency = row.get("frequency") if row.get("frequency") in {"semanal", "quincenal", "mensual", "ocasional"} else "quincenal"
        raw_days = row.get("payDays", [])
        if not isinstance(raw_days, list):
            raw_days = []
        days = []
        for day in raw_days[:2]:
            try:
                n = int(day)
            except (TypeError, ValueError, OverflowError):
                continue
            if 1 <= n <= 31 and n not in days:
                days.append(n)
        days.sort()
        if frequency == "semanal":  # payDays = [día de la semana que cobra]: 1 = lunes … 7 = domingo
            days = [days[0]] if days and 1 <= days[0] <= 7 else [5]
        elif frequency == "quincenal" and len(days) != 2:
            days = [15, 30]
        elif frequency == "mensual" and len(days) < 1:
            days = [30]
        elif frequency == "mensual":
            days = days[:1]
        else:  # ingreso ocasional: no se presume un importe mensual recurrente
            days = []
        clean_sources.append({
            "id": item_id, "kind": kind,
            "name": str(row.get("name") or "")[:120],
            "amount": amount,
            "currency": "BS" if row.get("currency") in ("BS", "VES") else "USD",
            "frequency": frequency, "payDays": days,
        })
    result["incomeSources"] = clean_sources

    # Gastos/compromisos escolares. Los pagos quedan en una colección separada
    # para conservar el historial de abonos sin modificar el compromiso original.
    raw_school = value.get("schoolExpenses", [])
    if not isinstance(raw_school, list) or len(raw_school) > 10000:
        raise ValueError("La colección schoolExpenses no es válida.")
    school_rows = []
    school_concepts = {"mensualidad", "matricula", "utiles", "uniforme", "transporte", "comedor", "actividad", "otro"}
    for row in raw_school:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        item_id = str(row.get("id"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
            continue
        try:
            amount = float(row.get("amount", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            continue
        concept = row.get("concept") if row.get("concept") in school_concepts else "otro"
        school_rows.append({
            "id": item_id,
            "student": str(row.get("student") or "")[:120],
            "concept": concept,
            "description": str(row.get("description") or "Gasto escolar")[:250],
            "amount": amount,
            "currency": "BS" if row.get("currency") in ("BS", "VES") else "USD",
            "date": str(row.get("date") or "")[:40],
            "dueDate": str(row.get("dueDate") or "")[:40],
            "notes": str(row.get("notes") or "")[:500],
        })
    result["schoolExpenses"] = school_rows

    raw_payments = value.get("schoolPayments", [])
    if not isinstance(raw_payments, list) or len(raw_payments) > 20000:
        raise ValueError("La colección schoolPayments no es válida.")
    payment_rows = []
    valid_methods = {"banco", "efectivo", "pago_movil", "tarjeta"}
    for row in raw_payments:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        item_id = str(row.get("id"))
        expense_id = str(row.get("expenseId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", expense_id):
            continue
        try:
            amount = float(row.get("amount", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            continue
        payment_rows.append({
            "id": item_id, "expenseId": expense_id, "amount": amount,
            "currency": "BS" if row.get("currency") in ("BS", "VES") else "USD",
            "date": str(row.get("date") or "")[:40],
            "method": row.get("method") if row.get("method") in valid_methods else "banco",
            "reference": str(row.get("reference") or "")[:120],
            "notes": str(row.get("notes") or "")[:250],
        })
    result["schoolPayments"] = payment_rows

    raw_planned = value.get("plannedDebtPayments", [])
    if not isinstance(raw_planned, list) or len(raw_planned) > 10000:
        raise ValueError("La colección plannedDebtPayments no es válida.")
    planned_rows = []
    valid_schedule_types = {"quincena1", "quincena2", "date"}
    valid_statuses = {"planned", "paid", "cancelled"}
    for row in raw_planned:
        if not isinstance(row, dict) or not row.get("id") or not row.get("debtId"):
            continue
        item_id, debt_id = str(row.get("id")), str(row.get("debtId"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", debt_id):
            continue
        try:
            amount = float(row.get("amount", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            continue
        schedule_type = row.get("scheduleType") if row.get("scheduleType") in valid_schedule_types else "date"
        planned_rows.append({
            "id": item_id, "debtId": debt_id,
            "entity": str(row.get("entity") or "")[:120],
            "description": str(row.get("description") or "Pago de deuda")[:250],
            "amount": amount,
            "currency": "BS" if row.get("currency") in ("BS", "VES") else "USD",
            "scheduleType": schedule_type,
            "scheduledDate": str(row.get("scheduledDate") or "")[:40],
            "status": row.get("status") if row.get("status") in valid_statuses else "planned",
            "createdAt": str(row.get("createdAt") or "")[:40],
            "paidAt": str(row.get("paidAt") or "")[:40],
            "paymentChoice": row.get("paymentChoice") if row.get("paymentChoice") in {"minimum", "total", "custom"} else "custom",
        })
    result["plannedDebtPayments"] = planned_rows
    raw_rec = value.get("recurring", [])
    if not isinstance(raw_rec, list) or len(raw_rec) > 500:
        raise ValueError("La colección recurring no es válida.")
    rec_rows = []
    for row in raw_rec:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        item_id = str(row.get("id"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
            continue
        try:
            amount, day = float(row.get("amount", 0)), int(row.get("day", 1))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            continue
        last = str(row.get("lastGenerated") or "")
        rec_rows.append({
            "id": item_id, "description": str(row.get("description") or "Gasto fijo")[:120], "amount": amount,
            "currency": "BS" if row.get("currency") in ("BS", "VES") else "USD",
            "category": row.get("category") if row.get("category") in {"comida", "transporte", "fijo", "hormiga", "salud", "hogar", "ocio", "educacion", "deudas"} else "fijo",
            "day": min(31, max(1, day)), "active": bool(row.get("active", True)),
            "lastGenerated": last if re.fullmatch(r"\d{4}-\d{2}", last) else "",
        })
    result["recurring"] = rec_rows
    return result


def resolve_planned_due_date(item: dict, data: dict, today: date | None = None) -> date | None:
    """Resolve an explicit scheduled date or align a quincena with configured income days."""
    try:
        today = today or datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "America/Caracas"))).date()
    except Exception:
        today = today or datetime.now(timezone.utc).date()
    raw_date = str(item.get("scheduledDate") or "")[:10]
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            return None
    schedule_type = item.get("scheduleType")
    if schedule_type not in {"quincena1", "quincena2"}:
        return None
    pay_days = []
    for source in (data.get("incomeSources", []) if isinstance(data, dict) else []):
        if isinstance(source, dict) and source.get("frequency") == "quincenal":
            days = source.get("payDays") or []
            if isinstance(days, list) and len(days) >= 2:
                try:
                    pay_days.append(sorted(max(1, min(31, int(day))) for day in days[:2]))
                except (TypeError, ValueError):
                    pass
    if pay_days:
        pay_day = min(pair[0] for pair in pay_days) if schedule_type == "quincena1" else max(pair[1] for pair in pay_days)
    else:
        pay_day = 15 if schedule_type == "quincena1" else 30
    import calendar
    year, month = today.year, today.month
    candidate = date(year, month, min(pay_day, calendar.monthrange(year, month)[1]))
    if candidate < today:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        candidate = date(year, month, min(pay_day, calendar.monthrange(year, month)[1]))
    return candidate


def webauthn_settings() -> tuple[str, str]:
    origin = (os.getenv("APP_PUBLIC_ORIGIN") or request.host_url.rstrip("/")).rstrip("/")
    parsed = urlsplit(origin)
    rp_id = os.getenv("WEBAUTHN_RP_ID") or parsed.hostname
    if not parsed.scheme or not parsed.hostname or not rp_id:
        raise ValueError("Configura APP_PUBLIC_ORIGIN y WEBAUTHN_RP_ID para usar acceso biométrico.")
    return rp_id, origin


def establish_user_session(user: User) -> dict:
    session.clear()
    session["uid"] = user.id
    session["ps"] = pw_stamp(user)
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    audit("passkey_login_succeeded", "Inicio de sesión mediante passkey", user.id)
    return {"ok": True, "csrf_token": session["csrf_token"], "user": {"username": user.username, "role": user.role, "must_change_password": user.must_change_password}}


def validate_school_balances(data: dict) -> str | None:
    expenses = {str(item["id"]): item for item in data.get("schoolExpenses", [])}
    paid_by_expense = {}
    for payment in data.get("schoolPayments", []):
        expense = expenses.get(str(payment.get("expenseId")))
        if expense is None:
            return "Hay un pago escolar asociado a un gasto que no existe. Actualiza los datos e inténtalo de nuevo."
        if payment.get("currency") != expense.get("currency"):
            return "La moneda del pago debe coincidir con la moneda del gasto escolar."
        key = str(payment["expenseId"])
        paid_by_expense[key] = paid_by_expense.get(key, 0.0) + float(payment.get("amount", 0))
    for expense_id, total_paid in paid_by_expense.items():
        if total_paid > float(expenses[expense_id]["amount"]) + 0.01:
            return "El total de pagos supera el monto registrado para uno de los gastos escolares. Revisa el saldo antes de guardar."
    return None


def same(a, b) -> bool:
    return a == b


def merge_array(base_list, incoming_list, current_list):
    base = {str(x.get("id")): x for x in base_list if isinstance(x, dict) and x.get("id") is not None}
    incoming = {str(x.get("id")): x for x in incoming_list if isinstance(x, dict) and x.get("id") is not None}
    current = {str(x.get("id")): x for x in current_list if isinstance(x, dict) and x.get("id") is not None}
    result = dict(current)
    conflicts = []
    all_ids = set(base) | set(incoming) | set(current)
    for item_id in all_ids:
        before, local, remote = base.get(item_id), incoming.get(item_id), current.get(item_id)
        local_changed = local != before
        remote_changed = remote != before
        if not local_changed:
            continue
        if remote_changed and local != remote:
            conflicts.append(item_id)
            continue
        if item_id in incoming:
            result[item_id] = local
        else:
            result.pop(item_id, None)
    # Keep newest-first ordering for the application's existing history views.
    merged_rows = list(result.values())
    merged_rows.sort(key=lambda row: str(row.get("date", "")), reverse=True)
    return merged_rows, conflicts


def three_way_merge(base_data, incoming_data, current_data):
    base = normalize_shared_data(base_data)
    incoming = normalize_shared_data(incoming_data)
    current = normalize_shared_data(current_data)
    merged = copy.deepcopy(current)
    conflicts = []
    for key in ("currency", "income", "budgets", "debtPlan", "bsSavings"):
        if incoming[key] != base[key]:
            if current[key] != base[key] and current[key] != incoming[key]:
                conflicts.append(key)
            else:
                merged[key] = incoming[key]
    # Tasas e historial: gana el último que escribe (no es un conflicto real).
    for key in ("country", "exchangeRate", "marketRate", "rateHistory", "termsVersion"):
        if incoming[key] != base[key]:
            merged[key] = incoming[key]
    for key in ("expenses", "debts", "incomeSources", "schoolExpenses", "schoolPayments", "plannedDebtPayments", "recurring",
                "accounts", "transfers", "incomes", "receivables"):
        merged[key], row_conflicts = merge_array(base[key], incoming[key], current[key])
        if row_conflicts:
            conflicts.extend([f"{key}:{x}" for x in row_conflicts])
    return merged, conflicts


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Cookie"
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    # The existing page uses inline templates/events and trusted CDN assets for its visual styling.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; connect-src 'self' https://ve.dolarapi.com; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; font-src 'self' data: https://cdnjs.cloudflare.com https://fonts.gstatic.com")
    if os.getenv("RENDER") or request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@limiter.exempt
@app.get("/")
def index():
    """Sin sesión: la página de presentación (la que ven Google y los visitantes). Con sesión: la app, como siempre,
    para que las PWA instaladas y los enlaces viejos sigan abriendo directo."""
    page = "index.html" if current_user() is not None else "landing.html"
    response = send_from_directory(app.static_folder, page)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return response


@limiter.exempt
@app.get("/app")
def web_app():
    response = send_from_directory(app.static_folder, "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@limiter.exempt
@app.get("/robots.txt")
def robots():
    origin = public_origin() or request.url_root.rstrip("/")
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /app\nDisallow: /descargar/\n\nSitemap: {origin}/sitemap.xml\n"
    return app.response_class(body, mimetype="text/plain")


@limiter.exempt
@app.get("/google65c83ff0e56e9e6a.html")
def google_site_verification():
    # Archivo con el que Google Search Console comprueba que el sitio es nuestro; debe quedarse publicado.
    return send_from_directory(app.static_folder, "google65c83ff0e56e9e6a.html", mimetype="text/html")


@limiter.exempt
@app.get("/sitemap.xml")
def sitemap():
    origin = public_origin() or request.url_root.rstrip("/")
    pages = [("/", "1.0", "weekly"), ("/terminos", "0.3", "yearly"), ("/privacidad", "0.3", "yearly"), ("/cookies", "0.2", "yearly")]
    urls = "".join(f"<url><loc>{origin}{path}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>" for path, prio, freq in pages)
    return app.response_class(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', mimetype="application/xml")


@limiter.exempt
@app.get("/favicon.ico")
def favicon():
    # Los navegadores lo piden siempre en la raíz, aunque la página declare otro icono.
    response = send_from_directory(Path(app.static_folder) / "icons", "favicon.ico", mimetype="image/x-icon")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@limiter.exempt
@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


# ---- App Android: el archivo va en downloads/quincena.apk (fuera de /static para que el service worker no lo guarde) ----
APK_PATH = Path(__file__).resolve().parent / "downloads" / "quincena.apk"


def android_version() -> dict:
    """Versión del APK publicado: la escribe GitHub Actions en downloads/version.json (o se toma de variables)."""
    info = {"versionCode": int(os.getenv("ANDROID_APP_VERSION_CODE", "6") or 6), "version": os.getenv("ANDROID_APP_VERSION", "1.4.0"), "notes": ""}
    try:
        raw = json.loads((APK_PATH.parent / "version.json").read_text(encoding="utf-8"))
        info["versionCode"] = int(raw.get("versionCode", info["versionCode"]))
        info["version"] = str(raw.get("versionName") or info["version"])[:20]
        info["notes"] = str(raw.get("notes") or "")[:200]
    except (OSError, ValueError, TypeError):
        pass
    # Nunca mostrar mensajes técnicos de Git (Merge, Revert…) a los usuarios.
    if not info["notes"].strip() or re.match(r"(?i)^(merge|revert|apk |wip|fix\b|chore|update )", info["notes"].strip()):
        info["notes"] = "Mejoras de seguridad y correcciones."
    return info


def android_min_version_code() -> int:
    """Versión mínima de la app que puede usar el servidor. ANDROID_MIN_VERSION en Render:
    "latest" (por defecto) obliga a usar siempre la última publicada; un número fija esa versión; "0" u "off" lo apaga."""
    if not APK_PATH.is_file():
        return 0
    setting = os.getenv("ANDROID_MIN_VERSION", "latest").strip().lower()
    if setting in {"", "latest", "ultima", "última"}:
        return android_version()["versionCode"]
    if setting in {"0", "off", "no", "false"}:
        return 0
    try:
        return min(int(setting), android_version()["versionCode"])  # nunca exigir una versión que no existe
    except ValueError:
        return android_version()["versionCode"]


# Rutas que una app desactualizada sigue pudiendo usar: saber que hay versión nueva y descargarla.
UPDATE_EXEMPT_PATHS = {"/api/android-app", "/api/status", "/api/countries", "/api/rate"}


@app.before_request
def require_current_android_app():
    """La app Android manda X-App-Version; las versiones viejas (≤1.14) no lo mandan pero se identifican por okhttp.
    Si la versión es menor que la mínima, no se entregan datos y la app muestra el aviso de actualizar."""
    if not request.path.startswith("/api/") or request.path in UPDATE_EXEMPT_PATHS:
        return None
    raw = request.headers.get("X-App-Version")
    if raw is None and "okhttp" not in request.headers.get("User-Agent", "").lower():
        return None  # navegador: la web siempre está al día
    try:
        installed = int(raw) if raw is not None else 0
    except ValueError:
        installed = 0
    minimum = android_min_version_code()
    if installed >= minimum:
        return None
    info = android_version()
    return jsonify(error=f"Hay una versión nueva de Quincena VE ({info['version']}). Actualízala para seguir usándola: toca Actualizar en Inicio.",
                   code="update_required", minVersionCode=minimum, version=info["version"],
                   url=f"/descargar/android?v={info['versionCode']}"), 426


@app.get("/api/android-app")
def android_app_info():
    if not APK_PATH.is_file():
        return jsonify(available=False)
    info = android_version()
    return jsonify(available=True, version=info["version"], versionCode=info["versionCode"], notes=info["notes"],
                   minVersionCode=android_min_version_code(),
                   size_mb=round(APK_PATH.stat().st_size / 1048576, 1), url=f"/descargar/android?v={info['versionCode']}")


@app.get("/descargar/android")
def download_android():
    if not APK_PATH.is_file():
        abort(404)
    # El nombre lleva la versión (Quincena-1.7.apk) para no confundirlo con descargas viejas.
    version = re.sub(r"[^0-9A-Za-z.]", "", android_version()["version"]) or "nueva"
    return send_from_directory(APK_PATH.parent, APK_PATH.name, mimetype="application/vnd.android.package-archive",
                               as_attachment=True, download_name=f"Quincena-{version}.apk", max_age=0)


TERMS_VERSION = "2026-10-23"


@app.get("/api/devices")
@require_auth()
def list_devices():
    keep = current_bearer_hash()
    now = datetime.now(timezone.utc)
    rows = DeviceToken.query.filter_by(user_id=g.user.id, revoked_at=None).order_by(DeviceToken.last_used_at.desc()).all()
    return jsonify(devices=[{
        "id": r.id, "label": r.label, "this": r.token_hash == keep,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
    } for r in rows if _aware(r.expires_at) > now])


@app.post("/api/devices/revoke-others")
@limiter.limit("10 per hour")
@require_auth()
def revoke_devices():
    n = revoke_other_devices(g.user.id)
    db.session.commit()
    audit("devices_revoked", f"Sesión cerrada en {n} dispositivo(s)", g.user.id)
    if n:
        notify_security(g.user, "Se cerraron sesiones en sus dispositivos – Quincena VE", f"Se cerró la sesión de Quincena VE en {n} dispositivo(s) de su cuenta, a petición suya.")
    return jsonify(ok=True, revoked=n)


@app.post("/api/accept-terms")
@require_auth()
def accept_terms():
    version = str((request.get_json(silent=True) or {}).get("version", ""))[:20]
    if version != TERMS_VERSION:
        return jsonify(error="Versión de términos no válida."), 400
    audit("terms_accepted", f"Aceptó términos, privacidad y cookies (versión {version})", g.user.id)
    return jsonify(ok=True, version=version)


@app.get("/terminos")
@app.get("/privacidad")
@app.get("/cookies")
def legal_pages():
    html = (Path(app.static_folder) / "legal.html").read_text(encoding="utf-8")
    html = html.replace("{{CONTACTO}}", html_escape(os.getenv("CONTACT_EMAIL", "el correo de contacto indicado en la app")))
    html = html.replace("{{VERSION}}", TERMS_VERSION).replace("{{SECCION}}", request.path.strip("/"))
    response = app.response_class(html, mimetype="text/html")
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["X-Robots-Tag"] = "index, follow"
    return response


@limiter.exempt
@app.get("/healthz")
def healthz():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify(ok=True)
    except Exception:
        app.logger.exception("Health check de base de datos falló")
        return jsonify(ok=False), 503


@app.get("/api/status")
def status():
    required = setup_required()
    user = current_user()
    if user and not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(
        setup_required=required,
        authenticated=bool(user),
        csrf_token=session.get("csrf_token"),
        user={"username": user.username, "role": user.role, "must_change_password": user.must_change_password} if user else None,
        app_name="Quincena VE", registration_open=registration_open(),
    )


@app.post("/api/setup")
@limiter.limit("5 per hour")
def setup_admin():
    if not setup_required():
        return jsonify(error="La configuración inicial ya se completó."), 409
    configured_token = os.getenv("BOOTSTRAP_TOKEN", "")
    if not configured_token or len(configured_token) < 24:
        return jsonify(error="Falta configurar BOOTSTRAP_TOKEN con al menos 24 caracteres en el entorno."), 503
    body = request.get_json(silent=True) or {}
    supplied = str(body.get("bootstrap_token", ""))
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    if not hmac.compare_digest(supplied, configured_token):
        audit("setup_failed", "Token inicial inválido", user_id=None)
        return jsonify(error="El código inicial no es correcto."), 403
    if len(username) < 3 or len(username) > 40 or not all(c.isalnum() or c in "._-" for c in username):
        return jsonify(error="El usuario debe tener entre 3 y 40 caracteres: letras, números, punto, guion o guion bajo."), 400
    if password_problem(password, username):
        return jsonify(error=password_problem(password, username)), 400
    if User.query.first() is not None:
        return jsonify(error="La configuración inicial ya fue completada por otro usuario."), 409
    user = User(username=username, password_hash=generate_password_hash(password), role="admin", must_change_password=False)
    db.session.add(user)
    db.session.flush()
    # Never reset any financial state while creating the first account. If a
    # migrated orphan exists it will be claimed by ensure_user_state below.
    db.session.commit()
    ensure_user_state(user.id)
    session.clear()
    session["uid"] = user.id
    session["ps"] = pw_stamp(user)
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    audit("setup_completed", "Cuenta administradora inicial creada", user.id)
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": user.username, "role": user.role, "must_change_password": False})


@app.post("/api/login")
@limiter.limit("10 per minute")
@limiter.limit("30 per hour", key_func=_login_name_key)  # frena también los ataques repartidos entre muchas IP
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    user = User.query.filter_by(username=username, active=True).first()
    if not verify_login(user, password):
        # No se guarda lo que se escribió si el usuario no existe: a veces la gente pega aquí su contraseña.
        audit("login_failed", f"Usuario: {user.username}" if user else "Usuario inexistente", user_id=user.id if user else None)
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    session.clear()
    session["uid"] = user.id
    session["ps"] = pw_stamp(user)
    session["seen"] = time.time()
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    audit("login_succeeded", "Inicio de sesión correcto", user.id)
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": user.username, "role": user.role, "must_change_password": user.must_change_password})


@app.get("/api/passkeys/status")
@require_auth()
def passkeys_status():
    credentials = UserPasskey.query.filter_by(user_id=g.user.id).order_by(UserPasskey.created_at.asc()).all()
    return jsonify(available=True, credentials=[{"id": item.id, "label": item.label, "created_at": item.created_at.isoformat()} for item in credentials])


@app.post("/api/passkeys/register/options")
@limiter.limit("5 per hour")
@require_auth()
def passkey_registration_options():
    if g.user.must_change_password:
        return jsonify(error="Cambia primero la contraseña temporal."), 428
    try:
        rp_id, _origin = webauthn_settings()
        existing = UserPasskey.query.filter_by(user_id=g.user.id).all()
        options = generate_registration_options(
            rp_id=rp_id, rp_name="Control Financiero",
            user_id=str(g.user.id).encode("utf-8"),
            user_name=g.user.username, user_display_name=g.user.username,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id)) for item in existing],
        )
        session["passkey_register_challenge"] = bytes_to_base64url(options.challenge)
        session["passkey_register_user_id"] = g.user.id
        return jsonify(options=json.loads(options_to_json(options)))
    except Exception:
        app.logger.exception("No se pudieron generar opciones de registro WebAuthn")
        return jsonify(error="No se pudo iniciar la configuración biométrica. Verifica el origen HTTPS de la aplicación."), 503


@app.post("/api/passkeys/register/verify")
@limiter.limit("5 per hour")
@require_auth()
def passkey_registration_verify():
    if session.get("passkey_register_user_id") != g.user.id:
        return jsonify(error="La solicitud de registro venció. Inténtalo otra vez."), 400
    challenge_text = session.pop("passkey_register_challenge", None)
    session.pop("passkey_register_user_id", None)
    body = request.get_json(silent=True) or {}
    credential = body.get("credential")
    if not challenge_text or not isinstance(credential, dict):
        return jsonify(error="Falta la credencial de seguridad o el reto venció."), 400
    try:
        rp_id, origin = webauthn_settings()
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_text),
            expected_rp_id=rp_id, expected_origin=origin,
            require_user_verification=True,
        )
        credential_id = bytes_to_base64url(verification.credential_id)
        existing = UserPasskey.query.filter_by(credential_id=credential_id).first()
        if existing and existing.user_id != g.user.id:
            return jsonify(error="Esta credencial ya está asociada a otra cuenta."), 409
        if existing:
            existing.public_key = bytes_to_base64url(verification.credential_public_key)
            existing.sign_count = int(verification.sign_count or 0)
            existing.label = str(body.get("label") or existing.label or "Este dispositivo")[:80]
        else:
            db.session.add(UserPasskey(
                user_id=g.user.id, credential_id=credential_id,
                public_key=bytes_to_base64url(verification.credential_public_key),
                sign_count=int(verification.sign_count or 0),
                label=str(body.get("label") or "Este dispositivo")[:80],
            ))
        db.session.commit()
        audit("passkey_registered", "Acceso biométrico registrado", g.user.id)
        return jsonify(ok=True, message="Acceso biométrico configurado en este dispositivo.")
    except Exception:
        db.session.rollback()
        app.logger.exception("Falló la verificación de registro WebAuthn para usuario %s", g.user.id)
        return jsonify(error="No se pudo verificar el registro biométrico. Vuelve a intentarlo."), 400


@app.delete("/api/passkeys/<int:credential_id>")
@require_auth()
def passkey_delete(credential_id: int):
    item = UserPasskey.query.filter_by(id=credential_id, user_id=g.user.id).first()
    if item is None:
        return jsonify(error="Credencial no encontrada para esta cuenta."), 404
    db.session.delete(item)
    db.session.commit()
    audit("passkey_removed", "Acceso biométrico eliminado", g.user.id)
    return jsonify(ok=True)


@app.post("/api/passkeys/login/options")
@limiter.limit("10 per minute")
def passkey_login_options():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    user = User.query.filter_by(username=username, active=True).first()
    if not user or user.must_change_password:
        return jsonify(error="Inicia sesión con tu contraseña para terminar la configuración de la cuenta."), 401
    credentials = UserPasskey.query.filter_by(user_id=user.id).all()
    if not credentials:
        return jsonify(error="Esta cuenta aún no tiene acceso biométrico registrado. Entra con contraseña y actívalo desde la aplicación."), 404
    try:
        rp_id, _origin = webauthn_settings()
        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id)) for item in credentials],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        session["passkey_login_challenge"] = bytes_to_base64url(options.challenge)
        session["passkey_login_user_id"] = user.id
        return jsonify(options=json.loads(options_to_json(options)))
    except Exception:
        app.logger.exception("No se pudieron generar opciones de autenticación WebAuthn")
        return jsonify(error="No se pudo iniciar el acceso biométrico. Verifica la configuración HTTPS."), 503


@app.post("/api/passkeys/login/verify")
@limiter.limit("10 per minute")
def passkey_login_verify():
    challenge_text = session.pop("passkey_login_challenge", None)
    user_id = session.pop("passkey_login_user_id", None)
    body = request.get_json(silent=True) or {}
    credential = body.get("credential")
    if not challenge_text or not user_id or not isinstance(credential, dict):
        return jsonify(error="La solicitud biométrica venció. Vuelve a intentarlo."), 400
    credential_id = str(credential.get("rawId") or credential.get("id") or "")
    passkey = UserPasskey.query.filter_by(user_id=user_id, credential_id=credential_id).first()
    user = db.session.get(User, user_id)
    if not passkey or not user or not user.active or user.must_change_password:
        return jsonify(error="No se pudo validar la credencial para esta cuenta."), 401
    try:
        rp_id, origin = webauthn_settings()
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_text),
            expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=int(passkey.sign_count or 0),
            require_user_verification=True,
        )
        passkey.sign_count = int(verification.new_sign_count or 0)
        db.session.commit()
        return jsonify(establish_user_session(user))
    except Exception:
        db.session.rollback()
        app.logger.warning("Falló autenticación WebAuthn", exc_info=True)
        return jsonify(error="No se pudo verificar la huella, rostro o passkey. Usa contraseña o vuelve a intentarlo."), 401


@app.get("/api/push/public-key")
def push_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if not key:
        return jsonify(error="Las notificaciones push aún no están configuradas en el servidor."), 503
    return jsonify(public_key=key)


@app.post("/api/push/subscribe")
@require_auth()
def push_subscribe():
    body = request.get_json(silent=True) or {}
    subscription = body.get("subscription")
    if not isinstance(subscription, dict):
        return jsonify(error="La suscripción de notificaciones no es válida."), 400
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    if not endpoint.startswith("https://") or len(endpoint) > 2048 or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify(error="El navegador no entregó una suscripción push válida."), 400
    record = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if record is None:
        record = PushSubscription(user_id=g.user.id, endpoint=endpoint, subscription_json=subscription)
        db.session.add(record)
    else:
        record.user_id = g.user.id
        record.subscription_json = subscription
    db.session.commit()
    audit("payment_push_enabled", "Recordatorios push habilitados en un dispositivo", g.user.id)
    return jsonify(ok=True, message="Recordatorios habilitados para este dispositivo.")


@app.delete("/api/push/subscribe")
@require_auth()
def push_unsubscribe():
    body = request.get_json(silent=True) or {}
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        return jsonify(error="Falta el endpoint del dispositivo."), 400
    record = PushSubscription.query.filter_by(endpoint=endpoint, user_id=g.user.id).first()
    if record:
        db.session.delete(record)
        db.session.commit()
    return jsonify(ok=True)


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def registration_open() -> bool:
    return os.getenv("ALLOW_REGISTRATION", "false").lower() == "true" and not setup_required()


def mail_configured() -> bool:
    return bool(os.getenv("BREVO_API_KEY", "").strip() or (os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")))


def mail_problem() -> str | None:
    """Qué falta para que salgan los correos con enlace (verificación y recuperación), o None si está todo."""
    if not mail_configured():
        return "falta BREVO_API_KEY en Render"
    if os.getenv("BREVO_API_KEY", "").strip() and not parseaddr(os.getenv("MAIL_FROM", ""))[1]:
        return "falta MAIL_FROM (remitente verificado en Brevo, p. ej. Quincena VE <tu@correo.com>)"
    if not os.getenv("APP_PUBLIC_ORIGIN", "").strip() and os.getenv("RENDER"):
        return "falta APP_PUBLIC_ORIGIN (p. ej. https://control-financiero-wrm5.onrender.com)"
    return None


def brevo_send(api_key: str, to: str, subject: str, body: str, html_body: str | None = None) -> tuple[bool, str]:
    """Correo transaccional por la API HTTPS de Brevo (POST /v3/smtp/email). Devuelve (enviado, detalle)."""
    name, addr = parseaddr(os.getenv("MAIL_FROM", ""))
    if not addr:
        return False, "MAIL_FROM vacío o inválido: debe ser un remitente verificado en Brevo, p. ej. Quincena VE <tu@correo.com>."
    payload = {"sender": {"name": name or "Quincena VE", "email": addr}, "to": [{"email": to}], "subject": subject, "textContent": body}
    if html_body:
        payload["htmlContent"] = html_body
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            try:
                answer = json.loads(response.read() or b"{}")
            except Exception:
                answer = {}
            return 200 <= response.status < 300, f"HTTP {response.status}, messageId {answer.get('messageId', '?')}"
    except urllib.error.HTTPError as error:
        try:
            answer = json.loads(error.read() or b"{}")
        except Exception:
            answer = {}
        # Solo el código y el mensaje de Brevo (p. ej. «unauthorized: Key not found»), nunca la clave.
        detail = f"HTTP {error.code}: {str(answer.get('code', ''))[:60]}: {str(answer.get('message', ''))[:300]}"
        return False, detail.replace(api_key, "***") if api_key else detail
    except Exception as error:
        return False, f"No se pudo contactar a Brevo ({type(error).__name__})."


def send_mail(to: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Envía un correo. Prefiere la API HTTPS de Brevo (funciona en el plan gratis de Render, que bloquea SMTP);
    si no hay BREVO_API_KEY, usa SMTP de Gmail con contraseña de aplicación."""
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    if api_key:
        sent, detail = brevo_send(api_key, to, subject, body, html_body)
        if not sent:
            app.logger.error("Brevo no envió el correo «%s»: %s", subject, detail)
        return sent
    smtp_user, smtp_password = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_password:
        return False
    message = EmailMessage()
    message["Subject"], message["To"] = subject, to
    message["From"] = os.getenv("MAIL_FROM") or f"Quincena VE <{smtp_user}>"
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.warning("No se pudo enviar el correo por SMTP (Render gratis bloquea SMTP: usa BREVO_API_KEY).")
        return False


def send_mail_async(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """Envía en segundo plano: la respuesta tarda igual exista o no el correo (no se puede adivinar quién tiene cuenta)."""
    threading.Thread(target=lambda: send_mail(to, subject, body, html_body), daemon=True).start()


def public_origin() -> str:
    """Dirección pública para los enlaces de los correos. En producción solo la configurada: si se usara la cabecera Host
    que manda quien hace la petición, alguien podría mandar a la víctima un enlace a un sitio falso."""
    configured = os.getenv("APP_PUBLIC_ORIGIN", "").rstrip("/")
    if configured:
        return configured
    return "" if os.getenv("RENDER") else request.host_url.rstrip("/")


def notify_security(user: "User", subject: str, message: str) -> None:
    """Aviso de seguridad al correo confirmado de la persona (cambio de clave, nuevo dispositivo…)."""
    if not user.email or not user.email_verified_at or not mail_configured():
        return
    when = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "America/Caracas"))).strftime("%d/%m/%Y %H:%M")
    tail = ("Si usted no fue, restablezca su contraseña de inmediato desde la pantalla de acceso («¿Olvidaste tu contraseña?») "
            "y cierre las sesiones de sus otros dispositivos desde Más.")
    text = f"Estimado/a {user.username}:\n\n{message}\nFecha: {when} (hora de Caracas).\n\n{tail}\n\nAtentamente,\nEl equipo de Quincena VE"
    page = email_layout(subject, f'<p style="margin:0 0 16px">Estimado/a <b>{html_escape(user.username)}</b>:</p>'
                        f'<p style="margin:0 0 16px;padding:14px 16px;background:#BFF0D8;border-radius:12px;color:#0B3D2C">{html_escape(message)}<br>'
                        f'<span style="font-size:14px">Fecha: {when} (hora de Caracas).</span></p>'
                        f'<p style="margin:0 0 16px">{html_escape(tail)}</p><p style="margin:0">Atentamente,<br><b>El equipo de Quincena VE</b></p>', message)
    send_mail_async(user.email, subject, text, page)


def email_layout(title: str, inner_html: str, preheader: str = "") -> str:
    """Plantilla de todos los correos con la marca de Quincena VE: logo, franja con la bandera, verde de la marca y pie
    con la dirección del sitio. Tablas y estilos en línea para que se vea igual en Gmail, Outlook e iCloud."""
    origin = public_origin() if request else os.getenv("APP_PUBLIC_ORIGIN", "").rstrip("/")
    logo = (f'<img src="{html_escape(origin, quote=True)}/static/icons/icon-192.png" width="44" height="44" alt="" '
            'style="display:block;border:0;border-radius:12px">') if origin else ""
    site = (f'<a href="{html_escape(origin, quote=True)}" style="color:#0B6B4D;font-weight:bold;text-decoration:none">'
            f'{html_escape(origin.split("://")[-1])}</a> · ') if origin else ""
    font = "'Figtree',Arial,Helvetica,sans-serif"
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html_escape(title)}</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700&family=Figtree:wght@400;600;700&display=swap" rel="stylesheet">'
        f'</head><body style="margin:0;padding:0;background:#EEF5F0;font-family:{font};color:#12211B">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html_escape(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF5F0"><tr><td align="center" style="padding:28px 12px">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border:1px solid #D5E3DA;border-radius:16px;overflow:hidden">'
        '<tr><td style="padding:0;font-size:0;line-height:0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td height="6" style="background:#FFCC00;width:33.33%"></td><td height="6" style="background:#00247D;width:33.34%"></td>'
        '<td height="6" style="background:#CF142B;width:33.33%"></td></tr></table></td></tr>'
        '<tr><td style="padding:22px 28px 18px;border-bottom:1px solid #E3ECE6"><table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-right:{12 if logo else 0}px">{logo}</td>'
        "<td style=\"font-family:'Bricolage Grotesque',Arial,Helvetica,sans-serif;font-size:22px;font-weight:bold;color:#0B6B4D\">Quincena VE 🇻🇪"
        f'<div style="font-family:{font};font-size:13px;font-weight:normal;color:#46564E">Tus gastos en dólares y bolívares</div></td>'
        '</tr></table></td></tr>'
        f'<tr><td style="padding:28px;font-size:16px;line-height:1.6">{inner_html}</td></tr>'
        '<tr><td style="padding:18px 28px;background:#F5FAF7;border-top:1px solid #E3ECE6;font-size:12px;line-height:1.6;color:#46564E">'
        'Por su seguridad, Quincena VE nunca le solicitará su contraseña por correo electrónico ni por teléfono. '
        'Este es un mensaje automático; por favor, no responda a este correo.<br>'
        f'{site}Hecho en Venezuela por Gigadevs</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def email_button(link: str, label: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" align="center" style="margin:4px auto 24px"><tr>'
            f'<td style="background:#0B6B4D;border-radius:12px"><a href="{html_escape(link, quote=True)}" '
            f'style="display:inline-block;padding:14px 30px;color:#ffffff;font-weight:bold;font-size:16px;text-decoration:none">{html_escape(label)}</a></td></tr></table>')


def reset_email(username: str, link: str, validity: str = "60 minutos", intro: str = "Hemos recibido una solicitud para restablecer la contraseña de su cuenta en Quincena VE.", cta: str = "Restablecer contraseña", ignore: str = "Si usted no realizó esta solicitud, puede ignorar este mensaje; su contraseña actual no será modificada.") -> tuple[str, str]:
    """Correo formal de restablecimiento de contraseña: (texto plano, HTML)."""
    text = (
        f"Estimado/a {username}:\n\n"
        f"{intro}\n\n"
        f"Para continuar, abra el siguiente enlace. Tendrá una validez de {validity} y solo podrá utilizarse una vez:\n\n{link}\n\n"
        f"{ignore}\n\n"
        "Por su seguridad, Quincena VE nunca le solicitará su contraseña por correo electrónico ni por teléfono.\n\n"
        "Atentamente,\nEl equipo de Quincena VE"
    )
    name, url = html_escape(username), html_escape(link, quote=True)
    inner = (
        f'<p style="margin:0 0 16px">Estimado/a <b>{name}</b>:</p>'
        f'<p style="margin:0 0 16px">{html_escape(intro)}</p>'
        f'<p style="margin:0 0 24px">Para continuar, presione el siguiente botón. El enlace tiene una validez de <b>{validity}</b> y solo podrá utilizarse una vez.</p>'
        + email_button(link, cta) +
        f'<p style="margin:0 0 16px;padding:12px 14px;background:#F5FAF7;border-radius:10px;font-size:13px;color:#46564E">Si el botón no funciona, copie y pegue esta dirección en su navegador:<br><span style="word-break:break-all;color:#0B6B4D">{url}</span></p>'
        f'<p style="margin:0 0 16px">{html_escape(ignore)}</p>'
        '<p style="margin:0">Atentamente,<br><b>El equipo de Quincena VE</b></p>'
    )
    page = email_layout(cta, inner, intro)
    return text, page


def start_session(user: "User"):
    session.clear()
    session["uid"] = user.id
    session["ps"] = pw_stamp(user)
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": user.username, "role": user.role, "must_change_password": user.must_change_password})


@app.post("/api/register")
@limiter.limit("5 per hour")
def register():
    if not registration_open():
        return jsonify(error="El registro no está disponible por ahora."), 403
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if body.get("accept_terms") is not True:
        return jsonify(error="Debes aceptar los términos y la política de privacidad para crear tu cuenta."), 400
    if len(username) < 3 or len(username) > 40 or not all(c.isalnum() or c in "._-" for c in username):
        return jsonify(error="El usuario debe tener entre 3 y 40 caracteres: letras, números, punto, guion o guion bajo."), 400
    if len(email) > 254 or not EMAIL_RE.match(email):
        return jsonify(error="Escribe un correo válido."), 400
    if password_problem(password, username):
        return jsonify(error=password_problem(password, username)), 400
    if User.query.filter_by(username=username).first():
        return jsonify(error="Ese usuario ya existe. Elige otro."), 409
    if User.query.filter(db.func.lower(User.email) == email).first():
        return jsonify(error="Ya existe una cuenta con ese correo."), 409
    source = clean_source(body.get("source"), request.headers.get("User-Agent", ""))
    user = User(username=username, email=email, password_hash=generate_password_hash(password), role="member", must_change_password=False, signup_source=source)
    db.session.add(user)
    db.session.commit()
    record = ensure_user_state(user.id)
    country = clean_country(body.get("country"))
    record.data = {**copy.deepcopy(DEFAULT_SHARED_DATA), "country": country, "exchangeRate": COUNTRIES[country]["rate"]}
    db.session.commit()
    audit("account_registered", f"Cuenta creada ({country}, origen {source or 'directo'}); aceptó términos, privacidad y cookies (versión 2026-10-23)", user.id)
    send_verification(user)
    return start_session(user)


def send_verification(user: "User") -> bool:
    token = secrets.token_urlsafe(32)
    EmailVerification.query.filter_by(user_id=user.id, used_at=None).delete()
    db.session.add(EmailVerification(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    db.session.commit()
    origin = public_origin()
    if not origin or not mail_configured():
        app.logger.error("Correo de verificación no enviado: %s.", mail_problem() or "sin dirección pública")
        return False
    # El código va tras "#": el navegador nunca lo envía al servidor, así no queda en registros ni en cabeceras Referer.
    text_body, html_body = reset_email(user.username, f"{origin}/app#verify={token}", "24 horas",
                                       "Gracias por crear su cuenta en Quincena VE. Para activar la recuperación de su contraseña, necesitamos confirmar su correo electrónico.",
                                       "Confirmar correo", "Si usted no creó esta cuenta, puede ignorar este mensaje.")
    send_mail_async(user.email, "Confirme su correo electrónico – Quincena VE", text_body, html_body)
    return True


@app.post("/api/verify-email")
@limiter.limit("20 per hour")
def verify_email():
    token = str((request.get_json(silent=True) or {}).get("token", ""))
    row = EmailVerification.query.filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    now = datetime.now(timezone.utc)
    expires = (row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at) if row else None
    user = db.session.get(User, row.user_id) if row else None
    if not row or row.used_at is not None or expires < now or not user:
        return jsonify(error="El enlace venció o ya se usó. Pide uno nuevo desde la app."), 400
    user.email_verified_at = now
    row.used_at = now
    db.session.commit()
    audit("email_verified", "Correo confirmado", user.id)
    return jsonify(ok=True)


@app.get("/api/email-status")
@require_auth()
def email_status():
    return jsonify(email=g.user.email, verified=bool(g.user.email_verified_at))


@app.post("/api/resend-verification")
@limiter.limit("3 per hour")
@require_auth()
def resend_verification():
    if not g.user.email or g.user.email_verified_at:
        return jsonify(error="Tu correo ya está confirmado."), 400
    return jsonify(ok=True, sent=send_verification(g.user))


@app.post("/api/mobile/login")
@limiter.limit("10 per minute")
@limiter.limit("30 per hour", key_func=_login_name_key)
def mobile_login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    user = User.query.filter_by(username=username, active=True).first()
    if not verify_login(user, str(body.get("password", ""))):
        audit("login_failed", f"App móvil. Usuario: {user.username}" if user else "App móvil. Usuario inexistente", user_id=user.id if user else None)
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    if user.must_change_password:
        return jsonify(error="Cambia tu contraseña desde la versión web antes de usar la app."), 428
    token, now = secrets.token_urlsafe(48), datetime.now(timezone.utc)
    db.session.add(DeviceToken(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), label=str(body.get("device") or "Android")[:80],
                               last_used_at=now, expires_at=now + timedelta(days=90)))
    db.session.commit()
    audit("login_succeeded", "Inicio de sesión desde la app móvil", user.id)
    notify_security(user, "Nuevo inicio de sesión en su cuenta – Quincena VE",
                    f"Se inició sesión en su cuenta desde la app de Android ({str(body.get('device') or 'dispositivo').strip()[:60]}).")
    return jsonify(ok=True, token=token, user={"username": user.username, "role": user.role})


@app.post("/api/mobile/logout")
@require_auth()
def mobile_logout():
    header = request.headers.get("Authorization", "")
    row = DeviceToken.query.filter_by(token_hash=hashlib.sha256(header[7:].strip().encode()).hexdigest()).first() if header.startswith("Bearer ") else None
    if row is not None:
        row.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    return jsonify(ok=True)


@app.post("/api/forgot-password")
@limiter.limit("5 per hour")
@limiter.limit("3 per hour", key_func=_forgot_email_key)  # nadie puede llenar de correos la bandeja de otra persona
def forgot_password():
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    user = User.query.filter(db.func.lower(User.email) == email, User.active.is_(True), User.email_verified_at.isnot(None)).first() if EMAIL_RE.match(email) else None
    if user:
        PasswordReset.query.filter_by(user_id=user.id, used_at=None).delete()
        token = secrets.token_urlsafe(32)
        db.session.add(PasswordReset(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(minutes=60)))
        db.session.commit()
        origin = public_origin()
        if origin and mail_configured():
            text_body, html_body = reset_email(user.username, f"{origin}/app#reset={token}")
            send_mail_async(email, "Restablecimiento de contraseña – Quincena VE", text_body, html_body)
            audit("password_reset_requested", "Enlace solicitado", user.id)
        else:
            app.logger.error("Recuperación de clave no enviada: %s.", mail_problem() or "sin dirección pública")
            audit("password_reset_requested", "No se pudo enviar: el correo no está configurado", user.id)
    return jsonify(ok=True)  # misma respuesta exista o no el correo


@app.post("/api/reset-password")
@limiter.limit("10 per hour")
def reset_password():
    body = request.get_json(silent=True) or {}
    new_password = str(body.get("new_password", ""))
    if password_problem(new_password):
        return jsonify(error=password_problem(new_password)), 400
    row = PasswordReset.query.filter_by(token_hash=hashlib.sha256(str(body.get("token", "")).encode()).hexdigest()).first()
    now = datetime.now(timezone.utc)
    expires = row.expires_at.replace(tzinfo=timezone.utc) if row and row.expires_at.tzinfo is None else (row.expires_at if row else None)
    user = db.session.get(User, row.user_id) if row else None
    if not row or row.used_at is not None or expires < now or not user or not user.active:
        return jsonify(error="El enlace venció o ya se usó. Pide uno nuevo."), 400
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = False
    row.used_at = now
    DeviceToken.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": now})
    db.session.commit()
    session.clear()
    audit("password_reset_done", "Contraseña restablecida por correo", user.id)
    notify_security(user, "Su contraseña fue restablecida – Quincena VE", "La contraseña de su cuenta fue restablecida con el enlace enviado a su correo. Se cerró la sesión en todos sus dispositivos.")
    return jsonify(ok=True)


@app.post("/api/logout")
@require_auth(allow_password_change=True)
def logout():
    audit("logout", "Sesión cerrada", g.user.id)
    session.clear()
    return jsonify(ok=True)


@app.post("/api/change-password")
@require_auth(allow_password_change=True)
def change_password():
    body = request.get_json(silent=True) or {}
    old_password = str(body.get("old_password", ""))
    new_password = str(body.get("new_password", ""))
    if not check_password_hash(g.user.password_hash, old_password):
        return jsonify(error="La contraseña actual no coincide."), 400
    if password_problem(new_password, g.user.username):
        return jsonify(error=password_problem(new_password, g.user.username)), 400
    if new_password == old_password:
        return jsonify(error="La nueva contraseña debe ser diferente."), 400
    g.user.password_hash = generate_password_hash(new_password)
    g.user.must_change_password = False
    revoke_other_devices(g.user.id)
    db.session.commit()
    if not getattr(g, "via_token", False):
        session["ps"] = pw_stamp(g.user)  # esta sesión sigue abierta; las demás sesiones web quedan cerradas
    session["csrf_token"] = secrets.token_urlsafe(32)
    audit("password_changed", "Contraseña actualizada; se cerró la sesión en los demás dispositivos", g.user.id)
    notify_security(g.user, "Su contraseña fue cambiada – Quincena VE", "La contraseña de su cuenta fue cambiada. Se cerró la sesión en sus demás dispositivos.")
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": g.user.username, "role": g.user.role, "must_change_password": False})


@app.get("/api/state")
@require_auth()
def get_state():
    record = ensure_user_state(g.user.id)
    return jsonify(data=normalize_shared_data(record.data), revision=record.revision, updated_at=record.updated_at.isoformat() if record.updated_at else None)


@app.put("/api/state")
@limiter.limit("120 per minute")
@require_auth()
def put_state():
    body = request.get_json(silent=True) or {}
    if "data" not in body or "base_data" not in body or "base_revision" not in body:
        return jsonify(error="Faltan los datos base para sincronizar de forma segura."), 400
    try:
        incoming = normalize_shared_data(body["data"])
        base_data = normalize_shared_data(body["base_data"])
        base_revision = int(body["base_revision"])
    except (ValueError, TypeError, OverflowError):
        return jsonify(error="Los datos enviados no son válidos."), 400
    record = db.session.query(SharedState).filter_by(user_id=g.user.id).with_for_update().first()
    if record is None:
        record = SharedState(user_id=g.user.id, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA), updated_by=g.user.id)
        db.session.add(record)
        db.session.flush()
    current = normalize_shared_data(record.data)
    if record.revision == base_revision:
        merged, conflicts = incoming, []
    else:
        merged, conflicts = three_way_merge(base_data, incoming, current)
    # Every authenticated account owns this row, so each user can manage their
    # own income, expenses, debts, school records and settings. Account
    # administration remains separately restricted by require_auth(admin=True).
    if conflicts:
        db.session.rollback()
        latest = ensure_user_state(g.user.id)
        return jsonify(error="Otra persona modificó los mismos datos. Se cargó la versión más reciente; revisa y vuelve a aplicar tu cambio.", conflicts=conflicts, data=normalize_shared_data(latest.data), revision=latest.revision), 409
    school_error = validate_school_balances(merged)
    if school_error:
        db.session.rollback()
        return jsonify(error=school_error), 400
    record.data = merged
    record.revision += 1
    record.updated_at = datetime.now(timezone.utc)
    record.updated_by = g.user.id
    db.session.commit()
    audit("shared_data_updated", f"Revisión {record.revision}", g.user.id)
    return jsonify(ok=True, data=normalize_shared_data(record.data), revision=record.revision, updated_at=record.updated_at.isoformat())


@app.post("/api/import-legacy")
@require_auth()
def import_legacy_state():
    body = request.get_json(silent=True) or {}
    try:
        incoming = normalize_shared_data(body.get("data"))
    except (ValueError, TypeError, OverflowError):
        return jsonify(error="El archivo de respaldo no contiene datos válidos."), 400
    record = db.session.query(SharedState).filter_by(user_id=g.user.id).with_for_update().first()
    if record is None:
        record = SharedState(user_id=g.user.id, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA), updated_by=g.user.id)
        db.session.add(record)
        db.session.flush()
    current = normalize_shared_data(record.data)
    school_error = validate_school_balances(incoming)
    if school_error:
        db.session.rollback()
        return jsonify(error=school_error), 400
    financially_empty = (
        not current["expenses"] and not current["debts"] and not current["incomeSources"] and
        not current["schoolExpenses"] and not current["schoolPayments"] and not current["plannedDebtPayments"] and
        float(current["income"].get("amount", 0) or 0) == 0 and
        all(float(value or 0) == 0 for value in current["budgets"].values()) and
        not current["debtPlan"].get("configured", False)
    )
    incoming_has_data = bool(incoming["expenses"] or incoming["debts"] or incoming["incomeSources"] or incoming["schoolExpenses"] or incoming["schoolPayments"] or incoming["plannedDebtPayments"] or float(incoming["income"].get("amount", 0) or 0) or any(float(v or 0) for v in incoming["budgets"].values()) or incoming["debtPlan"].get("configured", False))
    if not financially_empty:
        db.session.rollback()
        return jsonify(error="La nube ya contiene información. Por seguridad, la importación de un respaldo antiguo solo se permite cuando ingresos, presupuestos, gastos y deudas están vacíos."), 409
    if not incoming_has_data:
        return jsonify(error="El respaldo no contiene registros financieros para importar."), 400
    record.data = incoming
    record.revision += 1
    record.updated_at = datetime.now(timezone.utc)
    record.updated_by = g.user.id
    db.session.commit()
    audit("legacy_data_imported", f"Importación de respaldo, revisión {record.revision}", g.user.id)
    return jsonify(ok=True, data=normalize_shared_data(record.data), revision=record.revision, updated_at=record.updated_at.isoformat())


@app.get("/api/users")
@require_auth(admin=True)
def list_users():
    users = User.query.order_by(User.created_at.asc()).all()
    return jsonify(users=[{"id": u.id, "username": u.username, "role": u.role, "active": u.active, "must_change_password": u.must_change_password, "created_at": u.created_at.isoformat() if u.created_at else None, "signup_source": u.signup_source} for u in users])


@app.get("/api/signups")
@require_auth(admin=True)
def signups_by_source():
    """Cuentas nuevas por origen (publicación o anuncio), para saber qué funciona antes de pagar publicidad."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = {}
    for source, created_at in db.session.query(User.signup_source, User.created_at).filter(User.role == "member").all():
        row = rows.setdefault(source or "directo", {"source": source or "directo", "total": 0, "last_7_days": 0})
        row["total"] += 1
        if created_at and (created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)) >= since:
            row["last_7_days"] += 1
    by_source = sorted(rows.values(), key=lambda r: (-r["total"], r["source"]))
    return jsonify(total=sum(r["total"] for r in by_source), by_source=by_source)


@app.post("/api/mail-test")
@require_auth(admin=True)
@limiter.limit("10 per hour")
def mail_test():
    """Solo administrador: envía un correo de prueba y devuelve la configuración detectada y la respuesta de Brevo."""
    to = str((request.get_json(silent=True) or {}).get("to") or g.user.email or "").strip().lower()
    if not EMAIL_RE.match(to):
        return jsonify(error="Indica un correo válido en «to»."), 400
    name, addr = parseaddr(os.getenv("MAIL_FROM", ""))
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    config = {"brevo_api_key": bool(api_key), "smtp": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")),
              "mail_from": f"{name} <{addr}>" if addr else None, "app_public_origin": os.getenv("APP_PUBLIC_ORIGIN", "").rstrip("/") or None,
              "problem": mail_problem()}
    subject, body = "Correo de prueba – Quincena VE", "Si recibe este mensaje, el envío de correos de Quincena VE funciona."
    page = email_layout(subject, '<p style="margin:0 0 16px"><b>¡Funciona!</b></p><p style="margin:0">Si recibe este mensaje, '
                        'el envío de correos de Quincena VE está bien configurado.</p>', body)
    if api_key:
        sent, detail = brevo_send(api_key, to, subject, body, page)
    else:
        sent = send_mail(to, subject, body, page)
        detail = "Enviado por SMTP." if sent else "No se pudo enviar por SMTP (revisa SMTP_USER y SMTP_PASSWORD)."
    if not sent:
        app.logger.error("Correo de prueba no enviado: %s", detail)
    audit("mail_test", f"Correo de prueba {'enviado' if sent else 'fallido'}", g.user.id)
    return jsonify(sent=sent, detail=detail, config=config), (200 if sent else 502)


@app.post("/api/users")
@require_auth(admin=True)
def create_user():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    role = str(body.get("role", "member"))
    if len(username) < 3 or len(username) > 40 or not all(c.isalnum() or c in "._-" for c in username):
        return jsonify(error="El usuario debe tener entre 3 y 40 caracteres: letras, números, punto, guion o guion bajo."), 400
    if role not in {"admin", "member"}:
        return jsonify(error="Rol no válido."), 400
    if User.query.filter_by(username=username).first():
        return jsonify(error="Ese nombre de usuario ya existe."), 409
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    temporary_password = "".join(secrets.choice(alphabet) for _ in range(18))
    user = User(username=username, password_hash=generate_password_hash(temporary_password), role=role, must_change_password=True, active=True)
    db.session.add(user)
    db.session.commit()
    audit("user_created", f"Cuenta {username} ({role}) creada", g.user.id)
    return jsonify(ok=True, user={"id": user.id, "username": user.username, "role": user.role}, temporary_password=temporary_password), 201


@app.patch("/api/users/<int:user_id>")
@require_auth(admin=True)
def change_user_status(user_id: int):
    body = request.get_json(silent=True) or {}
    user = db.session.get(User, user_id)
    if not user:
        return jsonify(error="Usuario no encontrado."), 404
    active = body.get("active")
    if not isinstance(active, bool):
        return jsonify(error="Se requiere el estado active true/false."), 400
    if user.id == g.user.id and not active:
        return jsonify(error="No puedes desactivar tu propia cuenta."), 400
    if user.role == "admin" and user.active and not active:
        other_admin = User.query.filter_by(role="admin", active=True).filter(User.id != user.id).count()
        if other_admin == 0:
            return jsonify(error="No puedes desactivar al último administrador."), 400
    user.active = active
    db.session.commit()
    audit("user_activated" if active else "user_deactivated", f"Cuenta {user.username}", g.user.id)
    return jsonify(ok=True)


@app.post("/api/users/<int:user_id>/reset-password")
@require_auth(admin=True)
def reset_user_password(user_id: int):
    user = db.session.get(User, user_id)
    if not user or not user.active:
        return jsonify(error="Usuario activo no encontrado."), 404
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    temporary_password = "".join(secrets.choice(alphabet) for _ in range(18))
    user.password_hash = generate_password_hash(temporary_password)
    user.must_change_password = True
    DeviceToken.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": datetime.now(timezone.utc)})
    db.session.commit()
    notify_security(user, "Un administrador restableció su contraseña – Quincena VE", "Un administrador de Quincena VE restableció la contraseña de su cuenta. Deberá cambiarla al entrar.")
    audit("password_reset_by_admin", f"Contraseña restablecida para {user.username}", g.user.id)
    return jsonify(ok=True, temporary_password=temporary_password)


@app.get("/api/audit-log")
@require_auth(admin=True)
def audit_log():
    events = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(100).all()
    users = {u.id: u.username for u in User.query.filter(User.id.in_([e.user_id for e in events if e.user_id is not None])).all()}
    return jsonify(events=[{"action": e.action, "detail": e.detail, "username": users.get(e.user_id, "Sistema"), "created_at": e.created_at.isoformat() if e.created_at else None} for e in events])

_RATE_CACHE = {}  # por país: {"rate", "updated", "fetched", "parallel", "parallel_updated"}


def _http_json(url: str):
    request_obj = urllib.request.Request(url, headers={"User-Agent": "Quincena/1.0"})
    with urllib.request.urlopen(request_obj, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_rate(country: str = DEFAULT_COUNTRY) -> dict:
    """Tasa oficial de la moneda local por 1 USD, con caché de 30 minutos y último valor conocido.

    Venezuela (oficial y paralelo) y Argentina (oficial) usan dolarapi; los demás países, tipos de cambio diarios de open.er-api.com."""
    country = clean_country(country)
    cache = _RATE_CACHE.setdefault(country, {"rate": None, "updated": None, "fetched": 0.0, "parallel": None, "parallel_updated": None})
    now = time.time()
    if cache["rate"] and now - cache["fetched"] < 1800:
        return cache
    cache["fetched"] = now - 1500 if cache["rate"] else 0.0  # ante un fallo, se reintenta en ~5 minutos
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        if country in ("VE", "AR"):
            rows = _http_json(f"https://{country.lower()}.dolarapi.com/v1/dolares")
            row = next((item for item in rows if item.get("fuente") == "oficial"), rows[0])
            value = float(row.get("promedio") if row.get("promedio") is not None else row.get("venta"))
            if math.isfinite(value) and 0 < value < 1_000_000:
                cache.update(rate=value, updated=row.get("fechaActualizacion") or stamp, fetched=now)
            market = next((item for item in rows if item.get("fuente") == "paralelo"), None) if country == "VE" else None
            market_value = _rate_or_none(market.get("promedio") if market and market.get("promedio") is not None else (market or {}).get("venta"))
            if market_value:
                cache.update(parallel=market_value, parallel_updated=market.get("fechaActualizacion") or stamp)
        else:
            data = _http_json("https://open.er-api.com/v6/latest/USD")
            value = float((data.get("rates") or {}).get(COUNTRIES[country]["cur"]))
            if math.isfinite(value) and 0 < value < 1_000_000:
                cache.update(rate=value, updated=stamp, fetched=now)
    except Exception:
        pass  # se conserva el último valor conocido
    return cache


def fetch_bcv_rate() -> dict:
    return fetch_rate("VE")


@app.get("/api/countries")
def list_countries():
    return jsonify(countries=COUNTRIES, default=DEFAULT_COUNTRY)


@app.get("/api/rate")
@require_auth()
def get_rate():
    country = clean_country(request.args.get("country"))
    cached = fetch_rate(country)
    if not cached["rate"]:
        return jsonify(error=f"No pudimos obtener el tipo de cambio ({COUNTRIES[country]['official']}) ahora. Inténtalo en unos minutos."), 503
    return jsonify(country=country, rate=cached["rate"], updated_at=cached["updated"], parallel=cached.get("parallel"), parallel_updated_at=cached.get("parallel_updated"))


@app.delete("/api/account")
@limiter.limit("5 per hour")
@require_auth()
def delete_account():
    body = request.get_json(silent=True) or {}
    user = g.user
    if not check_password_hash(user.password_hash, str(body.get("password", ""))):
        return jsonify(error="La contraseña no coincide."), 400
    if user.role == "admin" and User.query.filter_by(role="admin", active=True).count() <= 1:
        return jsonify(error="Eres la única persona administradora. Crea otra antes de eliminar esta cuenta."), 409
    uid = user.id
    PasswordReset.query.filter_by(user_id=uid).delete()
    EmailVerification.query.filter_by(user_id=uid).delete()
    DeviceToken.query.filter_by(user_id=uid).delete()
    UserPasskey.query.filter_by(user_id=uid).delete()
    PushSubscription.query.filter_by(user_id=uid).delete()
    ReminderDelivery.query.filter_by(user_id=uid).delete()
    SharedState.query.filter_by(user_id=uid).delete()
    SharedState.query.filter_by(updated_by=uid).update({"updated_by": None})
    AuditEvent.query.filter_by(user_id=uid).update({"user_id": None})
    db.session.delete(user)
    db.session.commit()
    session.clear()
    audit("account_deleted", f"Cuenta eliminada (id {uid})", None)
    return jsonify(ok=True)


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="La solicitud supera el tamaño máximo permitido."), 413


@app.errorhandler(429)
def too_many_requests(_error):
    return jsonify(error="Demasiados intentos. Espera unos minutos e inténtalo de nuevo."), 429


@app.errorhandler(404)
def not_found(error):
    return (jsonify(error="No encontrado."), 404) if request.path.startswith("/api/") else error


@app.errorhandler(405)
def method_not_allowed(error):
    return (jsonify(error="Método no permitido."), 405) if request.path.startswith("/api/") else error


@app.errorhandler(500)
def server_error(_error):
    app.logger.exception("Error interno")  # el detalle queda solo en el registro del servidor
    return jsonify(error="Algo salió mal en el servidor. Inténtalo de nuevo en unos minutos."), 500


with app.app_context():
    db.create_all()
    migrate_user_email()
    migrate_shared_state_schema()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)