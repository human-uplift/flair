#!/bin/bash
# Install main package in development mode, without extras
# Skipping word-embeddings extras to save time
pip install -e . --no-cache-dir

# Install only essential dev dependencies, not all of them
pip install black ruff mypy pytest
