#!/bin/bash

# Jarvis AI Stack - Test runner script
# This script runs tests for the Jarvis AI Stack

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
VERBOSE=false
COVERAGE=false
TEST_PATH="tests"
TEST_COMPONENT="all"  # Default to test all components

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -v|--verbose)
      VERBOSE=true
      shift
      ;;
    -c|--coverage)
      COVERAGE=true
      shift
      ;;
    -p|--path)
      TEST_PATH="$2"
      shift 2
      ;;
    --llm)
      TEST_COMPONENT="llm"
      shift
      ;;
    --asr)
      TEST_COMPONENT="asr"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [-v|--verbose] [-c|--coverage] [-p|--path PATH] [--llm|--asr]"
      echo "  -v, --verbose    Show verbose output"
      echo "  -c, --coverage   Run with coverage report"
      echo "  -p, --path PATH  Path to the tests to run (default: tests)"
      echo "  --llm            Test only LLM API component"
      echo "  --asr            Test only ASR service component"
      echo "  -h, --help       Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

echo -e "${GREEN}Running tests for Jarvis AI Stack...${NC}"

# Ensure virtual environment is active
if [ -d "venv" ] && [ ! "$VIRTUAL_ENV" ]; then
    echo -e "${YELLOW}Activating virtual environment...${NC}"
    source venv/bin/activate
fi

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${YELLOW}pytest not found. Installing...${NC}"
    pip install pytest pytest-cov
fi

# Set up component-specific test paths
if [ "$TEST_COMPONENT" = "llm" ]; then
    if [ "$TEST_PATH" = "tests" ]; then
        # Only override if user didn't specify a custom path
        TEST_PATH="tests/llm"
    fi
    echo -e "${YELLOW}Testing LLM API component...${NC}"
elif [ "$TEST_COMPONENT" = "asr" ]; then
    if [ "$TEST_PATH" = "tests" ]; then
        # Only override if user didn't specify a custom path
        TEST_PATH="tests/asr"
    fi
    echo -e "${YELLOW}Testing ASR service component...${NC}"
else
    echo -e "${YELLOW}Testing all components...${NC}"
fi

# Ensure test directory exists
if [ ! -d "$TEST_PATH" ]; then
    echo -e "${YELLOW}Test directory $TEST_PATH does not exist. Creating it...${NC}"
    mkdir -p "$TEST_PATH"
fi

# Set pytest arguments
PYTEST_ARGS=()

if [ "$VERBOSE" = true ]; then
    PYTEST_ARGS+=("-v")
fi

if [ "$COVERAGE" = true ]; then
    # Check if pytest-cov is installed
    if ! pip show pytest-cov &> /dev/null; then
        echo -e "${YELLOW}pytest-cov not found. Installing...${NC}"
        pip install pytest-cov
    fi
    
    if [ "$TEST_COMPONENT" = "llm" ]; then
        PYTEST_ARGS+=("--cov=api,models,utils")
    elif [ "$TEST_COMPONENT" = "asr" ]; then
        PYTEST_ARGS+=("--cov=asr")
    else
        PYTEST_ARGS+=("--cov=api,models,utils,asr")
    fi
    
    PYTEST_ARGS+=("--cov-report=term")
    PYTEST_ARGS+=("--cov-report=html")
fi

# Run the tests
echo -e "${YELLOW}Running pytest with arguments: ${PYTEST_ARGS[@]} $TEST_PATH${NC}"
python -m pytest "${PYTEST_ARGS[@]}" "$TEST_PATH"

# Check exit code
if [ $? -eq 0 ]; then
    echo -e "${GREEN}All tests passed successfully!${NC}"
    
    if [ "$COVERAGE" = true ]; then
        echo -e "${YELLOW}Coverage report generated in htmlcov/index.html${NC}"
    fi
else
    echo -e "${RED}Tests failed.${NC}"
    exit 1
fi 