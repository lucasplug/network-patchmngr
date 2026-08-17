FROM golang:1.24-alpine AS speedtest-builder
RUN apk add --no-cache git \
    && git clone --depth 1 --branch v1.0.13 https://github.com/librespeed/speedtest-cli.git /src
WORKDIR /src
RUN go build -trimpath -ldflags="-s -w" -o /out/librespeed-cli .

FROM alpine:3.21 AS oui-fetcher
# Lokale vendorherkenning: het IEEE-bestand wordt één keer bij de build gehaald,
# zodat de app nooit een externe lookup hoeft te doen. Faalt de download, dan
# blijft het bestand leeg en is vendorherkenning simpelweg uit.
RUN apk add --no-cache curl \
    && curl -fsSL --retry 3 --max-time 180 -A "network-patchmngr/oui-fetch" \
         -o /oui.csv https://standards-oui.ieee.org/oui/oui.csv || : > /oui.csv \
    # Een foutpagina of half bestand is erger dan geen bestand: alleen echte
    # OUI-data overhouden, anders leegmaken.
    && grep -qE '^[A-Z-]+,[0-9A-F]{6},' /oui.csv || : > /oui.csv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATCH_DATA_DIR=/data \
    PATCH_BACKUP_DIR=/backups \
    PATCH_OUI_FILE=/app/oui.csv

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping libcap2-bin \
    # De niet-root gebruiker kan alleen via deze file capability raw ICMP-sockets
    # openen; de cap moet ook in de container binnen cap_add NET_RAW vallen.
    && setcap cap_net_raw+ep "$(readlink -f "$(command -v ping)")" \
    && groupadd --gid 10001 patchmanager \
    && useradd --uid 10001 --gid patchmanager --no-create-home --shell /usr/sbin/nologin patchmanager \
    && mkdir -p /data /backups \
    && chown -R patchmanager:patchmanager /app /data /backups \
    && rm -rf /var/lib/apt/lists/*
COPY --from=speedtest-builder /out/librespeed-cli /usr/local/bin/librespeed-cli
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2 \
    && pip install --no-cache-dir -r requirements.txt

COPY --from=oui-fetcher /oui.csv ./oui.csv
COPY patch_manager ./patch_manager
COPY static ./static

USER 10001:10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["uvicorn", "patch_manager.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
