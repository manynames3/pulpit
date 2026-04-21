#!/bin/bash
# Build Lambda deployment packages with dependencies.
# All dependencies are now pure Python — no Docker needed.
#
# Usage: ./scripts/build-lambda.sh

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)

build() {
  NAME=$1
  SRC="$ROOT/lambda/$NAME"
  PKG="$SRC/package"

  echo "Building $NAME Lambda..."
  rm -rf "$PKG"
  mkdir -p "$PKG"

  pip3 install -r "$SRC/requirements.txt" \
    --target "$PKG" \
    --quiet \
    --upgrade \
    --only-binary=:none: \
    --no-deps

  # Install deps individually to avoid pulling in compiled sub-deps
  pip3 install -r "$SRC/requirements.txt" \
    --target "$PKG" \
    --quiet \
    --upgrade

  cp "$SRC/handler.py" "$PKG/"
  echo "  ✅ $NAME ready"
}

build ingest
build query

echo ""
echo "Done. Run: terraform apply -var-file=environments/dev/terraform.tfvars"
