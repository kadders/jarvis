FROM python:3.11-slim-bookworm

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

COPY main.py .

CMD ["python", "main.py"]
