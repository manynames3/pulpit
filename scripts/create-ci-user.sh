#!/bin/bash
# Creates a least-privilege IAM user for GitHub Actions CI.
# This user can PLAN but never APPLY — read-only to all Pulpit resources.
#
# Usage: ./scripts/create-ci-user.sh
# Requirements: AWS CLI configured with admin credentials

set -e

USER_NAME="pulpit-ci-readonly"
POLICY_NAME="pulpit-ci-plan-policy"

echo "Creating IAM user: $USER_NAME"
aws iam create-user --user-name $USER_NAME

echo "Creating and attaching policy..."
POLICY_ARN=$(aws iam create-policy \
  --policy-name $POLICY_NAME \
  --policy-document file://scripts/ci-policy.json \
  --query 'Policy.Arn' \
  --output text)

aws iam attach-user-policy \
  --user-name $USER_NAME \
  --policy-arn $POLICY_ARN

echo "Generating access keys..."
aws iam create-access-key --user-name $USER_NAME

echo ""
echo "✅ Done. Copy the AccessKeyId and SecretAccessKey above."
echo "   Add them to GitHub: Settings → Secrets → Actions"
echo "   AWS_ACCESS_KEY_ID = AccessKeyId"
echo "   AWS_SECRET_ACCESS_KEY = SecretAccessKey"
echo ""
echo "⚠️  This is the only time the secret key is shown."
