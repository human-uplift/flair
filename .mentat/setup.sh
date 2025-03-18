#!/bin/bash
set -e

# Find Python executable (try python3 first, then python)
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "Error: Python not found"
    exit 1
fi

echo "Using Python: $($PYTHON --version)"

# Install main package in development mode, without extras
echo "Installing flair package..."
$PYTHON -m pip install -e . --no-cache-dir

# Install only essential dev dependencies, not all of them
echo "Installing development tools..."
$PYTHON -m pip install black ruff --no-cache-dir
