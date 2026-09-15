#!/bin/bash

# Jarvis AI Stack - Requirements Cleanup Script
# This script removes problematic packages from requirements files

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Cleaning up requirements files...${NC}"

clean_requirements_file() {
    local input_file=$1
    local output_file="${input_file}.clean"
    
    echo -e "${YELLOW}Cleaning ${input_file}...${NC}"
    
    # Start with a clean slate
    > "${output_file}"
    
    # Keep track of the current section
    local current_section=""
    
    # Process file line by line
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Keep comments and section headers
        if [[ "$line" =~ ^#.*$ ]]; then
            echo "$line" >> "${output_file}"
            # Update current section if this is a section header
            if [[ "$line" =~ ^#[[:space:]]*(.*[Dd]ependencies|[Uu]tilities|[Tt]esting|[Mm]emory).*$ ]]; then
                current_section=$(echo "$line" | sed -E 's/^#[[:space:]]*(.*)[[:space:]]*$/\1/')
            fi
            continue
        fi
        
        # Skip empty lines
        if [[ -z "$line" ]]; then
            echo "" >> "${output_file}"
            continue
        fi
        
        # Only keep lines that match the pattern for package requirements: name==version
        if [[ "$line" =~ ^[a-zA-Z0-9_-]+==?[0-9]+(\.[0-9]+)*$ ]]; then
            echo "$line" >> "${output_file}"
        fi
    done < "$input_file"
    
    # Move cleaned file to replace the original
    mv "${output_file}" "$input_file"
    
    echo -e "${GREEN}${input_file} cleaned successfully!${NC}"
}

# Clean main requirements.txt
if [ -f "requirements.txt" ]; then
    clean_requirements_file "requirements.txt"
else
    echo -e "${RED}Main requirements.txt file not found.${NC}"
fi

# Clean new requirements file if it exists
if [ -f "requirements.txt.new" ]; then
    clean_requirements_file "requirements.txt.new"
fi

# Clean ASR requirements.txt
if [ -f "asr/requirements.txt" ]; then
    clean_requirements_file "asr/requirements.txt"
else
    echo -e "${RED}ASR requirements.txt file not found.${NC}"
fi

# Clean ASR new requirements file if it exists
if [ -f "asr/requirements.txt.new" ]; then
    clean_requirements_file "asr/requirements.txt.new"
fi

echo -e "${GREEN}All requirements files cleaned successfully!${NC}"
echo -e "${YELLOW}You can now run 'build.sh' to build the containers.${NC}" 