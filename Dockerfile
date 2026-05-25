FROM python:3.11-slim

LABEL maintainer="quant-gateway"
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data docker \
    && chmod +x docker/docker-entrypoint.sh \
    && useradd -m -s /bin/bash app \
    && chown -R app:app /app

USER app

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:5000/api/health || exit 1

ENTRYPOINT ["/app/docker/docker-entrypoint.sh"]
