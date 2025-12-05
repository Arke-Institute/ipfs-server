#!/bin/bash
set -e

# Setup weekly auto-restart for IPFS EC2 instance using Lambda + EventBridge
# Runs every Sunday at 3 AM UTC

INSTANCE_ID="${1:-i-0443444abcd3ed689}"
SCHEDULE_EXPRESSION="cron(0 3 ? * SUN *)"  # Sunday 3 AM UTC
RULE_NAME="arke-ipfs-weekly-restart"
FUNCTION_NAME="arke-ipfs-reboot-function"
ROLE_NAME="arke-ipfs-lambda-reboot-role"

echo "========================================="
echo "  Setting up Weekly Auto-Restart"
echo "========================================="
echo ""
echo "Instance ID: $INSTANCE_ID"
echo "Schedule: Every Sunday at 3:00 AM UTC"
echo ""

# Step 1: Create IAM role for Lambda
echo "🔐 Creating IAM role for Lambda..."
if aws iam get-role --role-name "$ROLE_NAME" 2>/dev/null; then
    echo "ℹ️  Role $ROLE_NAME already exists"
else
    cat > /tmp/lambda-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/lambda-trust-policy.json \
        --description "Allows Lambda to reboot Arke IPFS EC2 instance"

    # Attach basic Lambda execution policy
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    # Create inline policy for rebooting
    cat > /tmp/lambda-reboot-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RebootInstances",
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    }
  ]
}
EOF

    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "ec2-reboot-policy" \
        --policy-document file:///tmp/lambda-reboot-policy.json

    rm /tmp/lambda-trust-policy.json /tmp/lambda-reboot-policy.json

    echo "✅ Role created, waiting 10s for propagation..."
    sleep 10
fi

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# Step 2: Create Lambda function
echo ""
echo "⚡ Creating Lambda function..."

cat > /tmp/lambda_function.py <<EOF
import boto3
import os

ec2 = boto3.client('ec2')

def lambda_handler(event, context):
    instance_id = os.environ['INSTANCE_ID']
    print(f'Rebooting instance {instance_id}...')

    try:
        ec2.reboot_instances(InstanceIds=[instance_id])
        print(f'Successfully initiated reboot for {instance_id}')
        return {'statusCode': 200, 'body': f'Rebooted {instance_id}'}
    except Exception as e:
        print(f'Error rebooting instance: {str(e)}')
        raise
EOF

cd /tmp
zip lambda_function.zip lambda_function.py

if aws lambda get-function --function-name "$FUNCTION_NAME" 2>/dev/null; then
    echo "ℹ️  Lambda function exists, updating code..."
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb://lambda_function.zip > /dev/null

    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --environment "Variables={INSTANCE_ID=$INSTANCE_ID}" > /dev/null
else
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime python3.11 \
        --role "$ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file fileb://lambda_function.zip \
        --timeout 30 \
        --environment "Variables={INSTANCE_ID=$INSTANCE_ID}" \
        --description "Reboots Arke IPFS EC2 instance on schedule" > /dev/null
fi

FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --query 'Configuration.FunctionArn' --output text)
rm lambda_function.py lambda_function.zip

echo "✅ Lambda function ready: $FUNCTION_NAME"

# Step 3: Create EventBridge rule
echo ""
echo "📅 Creating EventBridge rule..."
aws events put-rule \
    --name "$RULE_NAME" \
    --description "Weekly restart for Arke IPFS server every Sunday at 3 AM UTC" \
    --schedule-expression "$SCHEDULE_EXPRESSION" \
    --state ENABLED > /dev/null

echo "✅ Rule created: $RULE_NAME"

# Step 4: Add Lambda permission for EventBridge
echo ""
echo "🔑 Granting EventBridge permission to invoke Lambda..."
aws lambda add-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id EventBridgeInvoke \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:us-east-1:$(aws sts get-caller-identity --query Account --output text):rule/$RULE_NAME" \
    2>/dev/null || echo "ℹ️  Permission already exists"

# Step 5: Add Lambda as target
echo ""
echo "🎯 Adding Lambda as target..."
aws events put-targets \
    --rule "$RULE_NAME" \
    --targets "Id=1,Arn=$FUNCTION_ARN" > /dev/null

echo "✅ Target configured"
echo ""
echo "========================================="
echo "  Auto-Restart Setup Complete!"
echo "========================================="
echo ""
echo "Schedule: Every Sunday at 3:00 AM UTC"
echo "Instance: $INSTANCE_ID"
echo "Lambda: $FUNCTION_NAME"
echo ""
echo "To verify:"
echo "  aws events list-rules --name-prefix arke-ipfs"
echo "  aws events list-targets-by-rule --rule $RULE_NAME"
echo ""
echo "To test manually:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME /tmp/output.json"
echo ""
echo "To disable:"
echo "  aws events disable-rule --name $RULE_NAME"
echo ""
echo "To delete:"
echo "  aws events remove-targets --rule $RULE_NAME --ids 1"
echo "  aws events delete-rule --name $RULE_NAME"
echo "  aws lambda delete-function --function-name $FUNCTION_NAME"
echo ""
