FROM python:3.10-slim

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

RUN mkdir -p /app/cache/oximeter

EXPOSE 8005
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8005"]
