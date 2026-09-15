#!/bin/bash

# Prefetch PyTorch dependencies script for Jarvis AI Stack
# This script downloads PyTorch and other large ML libraries to the local pip cache

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Create cache directory if it doesn't exist (aligned with Docker Compose / build.sh)
mkdir -p cache/build/pip
chmod -R 777 cache/build/pip

# Get absolute path to cache directory
PIP_CACHE_DIR=$(realpath cache/build/pip)

echo -e "${GREEN}Prefetching PyTorch dependencies to: ${PIP_CACHE_DIR}${NC}"

# Function to extract and prefetch ML dependencies
prefetch_deps() {
    local req_file=$1
    local service_name=$2
    
    if [ -f "$req_file" ]; then
        echo -e "${YELLOW}Extracting ML dependencies from ${req_file}...${NC}"
        grep -E "torch|torchaudio|transformers|numpy" "$req_file" > ml_requirements_temp.txt || echo "No ML dependencies found"
        
        if [ -s ml_requirements_temp.txt ]; then
            echo -e "${GREEN}Found the following ML dependencies for ${service_name}:${NC}"
            cat ml_requirements_temp.txt
            
            # Create a temporary Python virtual environment for prefetching
            echo -e "${YELLOW}Creating temporary virtual environment for prefetching...${NC}"
            python3 -m venv .venv_prefetch
            source .venv_prefetch/bin/activate
            
            # Configure pip to use our cache directory
            export PIP_CACHE_DIR=$PIP_CACHE_DIR
            
            # Install pip and wheel first
            echo -e "${YELLOW}Upgrading pip and installing wheel...${NC}"
            pip3 install --upgrade pip wheel
            
            # Download the dependencies to cache
            echo -e "${YELLOW}Prefetching ML dependencies to cache...${NC}"
            pip3 download -r ml_requirements_temp.txt
            
            # Verify cache contents
            echo -e "${GREEN}Cache directory contents:${NC}"
            ls -lah $PIP_CACHE_DIR
            
            # Clean up
            echo -e "${YELLOW}Cleaning up temporary environment...${NC}"
            deactivate
            rm -rf .venv_prefetch ml_requirements_temp.txt
            
            echo -e "${GREEN}Prefetching complete for ${service_name}!${NC}"
        else
            echo -e "${RED}No ML dependencies found in ${req_file}${NC}"
        fi
    else
        echo -e "${RED}Error: ${req_file} not found${NC}"
    fi
}

# Prefetch ASR dependencies
echo -e "${GREEN}Prefetching ASR dependencies...${NC}"
prefetch_deps "asr/requirements.txt" "ASR service"

# Prefetch LLM API dependencies
echo -e "${GREEN}Prefetching LLM API dependencies...${NC}"
prefetch_deps "requirements.txt" "LLM API service"

echo -e "${GREEN}All dependencies have been prefetched!${NC}"
echo -e "${GREEN}These packages will be available for Docker builds.${NC}" 