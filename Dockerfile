FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    portaudio19-dev \
    python3-pyaudio \
    libasound2-dev \
    libportaudio2 \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy just the requirements.txt file first
COPY requirements.txt .

# Set pip to use the cache directory that we'll mount at build time
ENV PIP_CACHE_DIR=/pip-cache
ENV PIP_NO_CACHE_DIR=0

# Filter out problematic packages
RUN sed -i '/Free/d' requirements.txt

# Split dependencies to maximize cache usage
RUN grep -E "torch|transformers|numpy" requirements.txt > ml_requirements.txt || echo "No ML requirements found"
RUN grep -vE "torch|transformers|numpy" requirements.txt > other_requirements.txt || echo "No other requirements found"

# Install ML dependencies first with cache if available
RUN pip install pip==23.3.1 && \
    if [ -d "/pip-cache" ] && [ -s ml_requirements.txt ]; then \
        echo "Installing ML dependencies with cache" && \
        pip install --cache-dir=/pip-cache -r ml_requirements.txt; \
    elif [ -s ml_requirements.txt ]; then \
        echo "Installing ML dependencies without cache" && \
        pip install -r ml_requirements.txt; \
    fi

# Install other dependencies
RUN if [ -d "/pip-cache" ] && [ -s other_requirements.txt ]; then \
        echo "Installing other dependencies with cache" && \
        pip install --cache-dir=/pip-cache -r other_requirements.txt; \
    elif [ -s other_requirements.txt ]; then \
        echo "Installing other dependencies without cache" && \
        pip install -r other_requirements.txt; \
    fi

# Copy application code
COPY . .

# Create model directory if it doesn't exist
RUN mkdir -p models/llm_model

# Expose the API port
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"] 