FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Set pip to use the cache directory that we'll mount at build time
ENV PIP_CACHE_DIR=/pip-cache
ENV PIP_NO_CACHE_DIR=0

# Copy just the requirements file first
COPY requirements.txt .

# Create a separate requirements file for heavy ML dependencies
RUN grep -E "torch|torchaudio|transformers" requirements.txt > ml_requirements.txt || echo "No ML requirements found"
RUN grep -vE "torch|torchaudio|transformers" requirements.txt > other_requirements.txt || echo "No other requirements found"

# Install heavy ML dependencies first to maximize cache reuse
RUN pip3 install pip==23.3.1 && \
    if [ -d "/pip-cache" ] && [ -s ml_requirements.txt ]; then \
        echo "Installing ML dependencies with cache" && \
        pip3 install --cache-dir=/pip-cache -r ml_requirements.txt; \
    elif [ -s ml_requirements.txt ]; then \
        echo "Installing ML dependencies without cache" && \
        pip3 install -r ml_requirements.txt; \
    fi

# Install other Python dependencies
RUN if [ -d "/pip-cache" ] && [ -s other_requirements.txt ]; then \
        echo "Installing other dependencies with cache" && \
        pip3 install --cache-dir=/pip-cache -r other_requirements.txt; \
    elif [ -s other_requirements.txt ]; then \
        echo "Installing other dependencies without cache" && \
        pip3 install -r other_requirements.txt; \
    fi

# Copy application code
COPY . .

# Create directories for models and shared volumes if they don't exist
RUN mkdir -p models/asr_model

# Expose the API port
EXPOSE 8001

# Command to run the application
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"] 