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

# Get list of modified Python files
echo "Checking for modified Python files..."
MODIFIED_FILES=$(git diff --name-only --cached --diff-filter=ACMR | grep '\.py$' || echo "")

if [ -z "$MODIFIED_FILES" ]; then
    # If no staged files, check all modified files
    MODIFIED_FILES=$(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$' || echo "")
fi

if [ -n "$MODIFIED_FILES" ]; then
    echo "Found modified Python files:"
    echo "$MODIFIED_FILES"
    
    # Format with black
    echo "Running black formatter..."
    if command -v black &> /dev/null; then
        black $MODIFIED_FILES || echo "Warning: black formatting had issues"
    else
        echo "Warning: black not found, skipping formatting"
    fi
else
    echo "No modified Python files found. Skipping formatting."
fi

# Basic sanity check
echo "Checking if package is importable..."
$PYTHON -c 'import flair' || {
    echo "Error: Failed to import flair"
    exit 1
}

echo "Pre-commit checks completed successfully!"
