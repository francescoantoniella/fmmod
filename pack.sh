#!/bin/bash
# Comprime la codebase escludendo file inutili
tar czf codebase.tgz \
  --exclude='.git' \
  --exclude='build' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.iq' \
  --exclude='*.mpx' \
  --exclude='*.raw' \
  --exclude='*.o' \
  --exclude='*.pyc' \
  --exclude='out.*' \
  --exclude='codebase.tgz' \
  .

echo "✅ codebase.tgz creato ($(du -sh codebase.tgz | cut -f1))"
