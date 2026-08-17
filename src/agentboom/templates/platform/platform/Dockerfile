FROM python:3.12-slim

ARG USER_ID=1000
ARG USER_NAME=user

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Non-root user ───────────────────────────────────────────────────────
# Mini-apps are arbitrary Python hot-loaded from a bind mount; running
# them as root inside the container wastes the loopback-only publishing.
# The agent container already drops privileges — the platform matches.
# /data is pre-created and chowned so the named volume inherits the right
# ownership on first use (SQLite lives there).
RUN useradd -m -u ${USER_ID} -s /bin/bash ${USER_NAME} \
    && mkdir -p /data \
    && chown -R ${USER_ID}:${USER_ID} /app /data

USER ${USER_NAME}

EXPOSE 8000

# Migrations run at every startup (idempotent), then the gateway starts.
# In production ./platform is bind-mounted over /app for hot growth — the
# COPY above is the fallback when running the image standalone.
CMD ["sh", "-c", "python migrations/run.py && python -m uvicorn api_gateway:app --host 0.0.0.0 --port 8000"]
