#!/bin/bash
set -e

# Run black formatter on modified files only
# Get list of modified Python files
MODIFIED_FILES=$(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$' || echo "")

if [ -n "$MODIFIED_FILES" ]; then
  echo "Formatting modified files with black..."
  black $MODIFIED_FILES
  
  echo "Running ruff with fix on modified files..."
  ruff check --fix $MODIFIED_FILES
else
  echo "No modified Python files found. Skipping formatting and linting."
fi

# Basic sanity check - just import the package
echo "Checking if package is importable..."
python -c 'import flair'

echo "Pre-commit checks completed successfully!"
