#!/bin/bash
# Build Lambda deployment packages for Linux x86_64.
# Uses Docker to match the exact AWS Lambda runtime environment.
# Required because compiled dependencies (.so files) are platform-specific.
#
# Prerequisites: Docker must be running
# Usage: ./scripts/build-lambda.sh

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
  echo "ERROR: Docker is not running. Start Docker Desktop and try again."
  exit 1
fi

build() {
  NAME=$1
  SRC="$ROOT/lambda/$NAME"
  PKG="$SRC/package"

  echo "Building $NAME Lambda (linux/amd64)..."

  rm -rf "$PKG"
  mkdir -p "$PKG"

  # Run pip inside the exact Lambda runtime container
  docker run --rm \
    --platform linux/amd64 \
    -v "$SRC":/var/task \
    -v "$PKG":/var/package \
    public.ecr.aws/lambda/python:3.12 \
    pip install -r /var/task/requirements.txt \
      --target /var/package \
      --quiet \
      --upgrade

  # Copy handler into package
  cp "$SRC/handler.py" "$PKG/"

  echo "  ✅ $NAME ready"
}

build ingest
build query

echo ""
echo "Done. Run: terraform apply -var-file=environments/dev/terraform.tfvars"
