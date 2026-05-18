"""Odoo XML-RPC + JSON-RPC clients. Single auth, cached uid per-process.

XML-RPC is used by analyze/validate/create (matches existing Windmill code).
JSON-RPC is used by search (faster, batches well, matches product_search_api).
"""
import xmlrpc.client
import requests
from threading import Lock

import config

_uid_cache: int | None = None
_uid_lock = Lock()


# Authenticate to Odoo via XML-RPC common endpoint; cache uid for process lifetime.
# Out: uid int. Raises RuntimeError if credentials reject.
def _authenticate() -> int:
    global _uid_cache
    with _uid_lock:
        if _uid_cache is None:
            common = xmlrpc.client.ServerProxy(f"{config.ODOO_URL}/xmlrpc/2/common")
            uid = common.authenticate(config.ODOO_DB, config.ODOO_LOGIN, config.ODOO_PASSWORD, {})
            if not uid:
                raise RuntimeError("Odoo authentication failed")
            _uid_cache = uid
        return _uid_cache


# Build an authenticated XML-RPC object proxy for Odoo.
# Out: (uid, ServerProxy) tuple ready for execute_kw calls.
def models_proxy():
    uid = _authenticate()
    models = xmlrpc.client.ServerProxy(f"{config.ODOO_URL}/xmlrpc/2/object")
    return uid, models


# Shortcut for Odoo XML-RPC execute_kw with cached uid.
# In: model name, method name, args list, optional kwargs dict. Out: Odoo response.
def execute_kw(model: str, method: str, args: list, kwargs: dict | None = None):
    uid, models = models_proxy()
    return models.execute_kw(
        config.ODOO_DB, uid, config.ODOO_PASSWORD,
        model, method, args, kwargs or {},
    )


# Odoo JSON-RPC call (used by /api/search; mirrors original product_search_api bun script).
# In: model, method, args, optional kwargs. Out: parsed result. Raises on Odoo error envelope.
def jsonrpc_call(model: str, method: str, args: list, kwargs: dict | None = None):
    uid = _authenticate()
    resp = requests.post(
        f"{config.ODOO_URL}/jsonrpc",
        json={
            "jsonrpc": "2.0", "method": "call", "id": 2,
            "params": {
                "service": "object", "method": "execute_kw",
                "args": [config.ODOO_DB, uid, config.ODOO_PASSWORD, model, method, args, kwargs or {}],
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Odoo JSON-RPC error: {data['error']}")
    return data.get("result")
