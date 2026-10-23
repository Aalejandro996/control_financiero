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
import urllib.request
from email.message import EmailMessage
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
    SESSION_COOKIE_NAME="fincontrol_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true" if os.getenv("RENDER") else "false").lower() == "true",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
db = SQLAlchemy(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

DEFAULT_SHARED_DATA = {
    "currency": "USD",
    "exchangeRate": 40.0,
    "marketRate": 0.0,  # dólar del mercado (USDT/paralelo) en Bs.; 0 = desconocido
    "bsSavings": 0.0,  # bolívares que la persona dice tener guardados (para el medidor de poder de compra)
    "rateHistory": [],  # [{"d": "AAAA-MM-DD", "b": tasa BCV, "m": tasa USDT}] un registro por día, máx. 120
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
    """Añade user.email y user.email_verified_at si faltan. Debe correr antes de cualquier consulta a User."""
    def columns() -> set:
        return {c["name"] for c in sqlalchemy_inspect(db.engine).get_columns("user")}

    for name, ddl in (("email", "VARCHAR(254)"), ("email_verified_at", "TIMESTAMP WITH TIME ZONE")):
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
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr or "") or "")[:64],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("No se pudo guardar el evento de auditoría")


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


def current_user() -> User | None:
    user_id = session.get("uid")
    if not user_id:
        return user_from_bearer()
    user = db.session.get(User, user_id)
    if user is None or not user.active:
        session.clear()
        return None
    return user


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


def normalize_shared_data(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Los datos deben tener formato de objeto JSON.")
    result = copy.deepcopy(DEFAULT_SHARED_DATA)
    result["currency"] = "BS" if value.get("currency") in ("BS", "VES") else "USD"
    try:
        rate = float(value.get("exchangeRate", DEFAULT_SHARED_DATA["exchangeRate"]))
    except (TypeError, ValueError):
        rate = DEFAULT_SHARED_DATA["exchangeRate"]
    result["exchangeRate"] = rate if math.isfinite(rate) and 0 < rate < 1_000_000 else DEFAULT_SHARED_DATA["exchangeRate"]
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
                if field not in {"id", "amount", "paid", "currency", "category", "description", "method", "bank", "date", "type", "entity", "minPayment", "cutDate", "payBefore", "installments", "invoice", "interestRate", "interestPeriod", "interestKnown", "rateType", "rateUsed", "bcvRate", "marketRate"}:
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
        frequency = row.get("frequency") if row.get("frequency") in {"quincenal", "mensual", "ocasional"} else "quincenal"
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
        if frequency == "quincenal" and len(days) != 2:
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
    for key in ("currency", "exchangeRate", "income", "budgets", "debtPlan"):
        if incoming[key] != base[key]:
            if current[key] != base[key] and current[key] != incoming[key]:
                conflicts.append(key)
            else:
                merged[key] = incoming[key]
    for key in ("expenses", "debts", "incomeSources", "schoolExpenses", "schoolPayments", "plannedDebtPayments", "recurring"):
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
    # The existing page uses inline templates/events and trusted CDN assets for its visual styling.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; connect-src 'self' https://ve.dolarapi.com; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; font-src 'self' data: https://cdnjs.cloudflare.com https://fonts.gstatic.com")
    if os.getenv("RENDER") or request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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
    return info


@app.get("/api/android-app")
def android_app_info():
    if not APK_PATH.is_file():
        return jsonify(available=False)
    info = android_version()
    return jsonify(available=True, version=info["version"], versionCode=info["versionCode"], notes=info["notes"],
                   size_mb=round(APK_PATH.stat().st_size / 1048576, 1), url=f"/descargar/android?v={info['versionCode']}")


@app.get("/descargar/android")
def download_android():
    if not APK_PATH.is_file():
        abort(404)
    return send_from_directory(APK_PATH.parent, APK_PATH.name, mimetype="application/vnd.android.package-archive",
                               as_attachment=True, download_name="Quincena.apk", max_age=0)


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
        app_name="Quincena", registration_open=registration_open(),
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
    if len(password) < 12 or len(password) > 200:
        return jsonify(error="Usa una contraseña inicial de al menos 12 caracteres."), 400
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
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    audit("setup_completed", "Cuenta administradora inicial creada", user.id)
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": user.username, "role": user.role, "must_change_password": False})


@app.post("/api/login")
@limiter.limit("10 per minute")
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    user = User.query.filter_by(username=username, active=True).first()
    if not user or not check_password_hash(user.password_hash, password):
        audit("login_failed", f"Usuario: {username[:40]}", user_id=user.id if user else None)
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    session.clear()
    session["uid"] = user.id
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


def send_mail(to: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Envía correo por SMTP de Gmail usando una contraseña de aplicación."""
    smtp_user, smtp_password = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_password:
        return False
    message = EmailMessage()
    message["Subject"], message["To"] = subject, to
    message["From"] = os.getenv("MAIL_FROM") or f"Quincena <{smtp_user}>"
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
        return True
    except Exception:
        return False


def reset_email(username: str, link: str, validity: str = "60 minutos", intro: str = "Hemos recibido una solicitud para restablecer la contraseña de su cuenta en Quincena.", cta: str = "Restablecer contraseña", ignore: str = "Si usted no realizó esta solicitud, puede ignorar este mensaje; su contraseña actual no será modificada.") -> tuple[str, str]:
    """Correo formal de restablecimiento de contraseña: (texto plano, HTML)."""
    text = (
        f"Estimado/a {username}:\n\n"
        f"{intro}\n\n"
        f"Para continuar, abra el siguiente enlace. Tendrá una validez de {validity} y solo podrá utilizarse una vez:\n\n{link}\n\n"
        f"{ignore}\n\n"
        "Por su seguridad, Quincena nunca le solicitará su contraseña por correo electrónico ni por teléfono.\n\n"
        "Atentamente,\nEl equipo de Quincena"
    )
    name, url = html_escape(username), html_escape(link, quote=True)
    page = (
        '<!doctype html><html lang="es"><body style="margin:0;padding:24px;background:#F3F6F2;font-family:Arial,Helvetica,sans-serif;color:#12211B">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border:1px solid #D5DDD7;border-radius:12px;overflow:hidden">'
        '<tr><td style="background:#0B6B4D;padding:20px 28px;color:#ffffff;font-size:22px;font-weight:bold">Quincena</td></tr>'
        '<tr><td style="padding:28px;font-size:16px;line-height:1.6">'
        f'<p style="margin:0 0 16px">Estimado/a <b>{name}</b>:</p>'
        f'<p style="margin:0 0 16px">{html_escape(intro)}</p>'
        f'<p style="margin:0 0 24px">Para continuar, presione el siguiente botón. El enlace tiene una validez de <b>{validity}</b> y solo podrá utilizarse una vez.</p>'
        f'<p style="margin:0 0 24px;text-align:center"><a href="{url}" style="display:inline-block;background:#0B6B4D;color:#ffffff;text-decoration:none;font-weight:bold;padding:14px 28px;border-radius:10px">{cta}</a></p>'
        f'<p style="margin:0 0 16px;font-size:14px;color:#46564E">Si el botón no funciona, copie y pegue esta dirección en su navegador:<br><span style="word-break:break-all">{url}</span></p>'
        f'<p style="margin:0 0 16px">{html_escape(ignore)}</p>'
        '<p style="margin:0">Atentamente,<br>El equipo de Quincena</p></td></tr>'
        '<tr><td style="padding:16px 28px;background:#F3F6F2;font-size:13px;color:#46564E">Por su seguridad, Quincena nunca le solicitará su contraseña por correo electrónico ni por teléfono. Este es un mensaje automático; por favor, no responda a este correo.</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return text, page


def start_session(user: "User"):
    session.clear()
    session["uid"] = user.id
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
    if len(password) < 12 or len(password) > 200:
        return jsonify(error="Usa una contraseña de al menos 12 caracteres."), 400
    if User.query.filter_by(username=username).first():
        return jsonify(error="Ese usuario ya existe. Elige otro."), 409
    if User.query.filter(db.func.lower(User.email) == email).first():
        return jsonify(error="Ya existe una cuenta con ese correo."), 409
    user = User(username=username, email=email, password_hash=generate_password_hash(password), role="member", must_change_password=False)
    db.session.add(user)
    db.session.commit()
    ensure_user_state(user.id)
    audit("account_registered", "Cuenta creada; aceptó términos y privacidad (versión 2026-10-20)", user.id)
    send_verification(user)
    return start_session(user)


def send_verification(user: "User") -> bool:
    token = secrets.token_urlsafe(32)
    EmailVerification.query.filter_by(user_id=user.id, used_at=None).delete()
    db.session.add(EmailVerification(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    db.session.commit()
    origin = os.getenv("APP_PUBLIC_ORIGIN", "").rstrip("/") or request.host_url.rstrip("/")
    text_body, html_body = reset_email(user.username, f"{origin}/?verify={token}", "24 horas",
                                       "Gracias por crear su cuenta en Quincena. Para activar la recuperación de su contraseña, necesitamos confirmar su correo electrónico.",
                                       "Confirmar correo", "Si usted no creó esta cuenta, puede ignorar este mensaje.")
    return send_mail(user.email, "Confirme su correo electrónico – Quincena", text_body, html_body)


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
def mobile_login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lower()
    user = User.query.filter_by(username=username, active=True).first()
    if not user or not check_password_hash(user.password_hash, str(body.get("password", ""))):
        audit("login_failed", f"App móvil. Usuario: {username[:40]}", user_id=user.id if user else None)
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    if user.must_change_password:
        return jsonify(error="Cambia tu contraseña desde la versión web antes de usar la app."), 428
    token, now = secrets.token_urlsafe(48), datetime.now(timezone.utc)
    db.session.add(DeviceToken(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), label=str(body.get("device") or "Android")[:80],
                               last_used_at=now, expires_at=now + timedelta(days=90)))
    db.session.commit()
    audit("login_succeeded", "Inicio de sesión desde la app móvil", user.id)
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
def forgot_password():
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    user = User.query.filter(db.func.lower(User.email) == email, User.active.is_(True), User.email_verified_at.isnot(None)).first() if EMAIL_RE.match(email) else None
    if user:
        PasswordReset.query.filter_by(user_id=user.id, used_at=None).delete()
        token = secrets.token_urlsafe(32)
        db.session.add(PasswordReset(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + timedelta(minutes=60)))
        db.session.commit()
        origin = os.getenv("APP_PUBLIC_ORIGIN", "").rstrip("/") or request.host_url.rstrip("/")
        text_body, html_body = reset_email(user.username, f"{origin}/?reset={token}")
        sent = send_mail(email, "Restablecimiento de contraseña – Quincena", text_body, html_body)
        audit("password_reset_requested", "Enlace enviado" if sent else "No se pudo enviar el correo", user.id)
    return jsonify(ok=True)  # misma respuesta exista o no el correo


@app.post("/api/reset-password")
@limiter.limit("10 per hour")
def reset_password():
    body = request.get_json(silent=True) or {}
    new_password = str(body.get("new_password", ""))
    if len(new_password) < 12 or len(new_password) > 200:
        return jsonify(error="La contraseña nueva debe tener entre 12 y 200 caracteres."), 400
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
    if len(new_password) < 12 or len(new_password) > 200:
        return jsonify(error="La nueva contraseña debe tener entre 12 y 200 caracteres."), 400
    if new_password == old_password:
        return jsonify(error="La nueva contraseña debe ser diferente."), 400
    g.user.password_hash = generate_password_hash(new_password)
    g.user.must_change_password = False
    db.session.commit()
    session["csrf_token"] = secrets.token_urlsafe(32)
    audit("password_changed", "Contraseña actualizada", g.user.id)
    return jsonify(ok=True, csrf_token=session["csrf_token"], user={"username": g.user.username, "role": g.user.role, "must_change_password": False})


@app.get("/api/state")
@require_auth()
def get_state():
    record = ensure_user_state(g.user.id)
    return jsonify(data=normalize_shared_data(record.data), revision=record.revision, updated_at=record.updated_at.isoformat() if record.updated_at else None)


@app.put("/api/state")
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
    return jsonify(users=[{"id": u.id, "username": u.username, "role": u.role, "active": u.active, "must_change_password": u.must_change_password, "created_at": u.created_at.isoformat() if u.created_at else None} for u in users])


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
    db.session.commit()
    audit("password_reset_by_admin", f"Contraseña restablecida para {user.username}", g.user.id)
    return jsonify(ok=True, temporary_password=temporary_password)


@app.get("/api/audit-log")
@require_auth(admin=True)
def audit_log():
    events = AuditEvent.query.order_by(AuditEvent.created_at.desc()).limit(100).all()
    users = {u.id: u.username for u in User.query.filter(User.id.in_([e.user_id for e in events if e.user_id is not None])).all()}
    return jsonify(events=[{"action": e.action, "detail": e.detail, "username": users.get(e.user_id, "Sistema"), "created_at": e.created_at.isoformat() if e.created_at else None} for e in events])

_RATE_CACHE = {"rate": None, "updated": None, "fetched": 0.0, "parallel": None, "parallel_updated": None}


def fetch_bcv_rate() -> dict:
    """Dólar BCV consultado desde el servidor, con caché de 30 minutos y último valor conocido."""
    now = time.time()
    if _RATE_CACHE["rate"] and now - _RATE_CACHE["fetched"] < 1800:
        return _RATE_CACHE
    try:
        request_obj = urllib.request.Request("https://ve.dolarapi.com/v1/dolares", headers={"User-Agent": "Quincena/1.0"})
        with urllib.request.urlopen(request_obj, timeout=5) as response:
            rows = json.loads(response.read().decode("utf-8"))
        row = next((item for item in rows if item.get("fuente") == "oficial"), rows[0])
        value = float(row.get("promedio"))
        if math.isfinite(value) and 0 < value < 1_000_000:
            _RATE_CACHE.update(rate=value, updated=row.get("fechaActualizacion") or datetime.now(timezone.utc).isoformat(), fetched=now)
        market = next((item for item in rows if item.get("fuente") == "paralelo"), None)
        market_value = _rate_or_none(market.get("promedio")) if market else None
        if market_value:
            _RATE_CACHE.update(parallel=market_value, parallel_updated=market.get("fechaActualizacion") or datetime.now(timezone.utc).isoformat())
    except Exception:
        pass  # se conserva el último valor conocido
    return _RATE_CACHE


@app.get("/api/rate")
@require_auth()
def get_rate():
    cached = fetch_bcv_rate()
    if not cached["rate"]:
        return jsonify(error="No pudimos obtener el dólar BCV ahora. Inténtalo en unos minutos."), 503
    return jsonify(rate=cached["rate"], updated_at=cached["updated"], parallel=cached.get("parallel"), parallel_updated_at=cached.get("parallel_updated"))


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


with app.app_context():
    db.create_all()
    migrate_user_email()
    migrate_shared_state_schema()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)