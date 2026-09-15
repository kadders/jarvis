#!/bin/bash

# Jarvis AI Stack - Model Downloader
# This script downloads Hugging Face models to a local cache 
# to avoid downloading them during container build

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Load .env file if it exists
if [ -f ".env" ]; then
    source .env
fi

# Default model paths and names
LLM_MODEL_PATH=${MODEL_PATH:-"models/llm_model"}
LLM_MODEL_NAME=${MODEL_NAME:-"google/flan-t5-base"}

ASR_MODEL_PATH=${ASR_MODEL_PATH:-"models/asr_model"}
ASR_MODEL_NAME=${ASR_MODEL_NAME:-"facebook/wav2vec2-base-960h"}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --llm)
      LLM_MODEL_NAME="$2"
      shift 2
      ;;
    --asr)
      ASR_MODEL_NAME="$2"
      shift 2
      ;;
    --llm-path)
      LLM_MODEL_PATH="$2"
      shift 2
      ;;
    --asr-path)
      ASR_MODEL_PATH="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [options]"
      echo "Options:"
      echo "  --llm MODEL_NAME    Specify the LLM model to download (default: $LLM_MODEL_NAME)"
      echo "  --asr MODEL_NAME    Specify the ASR model to download (default: $ASR_MODEL_NAME)"
      echo "  --llm-path PATH     Specify the path to store the LLM model (default: $LLM_MODEL_PATH)"
      echo "  --asr-path PATH     Specify the path to store the ASR model (default: $ASR_MODEL_PATH)"
      echo "  -h, --help          Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Check if Python and required packages are installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed. Please install Python 3 first.${NC}"
    exit 1
fi

echo -e "${GREEN}Downloading models for Jarvis AI Stack...${NC}"

# Check if virtual environment exists and activate it
if [ -d "venv" ]; then
    echo -e "${YELLOW}Activating virtual environment...${NC}"
    source venv/bin/activate
fi

# Install required packages if not already installed
echo -e "${YELLOW}Checking required packages...${NC}"
pip install transformers torch tqdm --upgrade --quiet

# Create model directories
mkdir -p "$LLM_MODEL_PATH"
mkdir -p "$ASR_MODEL_PATH"

# Function to download model using Python and HuggingFace
download_model() {
    local model_name=$1
    local model_path=$2
    local model_type=$3

    echo -e "${YELLOW}Downloading $model_type model: $model_name to $model_path${NC}"
    python3 -c "
from transformers import AutoTokenizer, AutoModel, AutoModelForSeq2SeqLM, AutoProcessor
import os

model_name = '$model_name'
model_path = '$model_path'
model_type = '$model_type'

# First, try loading the tokenizer
print(f'Downloading tokenizer for {model_name}...')
tokenizer = AutoTokenizer.from_pretrained(model_name)
tokenizer.save_pretrained(model_path)
print(f'Tokenizer saved to {model_path}')

# Then, load the appropriate model type
if model_type == 'llm':
    print(f'Downloading LLM model {model_name}...')
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.save_pretrained(model_path)
elif model_type == 'asr':
    print(f'Downloading ASR model {model_name}...')
    processor = AutoProcessor.from_pretrained(model_name)
    processor.save_pretrained(model_path)
    model = AutoModel.from_pretrained(model_name)
    model.save_pretrained(model_path)

print(f'{model_type.upper()} model saved to {model_path}')
"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Successfully downloaded $model_type model: $model_name${NC}"
    else
        echo -e "${RED}Failed to download $model_type model: $model_name${NC}"
        exit 1
    fi
}

# Download LLM model
echo -e "${GREEN}Downloading LLM model...${NC}"
download_model "$LLM_MODEL_NAME" "$LLM_MODEL_PATH" "llm"

# Download ASR model
echo -e "${GREEN}Downloading ASR model...${NC}"
download_model "$ASR_MODEL_NAME" "$ASR_MODEL_PATH" "asr"

echo -e "${GREEN}All models downloaded successfully!${NC}"
echo -e "${YELLOW}You can now build the containers with ./build.sh${NC}" 