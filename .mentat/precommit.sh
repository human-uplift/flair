#!/bin/bash
# Format code with black
black flair tests

# Run ruff with fix to automatically fix linting issues
ruff check --fix flair tests

# Type checking with mypy (doesn't fix issues but reports them)
mypy flair tests

# Run basic sanity test to ensure the package is importable
python -c 'import flair'
