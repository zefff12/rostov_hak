"""Ролевая модель и аудит.

В прототипе — демо-пользователи и подписанный токен. В корпоративной среде
вход заменяется на SSO (Kerberos / ADFS / Keycloak), роли берутся из групп AD.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import time

from fastapi import Header, HTTPException

from . import config

ROLES = {
    "employee": "Работник",
    "dou": "Работник Департамента ДОУ",
    "admin": "Администратор базы знаний",
}

DEMO_USERS = {
    "ivanov": {"id": "ivanov", "name": "Иванов И. И.", "role": "employee", "unit": "Департамент N"},
    "petrova": {"id": "petrova", "name": "Петрова А. А.", "role": "dou", "unit": "Департамент ДОУ"},
    "admin": {"id": "admin", "name": "Администратор", "role": "admin", "unit": "Департамент ДОУ"},
}
TOKEN_TTL = 12 * 3600


# Роли, для входа в которые нужен пароль (остальные — вход в один клик для демонстрации).
PASSWORD_ROLES = {"admin"}


def check_password(user: dict, password: str) -> bool:
    if user["role"] not in PASSWORD_ROLES:
        return True
    return hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())


_hits: dict[str, list[float]] = {}


def rate_limited(key: str, per_min: int) -> bool:
    """Скользящее окно в 60 с на ключ (IP + действие)."""
    now = time.time()
    hits = [t for t in _hits.get(key, []) if now - t < 60]
    limited = len(hits) >= per_min
    if not limited:
        hits.append(now)
    _hits[key] = hits
    return limited


def _sign(payload: bytes) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def issue_token(user_id: str) -> str:
    payload = json.dumps({"u": user_id, "exp": int(time.time()) + TOKEN_TTL}).encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload)


def verify_token(token: str) -> dict | None:
    try:
        b64, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(b64.encode())
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        data = json.loads(payload)
        if data["exp"] < time.time():
            return None
        return DEMO_USERS.get(data["u"])
    except (ValueError, KeyError):
        return None


def current_user(authorization: str = Header(default="")) -> dict:
    user = verify_token(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(401, "Требуется вход")
    return user


def require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(403, "Недостаточно прав")


def audit(user: dict | None, action: str, **details) -> None:
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "user": user["id"] if user else None,
           "role": user["role"] if user else None, "action": action, **details}
    with open(config.AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
