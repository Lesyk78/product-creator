"""Gemini API client — analyze, chat, translate."""
import json
import re
import requests

import config

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


# Single Gemini generateContent call returning parsed JSON.
# In: content parts list + generation params. Out: parsed dict, or None on HTTP / parse failure.
def _post(parts: list, *, temperature: float, max_tokens: int, timeout: int) -> dict | None:
    url = f"{_ENDPOINT}/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    resp = requests.post(
        url,
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        print(f"[gemini] {resp.status_code}: {resp.text[:300]}")
        return None
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"\{[\s\S]*\}", text)
        return json.loads(m.group()) if m else None
    except Exception as e:
        print(f"[gemini] parse error: {e}")
        return None


# Convert [{b64,mimetype}] image list into Gemini inline_data parts.
# In: images list, optional limit. Out: list of {inline_data: {...}} dicts.
def _image_parts(images: list, limit: int | None = None) -> list:
    out = []
    for img in (images[:limit] if limit else images):
        mt = img.get("mimetype", "image/jpeg")
        if mt not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mt = "image/jpeg"
        out.append({"inline_data": {"mime_type": mt, "data": img["b64"]}})
    return out


# Analyze product photos with Gemini, return full structured product data.
# In: images list, user free-text description. Out: dict matching CONTENT_FIELDS schema, or None.
def analyze_product(images: list, user_description: str) -> dict | None:
    clean = re.sub(r"<[^>]+>", "", user_description or "").strip()
    prompt = f"""You are a product data specialist for Boni-Shop (German e-commerce).
Analyze these product images ({len(images)} photo(s)). User description: "{clean}"

IMPORTANT: If you can see a barcode (EAN/GTIN) on the product, read it and include in the response.
IMPORTANT: Weight must be realistic! A marker weighs ~15g, a valve ~300g, etc.
IMPORTANT: Generate ALL content uniquely. Do NOT copy text from any website (copyright risk!).

Return JSON:
{{"name":"German product name","sale_price":0,"purchase_price":0,"hs_code":"8 digits","weight":0.0,"length":0,"width":0,"height":0,"barcode":"EAN if visible or empty string","description_sale":"German formal sales description","description_purchase":"German technical purchase description","simple_description":"one-line German description","description_short":"<p>150 chars German HTML</p>","description_unique":"<p>2000 chars unique SEO German HTML</p>","description_long":"<p>Full 10000 chars German HTML with technical specs table</p>","description_amazon":"German plain text for Amazon","description_ebay":"<p>German HTML for eBay</p>","website_description":"<div style='font-family:Inter;max-width:800px'>English HTML: H2 title, intro block (#F6F7F9 bg, border-radius 20px), key specs, technical table (dark header #333, light rows #F6F7F9), features list, FAQ (#F0F6EA blocks), Why Buy From Us (fast shipping, expert advice, proven quality), CTA</div>","meta_description":"German 160 chars SEO meta"}}

Realistic German prices. Weight in KG (not grams!). German formal style. website_description in ENGLISH. ONLY valid JSON."""

    parts = _image_parts(images) + [{"text": prompt}]
    return _post(parts, temperature=0.3, max_tokens=8192, timeout=180)


# Chat agent for product editing — suggest field updates from natural-language message.
# In: user message, current product_data, optional images (max 3). Out: {response, updates}.
def chat(message: str, product_data: dict, images: list) -> dict:
    field_descriptions = {
        "name": "Product name (German)",
        "sale_price": "Sale price EUR",
        "purchase_price": "Purchase price EUR",
        "weight": "Weight in kg",
        "hs_code": "HS/customs code (8 digits)",
        "length": "Length in cm", "width": "Width in cm", "height": "Height in cm",
        "barcode": "EAN/GTIN barcode",
        "default_code": "SKU code (e.g. BONI-60001, WF-1001)",
        "description_sale": "Sales description (German)",
        "description_purchase": "Purchase description (German)",
        "simple_description": "One-line description (German)",
        "description_short": "Short HTML description 150 chars (German)",
        "description_unique": "Unique SEO HTML 2000 chars (German)",
        "description_long": "Long HTML description 10000 chars with specs table (German)",
        "description_amazon": "Amazon description plain text (German)",
        "description_ebay": "eBay HTML description (German)",
        "website_description": "Website full HTML page (English)",
        "meta_description": "SEO meta description 160 chars (German)",
    }
    current_data_str = json.dumps(product_data or {}, ensure_ascii=False, indent=2)
    fields_str = "\n".join(f"  - {k}: {v}" for k, v in field_descriptions.items())

    prompt = f"""You are a helpful AI assistant for Boni-Shop product creation (German e-commerce).
The user is reviewing/editing a product before creating it in Odoo.

Current product data:
{current_data_str}

Available fields that can be updated:
{fields_str}

SKU rules:
- BONI-xxxxx or IZS-xxxxx → Boni-Shop GmbH (company_id: 1)
- WF-xxxxx or NEW-xxxxx → HAJUS AG (company_id: 2)

User message: "{message}"

Instructions:
- Help the user with their request
- If they ask to change/fix/suggest values, include the updates
- If they ask to read something from the product photos, analyze the images
- If they ask for a different SKU, suggest one with reasoning
- Generate content in German (except website_description which is English)
- NEVER copy text from other websites (copyright risk!)
- Weight must be realistic (in KG, not grams)
- Be concise but helpful

Return ONLY valid JSON:
{{"response": "Your helpful response text in the user's language (Ukrainian/German/English based on their message)", "updates": {{"field_name": "new_value"}} }}

If no fields need updating, return empty updates: {{"response": "...", "updates": {{}} }}
ONLY valid JSON, nothing else."""

    parts = _image_parts(images or [], limit=3) + [{"text": prompt}]
    result = _post(parts, temperature=0.4, max_tokens=4096, timeout=120)
    if not result:
        return {"response": "Could not process request", "updates": {}}
    return {
        "response": result.get("response", ""),
        "updates": result.get("updates", {}),
    }


ALL_LANGS = [
    "bg_BG", "hr_HR", "cs_CZ", "da_DK", "nl_NL", "en_US", "et_EE", "fi_FI",
    "fr_FR", "de_DE", "el_GR", "hu_HU", "ga_IE", "it_IT", "lv_LV", "lt_LT",
    "nb_NO", "pl_PL", "pt_PT", "ro_RO", "ru_RU", "sk_SK", "sl_SI", "es_ES",
    "sv_SE", "uk_UA",
]
_LANG_NAMES = {
    "bg_BG": "Bulgarian", "hr_HR": "Croatian", "cs_CZ": "Czech", "da_DK": "Danish",
    "nl_NL": "Dutch", "en_US": "English", "et_EE": "Estonian", "fi_FI": "Finnish",
    "fr_FR": "French", "de_DE": "German", "el_GR": "Greek", "hu_HU": "Hungarian",
    "ga_IE": "Irish", "it_IT": "Italian", "lv_LV": "Latvian", "lt_LT": "Lithuanian",
    "nb_NO": "Norwegian", "pl_PL": "Polish", "pt_PT": "Portuguese", "ro_RO": "Romanian",
    "ru_RU": "Russian", "sk_SK": "Slovak", "sl_SI": "Slovenian", "es_ES": "Spanish",
    "sv_SE": "Swedish", "uk_UA": "Ukrainian",
}


# Translate German product name into 26 EU languages, prefixed with "[sku] ".
# In: sku, german_name. Out: {lang_code: "[sku] translated"} for all ALL_LANGS; fallback to german_name.
def translate_static_url(sku: str, german_name: str) -> dict:
    lang_list = ", ".join(f"{c}={_LANG_NAMES[c]}" for c in ALL_LANGS)
    prompt = f"""Translate this German product name into the following languages.
Product name: "{german_name}"

Languages: {lang_list}

Return a JSON object where keys are language codes and values are the translated product name.
Keep technical terms, model numbers, and measurements unchanged.
Example: {{"de_DE": "Kugelhahn 1\\" Messing", "fr_FR": "Vanne a bille 1\\" laiton"}}

ONLY valid JSON. All 26 languages must be present."""

    raw = _post([{"text": prompt}], temperature=0.2, max_tokens=4096, timeout=60) or {}
    return {
        lang: f"[{sku}] {raw.get(lang, german_name)}"
        for lang in ALL_LANGS
    }
