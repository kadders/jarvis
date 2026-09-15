#!/bin/bash

# Jarvis AI Stack - Health check script
# This script checks if the Jarvis services are running properly

set -e  # Exit on error

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
HOST="localhost"
LLM_PORT="8000"
ASR_PORT="8001"
TIMEOUT=5
SERVICE="all"  # Default to check all services

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--host)
      HOST="$2"
      shift 2
      ;;
    --llm-port)
      LLM_PORT="$2"
      shift 2
      ;;
    --asr-port)
      ASR_PORT="$2"
      shift 2
      ;;
    -t|--timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --llm)
      SERVICE="llm"
      shift
      ;;
    --asr)
      SERVICE="asr"
      shift
      ;;
    --help)
      echo "Usage: $0 [-h|--host HOST] [--llm-port PORT] [--asr-port PORT] [-t|--timeout TIMEOUT] [--llm|--asr]"
      echo "  -h, --host HOST       Host to check (default: localhost)"
      echo "  --llm-port PORT       LLM API port to check (default: 8000)"
      echo "  --asr-port PORT       ASR service port to check (default: 8001)"
      echo "  -t, --timeout TIMEOUT Timeout in seconds (default: 5)"
      echo "  --llm                 Check only LLM service"
      echo "  --asr                 Check only ASR service"
      echo "  --help                Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Function to check a service health
check_service_health() {
  local service_name=$1
  local service_host=$2
  local service_port=$3

  echo -e "${YELLOW}Checking health of $service_name at http://$service_host:$service_port/health...${NC}"

  # Try to connect to the health endpoint
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $TIMEOUT http://$service_host:$service_port/health)

  if [ "$HTTP_CODE" -eq 200 ]; then
    echo -e "${GREEN}$service_name is running and healthy!${NC}"
    
    # Get detailed health information
    echo -e "${YELLOW}Detailed health information:${NC}"
    curl -s http://$service_host:$service_port/health | jq || curl -s http://$service_host:$service_port/health
    
    return 0
  else
    echo -e "${RED}$service_name is not responding or unhealthy. HTTP code: $HTTP_CODE${NC}"
    
    # Check if Docker container is running
    if command -v docker &> /dev/null; then
      echo -e "${YELLOW}Checking if Docker container is running...${NC}"
      
      local container_name="jarvis_${service_name/_/-}"
      CONTAINER_ID=$(docker ps | grep "$container_name" | awk '{print $1}')
      
      if [ -n "$CONTAINER_ID" ]; then
        echo -e "${YELLOW}Container is running with ID: $CONTAINER_ID${NC}"
        echo -e "${YELLOW}Container logs:${NC}"
        docker logs --tail 20 $CONTAINER_ID
      else
        echo -e "${RED}Container is not running.${NC}"
        echo -e "${YELLOW}You can start the container with:${NC}"
        echo -e "  ./deploy.sh"
      fi
    fi
    
    return 1
  fi
}

# Check if curl is installed
if ! command -v curl &> /dev/null; then
    echo -e "${RED}Error: curl is not installed. Please install curl first.${NC}"
    exit 1
fi

# Keep track of overall status
OVERALL_STATUS=0

# Check LLM service if requested
if [ "$SERVICE" = "llm" ] || [ "$SERVICE" = "all" ]; then
  check_service_health "LLM API" "$HOST" "$LLM_PORT"
  if [ $? -ne 0 ]; then
    OVERALL_STATUS=1
  fi
fi

# Check ASR service if requested
if [ "$SERVICE" = "asr" ] || [ "$SERVICE" = "all" ]; then
  check_service_health "ASR Service" "$HOST" "$ASR_PORT"
  if [ $? -ne 0 ]; then
    OVERALL_STATUS=1
  fi
fi

# Exit with the overall status
exit $OVERALL_STATUS 