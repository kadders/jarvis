FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PIP_CACHE_DIR=/pip-cache
ENV PIP_NO_CACHE_DIR=0

COPY requirements.txt .
RUN pip3 install pip==23.3.1 && \
    if [ -d "/pip-cache" ]; then \
      pip3 install --cache-dir=/pip-cache -r requirements.txt; \
    else \
      pip3 install -r requirements.txt; \
    fi

COPY . .

# Match gpu-rig-telemetry runAsUser 1000 (NFS recordings write).
RUN mkdir -p /tmp/tap-stream /tmp/tap-stream-work \
    && chown -R 1000:1000 /tmp/tap-stream /tmp/tap-stream-work /app

USER 1000:1000

EXPOSE 8005
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
