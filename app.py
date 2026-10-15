from __future__ import annotations

import copy
import hmac
import math
import os
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

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
    "income": {"amount": 0, "currency": "USD"},  # compatibilidad con la versión anterior
    "incomeSources": [],  # sueldo, bono, cestatickets y otros ingresos programados
    "budgets": {"transporte": 0, "comida": 0, "fijo": 0, "hormiga": 0},
    "expenses": [],
    "debts": [],
    "schoolExpenses": [],  # compromisos/cargos del colegio
    "schoolPayments": [],  # pagos parciales o totales de esos compromisos
    "debtPlan": {"monthlyEssentials": 0.0, "monthlyCushion": 0.0, "paymentPercent": 60, "strategy": "avalanche", "configured": False},
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="member")  # admin | member
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class SharedState(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
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


def ensure_shared_state() -> SharedState:
    record = db.session.get(SharedState, 1)
    if record is None:
        record = SharedState(id=1, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA))
        db.session.add(record)
        try:
            db.session.commit()
        except Exception:
            # Multiple Gunicorn workers may race during the first startup.
            db.session.rollback()
            record = db.session.get(SharedState, 1)
            if record is None:
                raise
    return record


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


def current_user() -> User | None:
    user_id = session.get("uid")
    if not user_id:
        return None
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
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_ok():
                return jsonify(error="Token de seguridad vencido. Actualiza la página e inicia sesión nuevamente."), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapped
    return decorate


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
                if field not in {"id", "amount", "paid", "currency", "category", "description", "method", "bank", "date", "type", "entity", "minPayment", "cutDate", "payBefore", "installments", "invoice", "interestRate", "interestPeriod", "interestKnown"}:
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
                clean["category"] = row.get("category") if row.get("category") in {"comida", "transporte", "fijo", "hormiga"} else "fijo"
                clean["description"] = str(row.get("description") or "Gasto")[:250]
                clean["bank"] = str(row.get("bank") or "")[:120]
                clean["method"] = row.get("method") if row.get("method") in {"banco", "efectivo", "pago_movil"} else "banco"
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
    return result


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
    for key in ("currency", "exchangeRate", "income", "budgets"):
        if incoming[key] != base[key]:
            if current[key] != base[key] and current[key] != incoming[key]:
                conflicts.append(key)
            else:
                merged[key] = incoming[key]
    for key in ("expenses", "debts", "incomeSources", "schoolExpenses", "schoolPayments"):
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
        app_name="Control Financiero",
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
    shared = db.session.get(SharedState, 1)
    if shared is None:
        shared = SharedState(id=1, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA), updated_by=user.id)
        db.session.add(shared)
    else:
        shared.revision = 0
        shared.data = copy.deepcopy(DEFAULT_SHARED_DATA)
        shared.updated_by = user.id
        shared.updated_at = datetime.now(timezone.utc)
    db.session.commit()
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
    record = ensure_shared_state()
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
    record = db.session.query(SharedState).filter_by(id=1).with_for_update().first()
    if record is None:
        record = SharedState(id=1, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA))
        db.session.add(record)
        db.session.flush()
    current = normalize_shared_data(record.data)
    if record.revision == base_revision:
        merged, conflicts = incoming, []
    else:
        merged, conflicts = three_way_merge(base_data, incoming, current)
    if g.user.role != "admin" and not conflicts:
        # Enforce permissions against actual stored rows, never against client-supplied
        # base data (which can be tampered with).
        for collection in ("expenses", "debts"):
            current_rows = {str(item.get("id")): item for item in current.get(collection, []) if isinstance(item, dict) and item.get("id") is not None}
            merged_rows = {str(item.get("id")): item for item in merged.get(collection, []) if isinstance(item, dict) and item.get("id") is not None}
            if set(current_rows) - set(merged_rows):
                db.session.rollback()
                return jsonify(error="Solo el administrador puede eliminar gastos o deudas."), 403
            for item_id, stored in current_rows.items():
                proposed = merged_rows[item_id]
                if collection == "expenses" and proposed != stored:
                    db.session.rollback()
                    return jsonify(error="Los usuarios autorizados pueden agregar gastos, pero solo el administrador puede modificar o eliminar registros existentes."), 403
                if collection == "debts":
                    stored_terms = {key: value for key, value in stored.items() if key != "paid"}
                    proposed_terms = {key: value for key, value in proposed.items() if key != "paid"}
                    try:
                        new_paid = float(proposed.get("paid", 0) or 0)
                        old_paid = float(stored.get("paid", 0) or 0)
                    except (TypeError, ValueError):
                        new_paid, old_paid = -1, 0
                    if stored_terms != proposed_terms or new_paid < old_paid or new_paid > float(stored.get("amount", 0)):
                        db.session.rollback()
                        return jsonify(error="Solo se permite abonar a deudas existentes; sus términos solo los administra el administrador."), 403
                    payment_delta = round(new_paid - old_paid, 2)
                    if payment_delta > 0:
                        current_expense_ids = {str(item.get("id")) for item in current["expenses"] if item.get("id") is not None}
                        matching_payment = any(
                            str(item.get("id")) not in current_expense_ids and
                            str(item.get("description", "")).startswith(f"Abono/Pago Deuda: {stored.get('entity', '')}") and
                            round(float(item.get("amount", 0) or 0), 2) == payment_delta and
                            item.get("currency") == stored.get("currency")
                            for item in merged["expenses"]
                        )
                        if not matching_payment:
                            db.session.rollback()
                            return jsonify(error="Para registrar un abono, guarda al mismo tiempo el movimiento de pago correspondiente."), 403
        # Un usuario autorizado puede registrar gastos/pagos escolares nuevos, pero no
        # alterar o eliminar registros ya existentes. El administrador sí puede hacerlo.
        for collection in ("schoolExpenses", "schoolPayments"):
            current_rows = {str(item.get("id")): item for item in current.get(collection, []) if item.get("id") is not None}
            merged_rows = {str(item.get("id")): item for item in merged.get(collection, []) if item.get("id") is not None}
            if set(current_rows) - set(merged_rows):
                db.session.rollback()
                return jsonify(error="Solo el administrador puede eliminar registros escolares o sus pagos."), 403
            for item_id, stored in current_rows.items():
                if merged_rows.get(item_id) != stored:
                    db.session.rollback()
                    return jsonify(error="Los registros escolares y sus pagos no se pueden modificar después de guardarse; registra un nuevo movimiento o solicita al administrador que lo corrija."), 403
    if conflicts:
        db.session.rollback()
        latest = ensure_shared_state()
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
@require_auth(admin=True)
def import_legacy_state():
    body = request.get_json(silent=True) or {}
    try:
        incoming = normalize_shared_data(body.get("data"))
    except (ValueError, TypeError, OverflowError):
        return jsonify(error="El archivo de respaldo no contiene datos válidos."), 400
    record = db.session.query(SharedState).filter_by(id=1).with_for_update().first()
    if record is None:
        record = SharedState(id=1, revision=0, data=copy.deepcopy(DEFAULT_SHARED_DATA))
        db.session.add(record)
        db.session.flush()
    current = normalize_shared_data(record.data)
    school_error = validate_school_balances(incoming)
    if school_error:
        db.session.rollback()
        return jsonify(error=school_error), 400
    financially_empty = (
        not current["expenses"] and not current["debts"] and not current["incomeSources"] and
        not current["schoolExpenses"] and not current["schoolPayments"] and
        float(current["income"].get("amount", 0) or 0) == 0 and
        all(float(value or 0) == 0 for value in current["budgets"].values()) and
        not current["debtPlan"].get("configured", False)
    )
    incoming_has_data = bool(incoming["expenses"] or incoming["debts"] or incoming["incomeSources"] or incoming["schoolExpenses"] or incoming["schoolPayments"] or float(incoming["income"].get("amount", 0) or 0) or any(float(v or 0) for v in incoming["budgets"].values()) or incoming["debtPlan"].get("configured", False))
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


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="La solicitud supera el tamaño máximo permitido."), 413


with app.app_context():
    db.create_all()
    ensure_shared_state()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
