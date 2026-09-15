#!/bin/bash

# Script to move files to their respective cache locations

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Moving files to their respective cache locations...${NC}"

# Create cache directories if they don't exist
mkdir -p cache/build/pip
mkdir -p cache/build/apt
mkdir -p cache/models/llm_model
mkdir -p cache/models/asr_model
mkdir -p cache/docker

# Move all wheel files to pip cache
echo -e "${YELLOW}Moving wheel files to pip cache...${NC}"
mv *.whl cache/build/pip/ 2>/dev/null || true

# Move model files to their respective cache directories
echo -e "${YELLOW}Moving model files to models cache...${NC}"
if [ -d "models/llm_model" ]; then
    cp -r models/llm_model/* cache/models/llm_model/ 2>/dev/null || true
fi
if [ -d "models/asr_model" ]; then
    cp -r models/asr_model/* cache/models/asr_model/ 2>/dev/null || true
fi

# Set permissions
echo -e "${YELLOW}Setting permissions for cache directories...${NC}"
chmod -R 777 cache

echo -e "${GREEN}Files have been moved to their respective cache locations:${NC}"
echo -e "  - Pip packages: cache/build/pip/"
echo -e "  - LLM Models: cache/models/llm_model/"
echo -e "  - ASR Models: cache/models/asr_model/"
echo -e "  - Docker cache: cache/docker/"
echo -e "  - Apt cache: cache/build/apt/" 