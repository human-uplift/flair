#!/bin/bash
# Install main package with word-embeddings extras
pip install -e .[word-embeddings]

# Install development dependencies
pip install -r requirements-dev.txt
