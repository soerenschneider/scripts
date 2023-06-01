#!/usr/bin/env bash

PASS_PATH="infrastructure/cloud/aws-iam"

SECRET="$(pass ${PASS_PATH})"

export AWS_ACCESS_KEY_ID=$(echo "${SECRET}" | grep ^AKIA)
export AWS_SECRET_ACCESS_KEY=$(echo "${SECRET}" | grep -v ^AKIA)

# Command to execute
command="$@"

# Execute the command with injected environment variables
eval "$command"
