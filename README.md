# Product Creator

Self-hosted replacement for the Windmill `product_*` scripts.
Flask backend + 3 static HTML pages (hub, creator, search), Basic-auth + IP-whitelist gated.

## Routes

| Path | Description |
|---|---|
| `GET  /`         | Dashboard (hub) |
| `GET  /create`   | Creator UI |
| `GET  /search`   | Search UI |
| `POST /api/analyze`  | Gemini analysis + rembg background removal + free-SKU search |
| `POST /api/validate` | SKU/barcode uniqueness check (Odoo) |
| `POST /api/create`   | Create product.template on Odoo PROD + 26 translations |
| `POST /api/chat`     | AI chat agent (Gemini, edits product fields) |
| `POST /api/search`   | Product search (Odoo JSON-RPC) |
| `GET  /healthz`  | Unauthenticated healthcheck for Coolify |

## Local dev

```bash
cp .env.example .env  # fill secrets
pip install -r requirements.txt
python app.py         # http://localhost:5050
```

## Docker build / run

```bash
docker build -t product-creator .
docker run --rm -p 5050:5050 --env-file .env product-creator
```

The Dockerfile pre-downloads `u2net.onnx` (~170MB) at build time so the first
`/api/analyze` request does not stall while the model loads.

## Coolify deploy

1. New application → Public repo → branch `main`.
2. Build pack: `dockerfile`.
3. Domain: configured by Olesh on `core-code.app`.
4. Env vars: copy from `.env.example`, fill `AUTH_PASSWORD`, `ODOO_PASSWORD`,
   `GEMINI_API_KEY`, `ALLOWED_IPS`.
5. Healthcheck path: `/healthz`.
6. Port: `5050`.
7. After deploy → verify Basic-auth prompt appears, then end-to-end smoke
   (`/api/validate` is the cheapest — ~1s).

## Security model

- **Basic auth** (`AUTH_USER` / `AUTH_PASSWORD`) on every request except `/healthz`.
- **IP whitelist** (`ALLOWED_IPS`, comma-separated CIDRs/IPs). Empty list
  → allow all (use only behind another auth layer).
- `TRUST_PROXY=1` (default) reads client IP from `X-Forwarded-For` — required
  when running behind Coolify's Traefik. Set to `0` for direct exposure.

## What was the Windmill version

See git history for the migration commit. Original scripts:
`f/Oles_Dzhyhryniuk/product_{hub,creation_app,search,search_api,analyze,validate,create,chat}`
on `windmill.boni.tools`.
