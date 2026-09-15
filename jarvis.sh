#!/bin/bash

# Jarvis AI Stack - Main control script
# This script provides a unified interface to all jarvis operations

# Color codes for better visibility
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default command
CMD="help"

# Read command
if [ $# -gt 0 ]; then
    CMD="$1"
    shift
fi

# Show help
function show_help {
    echo -e "${BLUE}Jarvis AI Stack - Command Interface${NC}"
    echo
    echo "Usage: $0 COMMAND [OPTIONS]"
    echo
    echo "Commands:"
    echo "  build               Build the Docker images (supports --registry / --push)"
    echo "  push-images         Tag and push existing images to a registry"
    echo "                      (e.g. push-images --registry localhost:32000 --tag latest)"
    echo "  deploy              Deploy a Kubernetes stack/service (./deploy.sh --list)"
    echo "                      e.g. deploy tap-testing | deploy --profile gpu-rig ui"
    echo "  dev-setup           Set up development environment"
    echo "  test                Run tests"
    echo "  health              Check API health"
    echo "  start               Start the services (either in container or directly)"
    echo "  stop                Stop running containers"
    echo "  logs                Show container logs"
    echo "  asr-generate        Generate test audio files for ASR testing"
    echo "  update-requirements Update Python dependencies"
    echo "  help                Show this help message"
    echo
    echo "For help on specific commands, run:"
    echo "  $0 COMMAND --help"
}

# Check if a script exists
function check_script {
    script="$1"
    if [ ! -f "$script" ]; then
        echo -e "${RED}Error: Required script $script not found.${NC}"
        exit 1
    fi
}

# Execute command
case "$CMD" in
    build)
        check_script "./build.sh"
        ./build.sh "$@"
        ;;
    push-images)
        # Tag-and-push only (re-runs build.sh with --skip-tests --skip-models
        # --no-prefetch-deps so it only retags). Caller must pass --registry.
        check_script "./build.sh"
        ./build.sh --skip-tests --skip-models --no-prefetch-deps --push "$@"
        ;;
    deploy)
        check_script "./deploy.sh"
        ./deploy.sh "$@"
        ;;
    dev-setup)
        check_script "./setup_dev.sh"
        ./setup_dev.sh "$@"
        ;;
    test)
        check_script "./run_tests.sh"
        ./run_tests.sh "$@"
        ;;
    health)
        check_script "./check_health.sh"
        ./check_health.sh "$@"
        ;;
    start)
        # Determine if we should start in container or directly
        if [ "$1" = "--container" ] || [ "$1" = "-c" ]; then
            shift
            echo -e "${YELLOW}Kubernetes deploy: use ./deploy.sh <stack> (see ./deploy.sh --list).${NC}"
            echo -e "${YELLOW}Local Compose: docker compose up -d${NC}"
            exit 1
        else
            # Start directly using uvicorn (only for LLM API)
            if [ -d "venv" ] && [ ! "$VIRTUAL_ENV" ]; then
                echo -e "${YELLOW}Activating virtual environment...${NC}"
                source venv/bin/activate
            fi
            
            echo -e "${GREEN}Starting Jarvis LLM API using uvicorn...${NC}"
            echo -e "${YELLOW}Note: This only starts the LLM API service. For the complete stack including ASR, use docker compose.${NC}"
            uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
        fi
        ;;
    stop)
        echo -e "${YELLOW}Stopping Jarvis AI Stack containers...${NC}"
        if command -v docker-compose &> /dev/null; then
            docker-compose down
            echo -e "${GREEN}Containers stopped.${NC}"
        else
            echo -e "${RED}Error: docker-compose not found.${NC}"
            exit 1
        fi
        ;;
    logs)
        echo -e "${YELLOW}Showing container logs...${NC}"
        if command -v docker-compose &> /dev/null; then
            if [ "$1" = "asr" ]; then
                echo -e "${YELLOW}Showing ASR service logs...${NC}"
                docker-compose logs asr_service "$@"
            elif [ "$1" = "llm" ]; then
                echo -e "${YELLOW}Showing LLM API logs...${NC}"
                docker-compose logs llm_api "$@"
            else
                echo -e "${YELLOW}Showing all logs...${NC}"
                docker-compose logs "$@"
            fi
        else
            echo -e "${RED}Error: docker-compose not found.${NC}"
            exit 1
        fi
        ;;
    update-requirements)
        check_script "./update_requirements.sh"
        ./update_requirements.sh "$@"
        ;;
    asr-generate)
        # Run the ASR audio generation script
        if [ -f "asr/generate_test_audio.py" ]; then
            echo -e "${YELLOW}Generating test audio files...${NC}"
            python asr/generate_test_audio.py "$@"
        else
            echo -e "${RED}Error: ASR audio generation script not found.${NC}"
            exit 1
        fi
        ;;
    help|*)
        show_help
        ;;
esac 