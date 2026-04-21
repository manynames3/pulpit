#!/bin/bash
# Build Lambda deployment packages with dependencies included.
# Run this before terraform apply whenever lambda code or requirements change.
#
# Usage: ./scripts/build-lambda.sh

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)

build() {
  NAME=$1
  SRC="$ROOT/lambda/$NAME"
  BUILD="$ROOT/lambda/$NAME/package"

  echo "Building $NAME Lambda..."

  rm -rf "$BUILD"
  mkdir -p "$BUILD"

  # Install dependencies into package dir
  pip3 install -r "$SRC/requirements.txt" \
    --target "$BUILD" \
    --quiet \
    --upgrade

  # Copy handler into package dir
  cp "$SRC/handler.py" "$BUILD/"

  echo "  ✅ $NAME ready at lambda/$NAME/package/"
}

build ingest
build query

echo ""
echo "Done. Run terraform apply to deploy updated Lambdas."
