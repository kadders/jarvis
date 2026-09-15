#!/bin/bash

# Jarvis AI Stack - Python setup script
# This script ensures a working Python environment for the requirements update

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Setting up Python environment for Jarvis AI Stack...${NC}"

# Find the correct python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}Error: Python not found. Please install Python 3.${NC}"
    exit 1
fi

echo -e "${YELLOW}Using Python command: $PYTHON_CMD${NC}"
$PYTHON_CMD --version

# Find the correct pip command
if command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
elif command -v pip &> /dev/null; then
    PIP_CMD="pip"
else
    PIP_CMD="$PYTHON_CMD -m pip"
    # Check if pip module is available
    if ! $PYTHON_CMD -c "import pip" &> /dev/null; then
        echo -e "${YELLOW}pip not found. Trying to install pip...${NC}"
        
        # Try to install pip using ensurepip
        if $PYTHON_CMD -m ensurepip &> /dev/null; then
            echo -e "${GREEN}pip installed successfully.${NC}"
        else
            echo -e "${RED}Failed to install pip. Please install pip manually.${NC}"
            echo -e "${YELLOW}On Debian/Ubuntu: sudo apt-get install python3-pip${NC}"
            echo -e "${YELLOW}On CentOS/RHEL: sudo yum install python3-pip${NC}"
            exit 1
        fi
    fi
fi

echo -e "${YELLOW}Using pip command: $PIP_CMD${NC}"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    
    # Check if venv module is available
    if ! $PYTHON_CMD -c "import venv" &> /dev/null; then
        echo -e "${RED}Python venv module not found. Trying to install...${NC}"
        # Try to install python3-venv or virtualenv
        if command -v apt-get &> /dev/null; then
            echo -e "${YELLOW}Installing python3-venv using apt-get...${NC}"
            sudo apt-get update && sudo apt-get install -y python3-venv
        elif command -v yum &> /dev/null; then
            echo -e "${YELLOW}Installing python3-venv using yum...${NC}"
            sudo yum install -y python3-venv
        else
            echo -e "${RED}Failed to install venv. Please install it manually.${NC}"
            echo -e "${YELLOW}On Debian/Ubuntu: sudo apt-get install python3-venv${NC}"
            echo -e "${YELLOW}On CentOS/RHEL: sudo yum install python3-venv${NC}"
            exit 1
        fi
    fi
    
    $PYTHON_CMD -m venv venv
    echo -e "${GREEN}Virtual environment created.${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate

# Update pip and install basic packages
echo -e "${YELLOW}Installing basic Python packages...${NC}"
pip install --upgrade pip setuptools wheel

# Install pip-tools
echo -e "${YELLOW}Installing pip-tools...${NC}"
pip install pip-tools

echo -e "${GREEN}Python environment setup completed!${NC}"
echo -e "${YELLOW}The virtual environment is now activated.${NC}"
echo -e "${YELLOW}You can now run the update_requirements.sh script.${NC}"
echo -e "${YELLOW}To activate this environment in the future, run:${NC}"
echo -e "  source venv/bin/activate" 