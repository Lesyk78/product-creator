"""Centralized env-var loading. Fails fast if required secrets missing."""
import os
from dotenv import load_dotenv

load_dotenv()


# Read required env var; raise RuntimeError if unset or empty.
# In: env key. Out: trimmed value.
def _req(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        raise RuntimeError(f"Required env var {key} is not set")
    return v


# Read optional env var with default; never raises.
# In: env key, default. Out: trimmed value, or default if missing/empty.
def _opt(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip() or default


# --- Auth ---
AUTH_USER = _req("AUTH_USER")
AUTH_PASSWORD = _req("AUTH_PASSWORD")
ALLOWED_IPS = [x.strip() for x in _opt("ALLOWED_IPS", "").split(",") if x.strip()]
TRUST_PROXY = _opt("TRUST_PROXY", "1") == "1"

# --- Odoo ---
ODOO_URL = _req("ODOO_URL")
ODOO_DB = _req("ODOO_DB")
ODOO_LOGIN = _req("ODOO_LOGIN")
ODOO_PASSWORD = _req("ODOO_PASSWORD")

# --- Gemini ---
GEMINI_API_KEY = _req("GEMINI_API_KEY")
GEMINI_MODEL = _opt("GEMINI_MODEL", "gemini-2.5-pro")

# --- Bitrix24 alerts ---
BITRIX_WEBHOOK = _opt("BITRIX_WEBHOOK", "")
ALERT_RESPONSIBLE_ID = int(_opt("ALERT_RESPONSIBLE_ID", "310"))

LOG_LEVEL = _opt("LOG_LEVEL", "info").upper()
