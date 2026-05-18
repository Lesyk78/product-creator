FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    U2NET_HOME=/app/models

WORKDIR /app

# Build deps for Pillow / onnxruntime native wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg-dev zlib1g-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-download u2net.onnx (~170MB) at build time so first request is fast.
# rembg looks it up in U2NET_HOME.
RUN mkdir -p /app/models && \
    curl -fsSL -o /app/models/u2net.onnx \
      https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx

COPY . .

EXPOSE 5050

# 1 worker, 4 threads. Heavy CPU jobs (rembg, Gemini) → keep concurrency
# bounded; raise threads if Coolify host has more cores.
# Long timeout because product_analyze can take 60-90s.
CMD ["gunicorn", "-w", "1", "--threads", "4", \
     "-b", "0.0.0.0:5050", "--timeout", "240", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--log-level", "info", "--capture-output", \
     "app:app"]
