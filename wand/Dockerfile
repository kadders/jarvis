FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

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

RUN mkdir -p /app/models/wand_gesture

EXPOSE 8002
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8002"]
