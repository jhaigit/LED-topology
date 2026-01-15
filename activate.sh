#!/bin/bash
# Activate machine-specific virtual environment
#
# Usage: source activate.sh
#
# Creates venv if it doesn't exist, then activates it.

VENV_DIR=".venv-$(hostname)"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    echo "Installing dependencies..."
    pip install -r requirements.txt
else
    source "$VENV_DIR/bin/activate"
fi

echo "Activated: $VENV_DIR"
