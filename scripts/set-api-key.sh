#!/bin/bash
# Store YouTube API key in AWS SSM Parameter Store.
# Run this ONCE after terraform apply — never commit the key to git.
#
# Usage:
#   chmod +x scripts/set-api-key.sh
#   ./scripts/set-api-key.sh dev YOUR_API_KEY

ENVIRONMENT=${1:-dev}
API_KEY=$2

if [ -z "$API_KEY" ]; then
  echo "Usage: ./scripts/set-api-key.sh <environment> <api-key>"
  exit 1
fi

aws ssm put-parameter \
  --name "/pulpit/${ENVIRONMENT}/youtube_api_key" \
  --value "$API_KEY" \
  --type SecureString \
  --overwrite

echo "✅ Key stored at /pulpit/${ENVIRONMENT}/youtube_api_key"
echo "   Lambda reads it automatically on next invocation."
