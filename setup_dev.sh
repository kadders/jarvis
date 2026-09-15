#!/bin/bash

# Jarvis AI Stack - Development environment setup script
# This script sets up the development environment for the Jarvis AI Stack project

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default component to set up
COMPONENT="all"  # all, llm, or asr

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --llm)
      COMPONENT="llm"
      shift
      ;;
    --asr)
      COMPONENT="asr"
      shift
      ;;
    --all)
      COMPONENT="all"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--llm|--asr|--all]"
      echo "  --llm   Set up only LLM API development environment"
      echo "  --asr   Set up only ASR service development environment"
      echo "  --all   Set up complete development environment (default)"
      echo "  -h, --help  Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

echo -e "${GREEN}Setting up development environment for Jarvis AI Stack...${NC}"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed. Please install Python 3 first.${NC}"
    exit 1
fi

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${YELLOW}Detected Python version: ${python_version}${NC}"

# Check if version is at least 3.8
if [[ $(echo "${python_version}" | cut -d. -f1) -lt 3 || ($(echo "${python_version}" | cut -d. -f1) -eq 3 && $(echo "${python_version}" | cut -d. -f2) -lt 8) ]]; then
    echo -e "${RED}Error: Python version must be at least 3.8. Found: ${python_version}${NC}"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
else
    echo -e "${YELLOW}Virtual environment already exists.${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate

# Install requirements based on component
if [ "$COMPONENT" = "llm" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}Installing LLM API requirements...${NC}"
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
    else
        echo -e "${RED}Error: requirements.txt not found.${NC}"
        if [ "$COMPONENT" = "llm" ]; then
            exit 1
        fi
    fi
fi

if [ "$COMPONENT" = "asr" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}Installing ASR service requirements...${NC}"
    if [ -f "asr/requirements.txt" ]; then
        pip install -r asr/requirements.txt
    else
        echo -e "${RED}Error: asr/requirements.txt not found.${NC}"
        if [ "$COMPONENT" = "asr" ]; then
            exit 1
        fi
    fi
fi

# Create development .env file if it doesn't exist
if [ ! -f ".env.dev" ]; then
    echo -e "${YELLOW}Creating development .env file...${NC}"
    cat > .env.dev << EOL
# API configuration
API_DEBUG=True
API_PORT=8000
API_HOST=0.0.0.0
API_PREFIX=/api/v1

# LLM configuration
MODEL_PATH=models/llm_model
MODEL_TYPE=huggingface
MODEL_NAME=google/flan-t5-base
MAX_LENGTH=512
TEMPERATURE=0.7

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
LOG_LEVEL=DEBUG
EOL
    
    # Copy to .env
    cp .env.dev .env
    echo -e "${GREEN}.env file created successfully.${NC}"
fi

# Create directories if they don't exist
if [ "$COMPONENT" = "llm" ] || [ "$COMPONENT" = "all" ]; then
    for dir in api models utils tests/llm config; do
        if [ ! -d "$dir" ]; then
            echo -e "${YELLOW}Creating $dir directory...${NC}"
            mkdir -p "$dir"
        fi
    done
    
    # Make sure models/llm_model exists
    mkdir -p models/llm_model
fi

if [ "$COMPONENT" = "asr" ] || [ "$COMPONENT" = "all" ]; then
    # Create ASR directories if they don't exist
    for dir in asr/api asr/models asr/utils tests/asr; do
        if [ ! -d "$dir" ]; then
            echo -e "${YELLOW}Creating $dir directory...${NC}"
            mkdir -p "$dir"
        fi
    done
    
    # Make sure models/asr_model exists
    mkdir -p models/asr_model
    
    # Create shared directories
    mkdir -p shared/audio_input
    mkdir -p shared/audio_output
    mkdir -p shared/transcriptions
fi

echo -e "${GREEN}Development environment setup complete!${NC}"

echo -e "${YELLOW}To activate the virtual environment in a new shell, run:${NC}"
echo -e "  source venv/bin/activate"

if [ "$COMPONENT" = "llm" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}To start the LLM API server, run:${NC}"
    echo -e "  uvicorn api.main:app --reload --port 8000"
fi

if [ "$COMPONENT" = "asr" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}To start the ASR service, run:${NC}"
    echo -e "  cd asr && uvicorn api.main:app --reload --port 8001"
fi

if [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}To start the complete stack, run:${NC}"
    echo -e "  ./jarvis.sh start --container"
fi

echo -e "${GREEN}Happy coding!${NC}" 