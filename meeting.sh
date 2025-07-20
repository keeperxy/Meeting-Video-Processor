#!/bin/bash

# Meeting Video Processor Wrapper Script
# Loads environment variables and runs the Python tool

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/meeting_processor.py"

# Load environment variables if .env exists
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    export $(cat "$SCRIPT_DIR/.env" | grep -v '^#' | xargs)
fi

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check Python dependencies
check_python_deps() {
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed or not in PATH"
        exit 1
    fi
    
    # Check if virtual environment exists and activate it
    if [[ -d "$SCRIPT_DIR/.venv" ]]; then
        source "$SCRIPT_DIR/.venv/bin/activate"
        print_status "Virtual environment activated"
    else
        print_warning "Virtual environment not found. Creating it now..."
        python3 -m venv "$SCRIPT_DIR/.venv"
        source "$SCRIPT_DIR/.venv/bin/activate"
        print_status "Installing dependencies in virtual environment..."
        pip install -r "$SCRIPT_DIR/requirements.txt"
    fi
    
    # Check if required packages are installed
    python -c "import requests, questionary, ffmpeg, google.genai" 2>/dev/null || {
        print_error "Required Python packages not found in virtual environment."
        print_error "Please run: ./meeting.sh --setup"
        exit 1
    }
}

# Function to check if Python script exists
check_python_script() {
    if [[ ! -f "$PYTHON_SCRIPT" ]]; then
        print_error "Python script not found: $PYTHON_SCRIPT"
        exit 1
    fi
}

# Function to check system dependencies
check_system_deps() {
    local missing_deps=()
    
    # Check for HandBrakeCLI
    if ! command -v HandBrakeCLI &> /dev/null; then
        missing_deps+=("HandBrakeCLI")
    fi
    
    # Check for ffmpeg
    if ! command -v ffmpeg &> /dev/null; then
        missing_deps+=("ffmpeg")
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        print_warning "Missing system dependencies: ${missing_deps[*]}"
        print_status "Please install them:"
        echo "  macOS: brew install handbrake ffmpeg"
        echo "  Ubuntu/Debian: sudo apt install handbrake-cli ffmpeg"
        echo "  Windows: Download from https://handbrake.fr/ and https://ffmpeg.org/"
        echo ""
        print_status "Continuing anyway (some features may not work)..."
    fi
}

# Function to show help
show_help() {
    echo "Meeting Video Processor Wrapper"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --video FILE              Path to video file"
    echo "  --prompt TEMPLATE         Prompt template (prompt.txt, prompt_wo_transcript.txt, prompt_only_transcript.txt, 'only transcript', 'without transcript')"
    echo "  --notes TEXT              Notes content"
    echo "  -d, --directory DIR       Specify target directory (instead of auto-creating timestamp-based directory)"
    echo "  --debug                   Enable debug logging"
    echo "  --dry-run                 Simulate processing without making changes"
    echo "  --no-cleanup              Don't clean up temporary files"
    echo "  --setup                   Install Python dependencies"
    echo "  --help, -h                Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --video meeting.mp4"
    echo "  $0 --video meeting.mp4 --prompt prompt_only_transcript.txt"
    echo "  $0 --video meeting.mp4 --prompt 'only transcript'"
    echo "  $0 --video meeting.mp4 --dry-run --debug"
    echo "  $0 --setup"
    echo ""
    echo "The script automatically:"
    echo "  - Loads environment variables from .env file"
    echo "  - Checks and installs Python dependencies if needed"
    echo "  - Runs the Python meeting processor"
}

# Main function
main() {
    print_status "Meeting Video Processor Wrapper"
    print_status "Script directory: $SCRIPT_DIR"
    
    # Check if Python script exists
    check_python_script
    
    # Check system dependencies
    check_system_deps
    
    # Handle special cases
    if [[ "$1" == "--help" || "$1" == "-h" ]]; then
        show_help
        exit 0
    fi
    
    if [[ "$1" == "--setup" ]]; then
        print_status "Setting up Python environment..."
        
        # Create virtual environment if it doesn't exist
        if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
            print_status "Creating virtual environment..."
            python3 -m venv "$SCRIPT_DIR/.venv"
        fi
        
        # Activate virtual environment and install dependencies
        print_status "Installing dependencies in virtual environment..."
        source "$SCRIPT_DIR/.venv/bin/activate"
        pip install -r "$SCRIPT_DIR/requirements.txt"
        
        print_success "Setup completed successfully!"
        print_status "Virtual environment created at: $SCRIPT_DIR/.venv"
        print_status "To activate manually: source $SCRIPT_DIR/.venv/bin/activate"
        exit 0
    fi
    
    # Check Python dependencies
    check_python_deps
    
    # Run the Python script with all arguments
    print_status "Running meeting processor..."
    echo ""
    python "$PYTHON_SCRIPT" "$@"
    
    # Check exit code
    if [[ $? -eq 0 ]]; then
        print_success "Meeting processor completed successfully!"
    else
        print_error "Meeting processor failed with exit code $?"
        exit 1
    fi
}

# Run main function with all arguments
main "$@" 