"""Product Creator — Flask app, auth-gated, mirrors Windmill product_* scripts.

Routes:
  GET  /                → static/hub.html
  GET  /create          → static/index.html
  GET  /search          → static/search.html
  POST /api/analyze     → Gemini + rembg + free-SKU search
  POST /api/validate    → SKU/barcode uniqueness check
  POST /api/create      → Create product.template on Odoo PROD + translations
  POST /api/chat        → AI chat agent
  POST /api/search      → Product search via Odoo JSON-RPC
  GET  /healthz         → unauthenticated healthcheck for Coolify
"""
import base64
import ipaddress
import secrets
import traceback
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory, Response

import config
import alerts
import bg_remove
import gemini_client
import odoo_client

app = Flask(__name__, static_folder=None)

# --- SKU configuration (mirrors product_analyze + product_create) ---
SKU_COMPANIES = {
    "BONI": {"company_id": 1, "company_name": "Boni-Shop GmbH", "range_start": 60001, "range_end": 61000},
    "IZS":  {"company_id": 1, "company_name": "Boni-Shop GmbH", "range_start": 1001,  "range_end": 2000},
    "WF":   {"company_id": 2, "company_name": "HAJUS AG",       "range_start": 1001,  "range_end": 2000},
    "NEW":  {"company_id": 2, "company_name": "HAJUS AG",       "range_start": 1001,  "range_end": 2000},
}
SKU_COMPANY_BY_PREFIX = {k: v["company_id"] for k, v in SKU_COMPANIES.items()}

# Weight (kg) → delivery tag mapping. Anything above max → oversize tag.
DELIVERY_TAGS = [(1.0, 1), (10.0, 2), (20.0, 3), (30.0, 4), (40.0, 5)]
DELIVERY_TAG_OVERSIZE = 6


# ===== Auth middleware =====

# Resolve real client IP. Reads X-Forwarded-For[0] when TRUST_PROXY=1 (behind Coolify Traefik),
# else falls back to request.remote_addr. Out: ip string or "".
def _client_ip() -> str:
    """Resolve real client IP. Returns X-Forwarded-For[0] when TRUST_PROXY=1
    (behind Coolify Traefik), otherwise request.remote_addr. Returns "" if unknown."""
    if config.TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or ""


# Check ip against config.ALLOWED_IPS (single IPs and CIDRs).
# Out: True if list empty (allow-all) or IP matches; False on bad input.
def _ip_allowed(ip: str) -> bool:
    """Check ip against config.ALLOWED_IPS (single IPs and CIDRs supported).
    In: ip string. Out: True if list empty (allow-all) or IP matches; False on parse error."""
    if not config.ALLOWED_IPS:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for rule in config.ALLOWED_IPS:
        try:
            if "/" in rule:
                if addr in ipaddress.ip_network(rule, strict=False):
                    return True
            elif addr == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False


# Constant-time compare of request's Basic-auth header with config user/password.
# Out: True only when both username and password match exactly.
def _basic_auth_ok() -> bool:
    """Constant-time compare of request Basic-auth header with config user/password.
    Out: True only when both username and password match exactly."""
    a = request.authorization
    if not a or a.type != "basic":
        return False
    return (
        secrets.compare_digest(a.username or "", config.AUTH_USER)
        and secrets.compare_digest(a.password or "", config.AUTH_PASSWORD)
    )


@app.before_request
# Auth gate: every request except /healthz must pass IP whitelist AND Basic auth.
# Returns 403/401 Response on failure; None to let the route handler run.
def gate():
    """Auth gate: every request except /healthz must pass IP whitelist AND Basic auth.
    Returns 403/401 Response on failure, None to let the route handler run."""
    if request.path == "/healthz":
        return None

    ip = _client_ip()
    if not _ip_allowed(ip):
        return Response("Forbidden (IP not allowed)", status=403)

    if not _basic_auth_ok():
        return Response(
            "Authentication required",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Product Creator"'},
        )
    return None


# ===== Static pages =====

@app.route("/")
# GET / → dashboard HTML (was Windmill product_hub).
def hub_page():
    """GET / → dashboard HTML (was Windmill product_hub)."""
    return send_from_directory("static", "hub.html")


@app.route("/create")
# GET /create → creator UI HTML (was Windmill product_creation_app).
def create_page():
    """GET /create → creator UI HTML (was Windmill product_creation_app)."""
    return send_from_directory("static", "index.html")


@app.route("/search")
# GET /search → search UI HTML (was Windmill product_search).
def search_page():
    """GET /search → search UI HTML (was Windmill product_search)."""
    return send_from_directory("static", "search.html")


@app.route("/healthz")
# Unauthenticated liveness probe used by Coolify. Out: {"ok": true}.
def healthz():
    """Unauthenticated liveness probe used by Coolify. Returns {ok: true}."""
    return {"ok": True}


# ===== API: analyze =====

# Linear scan of "<prefix>-N" in [range_start, range_end) on product.template.default_code.
# Out: first SKU with search_count == 0, or "<prefix>-99999" if range exhausted.
def _find_free_sku(prefix: str, range_start: int, range_end: int) -> str:
    """Linear scan of prefix-N in [range_start, range_end) on product.template.default_code.
    Returns the first SKU with search_count == 0, or "<prefix>-99999" if range exhausted."""
    for i in range(range_start, range_end):
        sku = f"{prefix}-{i}"
        if odoo_client.execute_kw(
            "product.template", "search_count", [[["default_code", "=", sku]]]
        ) == 0:
            return sku
    return f"{prefix}-99999"


@app.route("/api/analyze", methods=["POST"])
# POST /api/analyze: bg-remove + Gemini analyze + free SKU lookup.
# Body: {images:[{b64,mimetype}], description}. Out: {product_data, white_bg_b64, sku_options} or {error}.
def api_analyze():
    body = request.get_json(force=True, silent=True) or {}
    images = body.get("images") or []
    description = body.get("description") or ""
    if not images:
        return jsonify({"error": "No images provided"}), 400
    try:
        white_bg_b64 = bg_remove.remove_background_to_white(images[0]["b64"])
        product_data = gemini_client.analyze_product(images, description)
        if not product_data:
            return jsonify({"error": "Gemini analysis failed"}), 502

        if (product_data.get("weight") or 0) > 50:
            product_data["weight"] = 0

        sku_options = {}
        for prefix, cfg in SKU_COMPANIES.items():
            sku_options[prefix] = {
                "sku": _find_free_sku(prefix, cfg["range_start"], cfg["range_end"]),
                "company_id": cfg["company_id"],
                "company_name": cfg["company_name"],
            }

        return jsonify({
            "product_data": product_data,
            "white_bg_b64": white_bg_b64,
            "sku_options": sku_options,
        })
    except Exception as e:
        alerts.send_alert("Product Analyze", "/api/analyze", str(e), traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ===== API: validate =====

@app.route("/api/validate", methods=["POST"])
# POST /api/validate: check SKU / barcode uniqueness on product.template.
# Body: {sku, barcode}. Out: {sku_exists, barcode_exists, sku_product, barcode_product} or {error}.
def api_validate():
    body = request.get_json(force=True, silent=True) or {}
    sku = (body.get("sku") or "").strip()
    barcode = (body.get("barcode") or "").strip()

    result = {"sku_exists": False, "barcode_exists": False, "sku_product": None, "barcode_product": None}
    try:
        if sku:
            found = odoo_client.execute_kw(
                "product.template", "search_read",
                [[["default_code", "=", sku]]],
                {"fields": ["id", "name", "default_code"], "limit": 1},
            )
            if found:
                result["sku_exists"] = True
                result["sku_product"] = f"{found[0]['default_code']} - {found[0]['name']}"

        if barcode:
            found = odoo_client.execute_kw(
                "product.template", "search_read",
                [[["barcode", "=", barcode]]],
                {"fields": ["id", "name", "barcode"], "limit": 1},
            )
            if found:
                result["barcode_exists"] = True
                result["barcode_product"] = f"{found[0]['barcode']} - {found[0]['name']}"

        return jsonify(result)
    except Exception as e:
        alerts.send_alert("Product Validate", "/api/validate", str(e), traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ===== API: create =====

# Map weight in kg to product_tag_id using DELIVERY_TAGS thresholds.
# In: weight (kg). Out: tag_id; DELIVERY_TAG_OVERSIZE if weight ≥ largest threshold.
def _delivery_tag_for_weight(weight: float) -> int:
    for max_weight, tag_id in DELIVERY_TAGS:
        if weight < max_weight:
            return tag_id
    return DELIVERY_TAG_OVERSIZE


@app.route("/api/create", methods=["POST"])
# POST /api/create: create product.template on Odoo PROD + 26 translations.
# Body: full product dict (name, default_code, prices, dims, descriptions, extra_images).
# Out: {product_id, product_url, sku, name} or {error}.
def api_create():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    default_code = (body.get("default_code") or "").strip()
    if not name or not default_code:
        return jsonify({"error": "name and default_code are required"}), 400

    try:
        weight = float(body.get("weight") or 0)
        prefix = default_code.split("-")[0].upper() if "-" in default_code else "BONI"
        company_id = SKU_COMPANY_BY_PREFIX.get(prefix, 1)

        product_vals = {
            "name": name,
            "default_code": default_code,
            "type": "consu",
            "is_storable": True,
            "tracking": body.get("tracking") or "none",
            "list_price": float(body.get("list_price") or 0),
            "standard_price": float(body.get("standard_price") or 0),
            "categ_id": 1,
            "hs_code": body.get("hs_code") or "",
            "weight": weight,
            "x_studio_x_company_id": company_id,
            "image_1920": body.get("image_b64") or "",
            "feed_length": float(body.get("length") or 0),
            "feed_width":  float(body.get("width")  or 0),
            "feed_height": float(body.get("height") or 0),
            "description_sale":              body.get("description_sale") or "",
            "description_purchase":          body.get("description_purchase") or "",
            "x_studio_simple_description":   body.get("simple_description") or "",
            "x_studio_description_short":    body.get("description_short") or "",
            "x_studio_description_unique":   body.get("description_unique") or "",
            "x_studio_description_long":     body.get("description_long") or "",
            "x_studio_description_for_amazon": body.get("description_amazon") or "",
            "x_studio_description_for_ebay":   body.get("description_ebay") or "",
            "website_description":           body.get("website_description") or "",
            "website_meta_description":      body.get("meta_description") or "",
            "product_tag_ids": [(4, _delivery_tag_for_weight(weight))],
            "x_studio_static_url": f"[{default_code}] {name}",
        }

        barcode = (body.get("barcode") or "").strip()
        if barcode and len(barcode) >= 8:
            product_vals["barcode"] = barcode

        product_id = odoo_client.execute_kw(
            "product.template", "create", [product_vals]
        )

        extra_images = body.get("extra_images") or []
        for i, img_b64 in enumerate(extra_images):
            try:
                odoo_client.execute_kw(
                    "product.image", "create", [{
                        "product_tmpl_id": product_id,
                        "name": f"{default_code} - {i + 2}",
                        "image_1920": img_b64,
                    }],
                )
            except Exception as e:
                print(f"[create] extra image {i+2} error: {e}")

        translations = gemini_client.translate_static_url(default_code, name)
        for lang, translated_text in translations.items():
            try:
                odoo_client.execute_kw(
                    "product.template", "write",
                    [[product_id], {"x_studio_static_url": translated_text}],
                    {"context": {"lang": lang}},
                )
            except Exception as e:
                print(f"[create] translation {lang} error: {e}")

        return jsonify({
            "product_id": product_id,
            "product_url": f"{config.ODOO_URL}/odoo/product-template/{product_id}",
            "sku": default_code,
            "name": name,
        })
    except Exception as e:
        alerts.send_alert(
            "Product Create",
            "/api/create",
            f"SKU: {default_code} — {e}",
            traceback.format_exc(),
        )
        return jsonify({"error": str(e)}), 500


# ===== API: chat =====

@app.route("/api/chat", methods=["POST"])
# POST /api/chat: Gemini chat agent for product editing.
# Body: {message, product_data, images}. Out: {response, updates}.
def api_chat():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(gemini_client.chat(
            message=body.get("message") or "",
            product_data=body.get("product_data") or {},
            images=body.get("images") or [],
        ))
    except Exception as e:
        alerts.send_alert("Product Chat", "/api/chat", str(e), traceback.format_exc())
        return jsonify({"response": f"Error: {e}", "updates": {}}), 500


# ===== API: search =====

SEARCH_FIELDS = [
    "id", "name", "default_code", "list_price", "standard_price",
    "barcode", "image_128", "qty_available", "type", "categ_id",
    "x_studio_x_company_id", "weight", "is_storable", "tracking",
    "product_tag_ids", "hs_code", "active",
]


@app.route("/api/search", methods=["POST"])
# POST /api/search: product.template search via Odoo JSON-RPC.
# Body: {query, company, limit, offset}. Out: {records, total, offset, limit} or {error}.
def api_search():
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    company = int(body.get("company") or 0)
    limit = int(body.get("limit") or 20)
    offset = int(body.get("offset") or 0)

    domain: list = []
    if query:
        domain.extend([
            "|", "|", "|",
            ["default_code", "ilike", query],
            ["name", "ilike", query],
            ["barcode", "ilike", query],
            ["x_studio_static_url", "ilike", query],
        ])
    if company > 0:
        domain.append(["x_studio_x_company_id", "=", company])

    try:
        records = odoo_client.jsonrpc_call(
            "product.template", "search_read", [domain],
            {"fields": SEARCH_FIELDS, "limit": limit, "offset": offset, "order": "default_code asc"},
        ) or []
        total = odoo_client.jsonrpc_call(
            "product.template", "search_count", [domain],
        ) or 0
        return jsonify({"records": records, "total": total, "offset": offset, "limit": limit})
    except Exception as e:
        alerts.send_alert("Product Search", "/api/search", str(e), traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
