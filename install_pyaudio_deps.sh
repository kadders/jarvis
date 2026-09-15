#!/bin/bash

# Jarvis AI Stack - PyAudio Dependencies Installer
# This script installs system dependencies required for PyAudio

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Installing system dependencies for PyAudio...${NC}"

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
elif type lsb_release >/dev/null 2>&1; then
    OS=$(lsb_release -si)
else
    OS=$(uname -s)
fi

# Detect Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1-2)
echo -e "${YELLOW}Detected Python version: ${PYTHON_VERSION}${NC}"

# Install dependencies based on OS
if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
    echo -e "${YELLOW}Detected Debian/Ubuntu-based system${NC}"
    sudo apt-get update
    # Install Python dev package for the detected version
    sudo apt-get install -y portaudio19-dev python3-pyaudio libasound2-dev libportaudio2 python3-dev python${PYTHON_VERSION}-dev
elif [[ "$OS" == *"Fedora"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
    echo -e "${YELLOW}Detected Red Hat/Fedora-based system${NC}"
    sudo dnf install -y portaudio-devel python3-devel
elif [[ "$OS" == *"Arch"* ]]; then
    echo -e "${YELLOW}Detected Arch-based system${NC}"
    sudo pacman -S --noconfirm portaudio python-pip python
elif [[ "$OS" == *"macOS"* ]] || [[ "$OS" == *"Darwin"* ]]; then
    echo -e "${YELLOW}Detected macOS${NC}"
    brew install portaudio python
else
    echo -e "${RED}Unsupported operating system: $OS${NC}"
    echo -e "${YELLOW}Please install portaudio dependencies manually for your system${NC}"
    exit 1
fi

echo -e "${GREEN}System dependencies installed successfully!${NC}"
echo -e "${YELLOW}You can now install PyAudio with pip:${NC}"
echo -e "  pip install pyaudio"

# Check if virtual environment is active
if [ -d "venv" ]; then
    echo -e "${YELLOW}Installing PyAudio in virtual environment...${NC}"
    source venv/bin/activate
    pip install pyaudio
    echo -e "${GREEN}PyAudio installed in virtual environment${NC}"
fi 