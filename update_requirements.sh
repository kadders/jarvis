#!/bin/bash

# Jarvis AI Stack - Package updater script
# This script updates the requirements.txt files with the latest package versions

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
UPDATE_ALL=false
COMPONENT="all" # Possible values: all, llm, asr

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
      UPDATE_ALL=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--llm|--asr] [--all]"
      echo "  --llm        Update only LLM API requirements"
      echo "  --asr        Update only ASR service requirements"
      echo "  --all        Update all packages to latest versions (without this, minor updates only)"
      echo "  -h, --help   Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

echo -e "${GREEN}Starting requirements update for Jarvis AI Stack...${NC}"

# Find the correct pip command to use
if command -v pip &> /dev/null; then
    PIP_CMD="pip"
elif command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
elif command -v python3 &> /dev/null; then
    PIP_CMD="python3 -m pip"
elif command -v python &> /dev/null; then
    PIP_CMD="python -m pip"
else
    echo -e "${RED}Error: No pip or Python command found. Please install Python and pip.${NC}"
    exit 1
fi

echo -e "${YELLOW}Using pip command: $PIP_CMD${NC}"

# Check if virtual environment is active
if [ -d "venv" ] && [ ! "$VIRTUAL_ENV" ]; then
    echo -e "${YELLOW}Activating virtual environment...${NC}"
    source venv/bin/activate
    
    # Recheck pip command after activating venv
    if command -v pip &> /dev/null; then
        PIP_CMD="pip"
    fi
fi

# Ensure setuptools is installed (contains distutils)
echo -e "${YELLOW}Installing setuptools (required for distutils module)...${NC}"
$PIP_CMD install setuptools wheel

# Check if a package name is valid
is_valid_package_name() {
    local package=$1
    
    # Whitelist of known valid packages that might not be found by pip search
    local whitelist=("pyaudio" "portaudio" "sounddevice" "pygame")
    
    # Check if package is in whitelist
    for item in "${whitelist[@]}"; do
        if [[ "$package" == "$item" ]]; then
            echo -e "${YELLOW}Package '$package' is in whitelist, treating as valid${NC}"
            return 0
        fi
    done
    
    # Check if the package name follows PEP 508 naming conventions
    # Only allow alphanumeric characters, underscores, and hyphens
    if [[ "$package" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
        # Test if package exists in PyPI - note this may fail for private packages
        if $PIP_CMD search "$package" &> /dev/null || $PIP_CMD show "$package" &> /dev/null; then
            return 0  # Package is valid
        else
            echo -e "${RED}Warning: Package '$package' doesn't appear to exist in PyPI${NC}"
            return 1  # Package is not valid
        fi
    else
        echo -e "${RED}Warning: '$package' is not a valid package name${NC}"
        return 1  # Package name is not valid
    fi
}

# Function to update a requirements file - simple version that won't hang
update_requirements() {
    local req_file=$1
    local output_file=$2
    local update_all=$3
    
    if [ ! -f "$req_file" ]; then
        echo -e "${RED}Error: $req_file not found.${NC}"
        return 1
    fi
    
    echo -e "${YELLOW}Updating $req_file...${NC}"
    
    # Create a temporary output file
    echo "# Updated requirements" > "$output_file"
    echo "# Generated on $(date)" >> "$output_file"
    
    # Process each line in the requirements file
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip comments and empty lines
        if [[ "$line" =~ ^#.*$ ]] || [[ -z "$line" ]]; then
            echo "$line" >> "$output_file"
            continue
        fi
        
        # Extract the package name and version specification
        if [[ "$line" =~ ^([a-zA-Z0-9_.-]+)(==?.*)$ ]]; then
            package="${BASH_REMATCH[1]}"
            version_spec="${BASH_REMATCH[2]}"
            
            # Validate the package name
            if is_valid_package_name "$package"; then
                if [ "$update_all" = true ]; then
                    # For full updates, get the latest version
                    echo -e "${YELLOW}Checking latest version for $package...${NC}"
                    $PIP_CMD install --upgrade "$package"
                    # Get the installed version
                    version=$($PIP_CMD show "$package" | grep -i version | awk '{print $2}')
                    echo "$package==$version" >> "$output_file"
                else
                    # For minor updates, preserve major version
                    current_version=$(echo "$version_spec" | grep -oP '==\K[0-9]+\.[0-9]+\.[0-9]+' || echo "")
                    if [ -n "$current_version" ]; then
                        major_version=$(echo "$current_version" | cut -d. -f1)
                        echo -e "${YELLOW}Checking latest version for $package with major version $major_version...${NC}"
                        $PIP_CMD install --upgrade "$package~=$major_version.0"
                        # Get the installed version
                        version=$($PIP_CMD show "$package" | grep -i version | awk '{print $2}')
                        echo "$package==$version" >> "$output_file"
                    else
                        # If we can't parse the version, keep the original line
                        echo "$line" >> "$output_file"
                    fi
                fi
            else
                echo -e "${RED}Skipping invalid package name: $package${NC}"
            fi
        else
            echo -e "${RED}Warning: Line doesn't match expected format: $line${NC}"
        fi
    done < "$req_file"
    
    echo -e "${GREEN}Updated $output_file successfully!${NC}"
}

# Update requirements based on component
if [ "$COMPONENT" = "llm" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}Updating LLM API requirements...${NC}"
    update_requirements "requirements.txt" "requirements.txt.new" "$UPDATE_ALL"
    
    # Backup the old file and replace with new one
    if [ -f "requirements.txt.new" ]; then
        mv "requirements.txt" "requirements.txt.bak"
        mv "requirements.txt.new" "requirements.txt"
        echo -e "${GREEN}LLM API requirements updated! Backup saved as requirements.txt.bak${NC}"
    fi
fi

if [ "$COMPONENT" = "asr" ] || [ "$COMPONENT" = "all" ]; then
    echo -e "${YELLOW}Updating ASR service requirements...${NC}"
    update_requirements "asr/requirements.txt" "asr/requirements.txt.new" "$UPDATE_ALL"
    
    # Backup the old file and replace with new one
    if [ -f "asr/requirements.txt.new" ]; then
        mv "asr/requirements.txt" "asr/requirements.txt.bak"
        mv "asr/requirements.txt.new" "asr/requirements.txt"
        echo -e "${GREEN}ASR service requirements updated! Backup saved as asr/requirements.txt.bak${NC}"
    fi
fi

# Clean all requirements files to ensure no invalid entries remain
if [ -f "clean_requirements.sh" ]; then
    echo -e "${YELLOW}Running clean_requirements.sh to ensure no invalid entries remain...${NC}"
    ./clean_requirements.sh
fi

# Show a summary of changes
echo -e "${GREEN}Requirements update completed!${NC}"
echo -e "${YELLOW}To rebuild the containers with the updated requirements, run:${NC}"
echo -e "  ./build.sh"

# Ask if user wants to rebuild now - with timeout to avoid hanging
echo -e "${YELLOW}Do you want to rebuild the containers now? (y/n) [Automatically continuing in 10 seconds...]${NC}"
read -t 10 -p "" rebuild_now

if [[ $rebuild_now =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}Running build script...${NC}"
    ./build.sh
else
    echo -e "${GREEN}Updates completed. Remember to rebuild containers when ready.${NC}"
fi 