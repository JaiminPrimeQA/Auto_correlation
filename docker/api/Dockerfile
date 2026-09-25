# Baseline11 API and execution worker (same image, different command).
#   API:    uvicorn app.main:app --host 0.0.0.0 --port 8000   (default)
#   Worker: python -m app.worker --once
# Build from the repo root: docker build -f docker/api/Dockerfile -t baseline11/api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --requirement requirements.txt
COPY backend/app ./app

USER app
EXPOSE 8000
# --proxy-headers: the ALB terminates TLS and forwards the client address.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
