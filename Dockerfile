FROM golang:1.24-alpine AS speedtest-builder
RUN apk add --no-cache git \
    && git clone --depth 1 --branch v1.0.13 https://github.com/librespeed/speedtest-cli.git /src
WORKDIR /src
RUN go build -trimpath -ldflags="-s -w" -o /out/librespeed-cli .

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATCH_DATA_DIR=/data \
    PATCH_BACKUP_DIR=/backups

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*
COPY --from=speedtest-builder /out/librespeed-cli /usr/local/bin/librespeed-cli
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY patch_manager ./patch_manager
COPY static ./static

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["uvicorn", "patch_manager.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
