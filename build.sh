#!/bin/bash

# Jarvis AI Stack - Build automation script
# This script automates the build process for the Jarvis containers

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
CACHE_PACKAGES=true       # Enable pip caching by default
FORCE_BUILDKIT=false      # Don't force BuildKit by default
SKIP_TESTS=false          # Don't skip tests by default
DOWNLOAD_MODELS=true      # Download models if they don't exist
PREFETCH_DEPS=true        # Prefetch dependencies by default
BUILD_OPTIONAL=false      # Build optional jarvis_docs, jarvis_tts, jarvis_ui images
# Optional local container registry settings.
# Set REGISTRY (or pass --registry) to enable tagging; --push pushes images.
REGISTRY="${REGISTRY:-}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PUSH_IMAGES=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-cache)
      CACHE_PACKAGES=false
      shift
      ;;
    --use-buildkit)
      FORCE_BUILDKIT=true
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=true
      shift
      ;;
    --skip-models)
      DOWNLOAD_MODELS=false
      shift
      ;;
    --prefetch-deps)
      PREFETCH_DEPS=true
      shift
      ;;
    --no-prefetch-deps)
      PREFETCH_DEPS=false
      shift
      ;;
    --build-optional)
      BUILD_OPTIONAL=true
      shift
      ;;
    --registry)
      REGISTRY="$2"
      shift 2
      ;;
    --registry=*)
      REGISTRY="${1#*=}"
      shift
      ;;
    --tag)
      IMAGE_TAG="$2"
      shift 2
      ;;
    --tag=*)
      IMAGE_TAG="${1#*=}"
      shift
      ;;
    --push)
      PUSH_IMAGES=true
      shift
      ;;
    --no-push)
      PUSH_IMAGES=false
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [options]

Build options:
  --no-cache              Disable pip package caching
  --use-buildkit          Force use of Docker BuildKit (requires Docker >= 18.09)
  --skip-tests            Skip running tests before building
  --skip-models           Skip downloading models
  --prefetch-deps         Prefetch torch and other ML dependencies (default)
  --no-prefetch-deps      Skip prefetching dependencies
  --build-optional        Also build jarvis_docs, jarvis_tts, jarvis_ui, and jarvis_tap_collector images

Local registry options (for MicroK8s / kind / k3d / private registries):
  --registry HOST[:PORT]  Tag images with this registry prefix
                          (e.g. localhost:32000 for the MicroK8s registry addon)
  --tag TAG               Image tag for retagged images (default: latest)
  --push                  Push tagged images to the registry (implies --registry required)
  --no-push               Build and tag only; do not push

Environment variables: REGISTRY, IMAGE_TAG (same meaning as the flags above).

Examples:
  $0                                            # Local Compose build
  $0 --build-optional                           # Also build docs + tts images
  $0 --registry localhost:32000 --push          # MicroK8s built-in registry
  $0 --registry registry.example.com/team --tag v1.2.3 --push --build-optional
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Validation: --push needs --registry / REGISTRY
if [ "$PUSH_IMAGES" = true ] && [ -z "$REGISTRY" ]; then
    echo -e "${RED}Error: --push requires --registry (or REGISTRY env var).${NC}"
    exit 1
fi

echo -e "${GREEN}Starting Jarvis AI Stack build process...${NC}"

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

# Check if Docker Compose is installed (V1 standalone OR V2 plugin)
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    echo -e "${RED}Error: Docker Compose is not installed.${NC}"
    echo -e "${YELLOW}Install either the 'docker compose' plugin (V2) or the standalone 'docker-compose' (V1).${NC}"
    exit 1
fi

# Check if BuildKit is available if forced
if [ "$FORCE_BUILDKIT" = true ]; then
    # Check Docker version
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
    if [ "$(printf '%s\n' "18.09" "$DOCKER_VERSION" | sort -V | head -n1)" = "18.09" ]; then
        echo -e "${YELLOW}Enabling Docker BuildKit...${NC}"
        export DOCKER_BUILDKIT=1
    else
        echo -e "${RED}Warning: Docker BuildKit requires Docker >= 18.09, but you have $DOCKER_VERSION.${NC}"
        echo -e "${YELLOW}Proceeding without BuildKit.${NC}"
        FORCE_BUILDKIT=false
    fi
fi

# Create cache directories if they don't exist
if [ "$CACHE_PACKAGES" = true ]; then
    echo -e "${YELLOW}Setting up package cache directories...${NC}"
    # Create a more organized cache structure
    mkdir -p cache/build/pip
    mkdir -p cache/build/apt
    mkdir -p cache/models/llm_model
    mkdir -p cache/models/asr_model
    mkdir -p cache/docker
    
    # Ensure cache directories have correct permissions
    echo -e "${YELLOW}Setting correct permissions for cache directories...${NC}"
    chmod -R 777 cache  # Make cache writable by Docker containers
    
    # Report cache location
    PIP_CACHE_DIR=$(realpath cache/build/pip)
    echo -e "${GREEN}Pip cache location: ${PIP_CACHE_DIR}${NC}"
    echo -e "${YELLOW}This directory will be mounted to /pip-cache in containers${NC}"
    
    # Prefetch dependencies if requested
    if [ "$PREFETCH_DEPS" = true ]; then
        echo -e "${YELLOW}Prefetching PyTorch and other ML dependencies...${NC}"
        if [ -f "./prefetch_deps.sh" ]; then
            if [ ! -x "./prefetch_deps.sh" ]; then
                echo -e "${YELLOW}Making prefetch_deps.sh executable...${NC}"
                chmod +x ./prefetch_deps.sh
            fi
            echo -e "${GREEN}Running prefetch_deps.sh...${NC}"
            ./prefetch_deps.sh
            if [ $? -ne 0 ]; then
                echo -e "${RED}Error: prefetch_deps.sh failed${NC}"
                echo -e "${YELLOW}Continuing build anyway...${NC}"
            fi
        else
            echo -e "${RED}Error: prefetch_deps.sh not found${NC}"
            echo -e "${YELLOW}Continuing without prefetching dependencies.${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping dependency prefetching. Use --prefetch-deps to enable.${NC}"
        echo -e "${YELLOW}This might help with torch install issues.${NC}"
    fi
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating default .env file...${NC}"
    cat > .env << EOL
# API configuration
API_DEBUG=True
API_PORT=8000
API_HOST=0.0.0.0
API_PREFIX=/api/v1

# LLM configuration
MODEL_PATH=models/llm_model
MODEL_TYPE=ollama
MODEL_NAME=gpt-oss:20b
OLLAMA_BASE_URL=http://localhost:11434/v1
MAX_LENGTH=512
TEMPERATURE=0.7
IDEMPOTENCY_BACKEND=memory
REDIS_URL=

# ASR configuration
ASR_API_DEBUG=True
ASR_API_PORT=8001
ASR_API_HOST=0.0.0.0
ASR_MODEL_PATH=models/asr_model
ASR_MODEL_NAME=facebook/wav2vec2-base-960h
MONITOR_AUDIO_DIR=True
AUDIO_INPUT_DIR=shared/audio_input
AUDIO_OUTPUT_DIR=shared/audio_output
TRANSCRIPTIONS_DIR=shared/transcriptions

# Logging
LOG_LEVEL=INFO
EOL
    echo -e "${GREEN}.env file created successfully.${NC}"
fi

# Load environment variables from .env
if [ -f ".env" ]; then
    source .env
fi

# Create directories if they don't exist
for dir in api models utils tests config; do
    if [ ! -d "$dir" ]; then
        echo -e "${YELLOW}Creating $dir directory...${NC}"
        mkdir -p "$dir"
    fi
done

# Make sure models directories exist
LLM_MODEL_PATH=${MODEL_PATH:-"models/llm_model"}
ASR_MODEL_PATH=${ASR_MODEL_PATH:-"models/asr_model"}

mkdir -p "$LLM_MODEL_PATH"
mkdir -p "$ASR_MODEL_PATH"

# Create shared directories for audio files and transcriptions
echo -e "${YELLOW}Creating shared directories...${NC}"
mkdir -p shared/audio_input
mkdir -p shared/audio_output
mkdir -p shared/transcriptions

# Check if models exist and download them if needed
if [ "$DOWNLOAD_MODELS" = true ]; then
    # Check if LLM model is downloaded
    if [ ! -d "$LLM_MODEL_PATH/model.safetensors" ] && [ ! -f "$LLM_MODEL_PATH/pytorch_model.bin" ]; then
        echo -e "${YELLOW}LLM model not found in $LLM_MODEL_PATH${NC}"
        echo -e "${YELLOW}Downloading models before building...${NC}"
        
        # Check if download_models.sh is executable
        if [ ! -x "download_models.sh" ]; then
            chmod +x download_models.sh
        fi
        
        ./download_models.sh
    else
        echo -e "${GREEN}LLM model already exists in $LLM_MODEL_PATH${NC}"
    fi
fi

# Run tests before building
if [ "$SKIP_TESTS" = false ]; then
    echo -e "${YELLOW}Running tests...${NC}"
    if command -v pytest &> /dev/null; then
        if [ -d "tests" ] && [ "$(ls -A tests)" ]; then
            python3 -m pytest -xvs tests/ || {
                echo -e "${RED}Tests failed. Please fix the errors before building.${NC}"
                exit 1
            }
        else
            echo -e "${YELLOW}No tests found. Skipping test phase...${NC}"
        fi
    else
        echo -e "${YELLOW}Pytest not found in PATH. Skipping tests...${NC}"
    fi
else
    echo -e "${YELLOW}Skipping tests as requested...${NC}"
fi

# Pick a Compose CLI: prefer `docker compose` plugin, fall back to docker-compose.
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo -e "${RED}Error: neither 'docker compose' nor 'docker-compose' is available.${NC}"
    exit 1
fi

# Build the Docker images with appropriate caching
echo -e "${YELLOW}Building Docker images via ${COMPOSE}...${NC}"
if [ "$CACHE_PACKAGES" = true ]; then
    echo -e "${YELLOW}Using pip package caching for faster builds...${NC}"
    echo -e "${YELLOW}PyTorch and other large packages will be cached at: ${PIP_CACHE_DIR}${NC}"
    $COMPOSE build
else
    echo -e "${YELLOW}Building without package caching...${NC}"
    $COMPOSE build --no-cache
fi

# Optional: build jarvis_docs, jarvis_tts, jarvis_ui (docs/tts not in compose build set alone).
if [ "$BUILD_OPTIONAL" = true ]; then
    if [ -f "Dockerfile.docs" ]; then
        echo -e "${YELLOW}Building jarvis_docs:${IMAGE_TAG}...${NC}"
        docker build -f Dockerfile.docs -t "jarvis_docs:${IMAGE_TAG}" .
    else
        echo -e "${YELLOW}Skipping jarvis_docs (Dockerfile.docs missing)${NC}"
    fi
    if [ -f "tts/Dockerfile" ]; then
        echo -e "${YELLOW}Building jarvis_tts:${IMAGE_TAG}...${NC}"
        docker build -f tts/Dockerfile -t "jarvis_tts:${IMAGE_TAG}" ./tts
    else
        echo -e "${YELLOW}Skipping jarvis_tts (tts/Dockerfile missing)${NC}"
    fi
    if [ -f "ui/Dockerfile" ]; then
        echo -e "${YELLOW}Building jarvis_ui:${IMAGE_TAG}...${NC}"
        docker build -f ui/Dockerfile -t "jarvis_ui:${IMAGE_TAG}" ./ui
    else
        echo -e "${YELLOW}Skipping jarvis_ui (ui/Dockerfile missing)${NC}"
    fi
    if [ -f "tap_collector/Dockerfile" ]; then
        echo -e "${YELLOW}Building jarvis_tap_collector:${IMAGE_TAG}...${NC}"
        docker build -f tap_collector/Dockerfile -t "jarvis_tap_collector:${IMAGE_TAG}" ./tap_collector
    else
        echo -e "${YELLOW}Skipping jarvis_tap_collector (tap_collector/Dockerfile missing)${NC}"
    fi
fi

# Optional: tag (and push) images to a local/private container registry.
if [ -n "$REGISTRY" ]; then
    REGISTRY="${REGISTRY%/}"   # strip trailing slash
    echo -e "${YELLOW}Tagging images for registry: ${REGISTRY}${NC}"

    # Images expected from the Compose build (+ optional extras):
    REGISTRY_IMAGES=(
      "jarvis_llm_api"
      "jarvis_asr_service"
      "jarvis_ui"
    )
    if [ "$BUILD_OPTIONAL" = true ]; then
        REGISTRY_IMAGES+=("jarvis_docs" "jarvis_tts" "jarvis_tap_collector")
    fi

    for img in "${REGISTRY_IMAGES[@]}"; do
        local_ref="${img}:latest"
        remote_ref="${REGISTRY}/${img}:${IMAGE_TAG}"
        if ! docker image inspect "$local_ref" >/dev/null 2>&1; then
            echo -e "${YELLOW}Skipping ${img}: local image ${local_ref} not found${NC}"
            continue
        fi
        echo -e "${GREEN}docker tag ${local_ref} ${remote_ref}${NC}"
        docker tag "$local_ref" "$remote_ref"

        if [ "$PUSH_IMAGES" = true ]; then
            echo -e "${GREEN}docker push ${remote_ref}${NC}"
            docker push "$remote_ref"
        fi
    done

    if [ "$PUSH_IMAGES" = false ]; then
        echo -e "${YELLOW}Images tagged but not pushed. Re-run with --push to publish.${NC}"
    fi
fi

echo -e "${YELLOW}To start the containers, run:${NC}"
echo -e "  ${COMPOSE} up"
echo -e "${YELLOW}To start in background mode, run:${NC}"
echo -e "  ${COMPOSE} up -d"

# Don't prompt when run non-interactively (e.g. from CI or jarvis.sh build).
if [ -t 0 ] && [ -t 1 ]; then
    read -p "Do you want to start the containers now? (y/n): " start_container
    if [[ $start_container =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}Starting containers...${NC}"
        $COMPOSE up
    else
        echo -e "${GREEN}Build completed. Containers not started.${NC}"
    fi
fi
