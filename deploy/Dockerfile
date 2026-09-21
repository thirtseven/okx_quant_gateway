FROM python:3.12-slim

WORKDIR /app

# curl 用于 OKX 数据拉取 (data_feed 走 subprocess curl)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt prometheus_client

COPY . .

EXPOSE 8321

CMD ["python", "-m", "uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8321"]