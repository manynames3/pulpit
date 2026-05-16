#!/bin/bash
# Build Lambda deployment packages with dependencies for AWS Lambda.
#
# Usage: ./scripts/build-lambda.sh

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LAMBDA_PLATFORM=${LAMBDA_PLATFORM:-manylinux2014_x86_64}
LAMBDA_PYTHON_VERSION=${LAMBDA_PYTHON_VERSION:-3.12}

build() {
  NAME=$1
  SRC="$ROOT/lambda/$NAME"
  PKG="$SRC/package"

  echo "Building $NAME Lambda..."
  rm -rf "$PKG"
  mkdir -p "$PKG"

  python3 -m pip install -r "$SRC/requirements.txt" \
    --target "$PKG" \
    --quiet \
    --upgrade \
    --platform "$LAMBDA_PLATFORM" \
    --implementation cp \
    --python-version "$LAMBDA_PYTHON_VERSION" \
    --only-binary=:all:

  cp "$SRC/handler.py" "$PKG/"
  echo "  $NAME ready"
}

build ingest
build query
build admin-trigger

echo ""
echo "Done. Run: terraform apply -var-file=environments/dev/terraform.tfvars"
