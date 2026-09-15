# CPU-friendly image that exports preferences (and can dry-run train).
# Full QLoRA/DPO needs a CUDA base + training/requirements-train.txt.
FROM python:3.11-slim

WORKDIR /app
COPY training/ /app/training/
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --upgrade pip

ENTRYPOINT ["python", "-m", "training.export_preferences"]
CMD ["--feedback", "/data/feedback/feedback.jsonl", "--out", "/data/adapters/preferences.jsonl", "--min-pairs", "1"]
