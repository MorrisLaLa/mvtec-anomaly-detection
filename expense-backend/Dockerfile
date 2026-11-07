# 支出申報後端。以非 root 執行；執行時僅 /data 可寫（compose 以 read_only 加 volume 強制）。
FROM python:3.13.1-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data

WORKDIR /srv
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY app ./app
COPY config ./config
COPY frontend ./frontend

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
