FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Migrations run at every startup (idempotent), then the gateway starts.
# In production ./platform is bind-mounted over /app for hot growth — the
# COPY above is the fallback when running the image standalone.
CMD ["sh", "-c", "python migrations/run.py && python -m uvicorn api_gateway:app --host 0.0.0.0 --port 8000"]
