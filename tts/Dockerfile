FROM python:3.10-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn>=0.34.0" "pydantic>=2.0" "pydantic-settings>=2.0" "loguru>=0.7.0"
COPY k8s_config_reload.py .
COPY main.py .
EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
