#!/bin/bash

# Check if the username was provided as an argument
if [ -z "$1" ]; then
  echo "Usage: $0 <username>"
  exit 1
fi

USERNAME=$1

# Step 1: Delete the user's access keys
echo "Deleting access keys for user $USERNAME..."
ACCESS_KEYS=$(aws iam list-access-keys --user-name $USERNAME --query 'AccessKeyMetadata[*].AccessKeyId' --output text)
for KEY in $ACCESS_KEYS; do
  echo "Deleting access key: $KEY"
  aws iam delete-access-key --user-name $USERNAME --access-key-id $KEY
done

# Step 2: Detach managed policies attached to the user
echo "Detaching managed policies for user $USERNAME..."
POLICIES=$(aws iam list-attached-user-policies --user-name $USERNAME --query 'AttachedPolicies[*].PolicyArn' --output text)
for POLICY in $POLICIES; do
  echo "Detaching policy: $POLICY"
  aws iam detach-user-policy --user-name $USERNAME --policy-arn $POLICY
done

# Step 3: Delete inline policies attached to the user
echo "Deleting inline policies for user $USERNAME..."
INLINE_POLICIES=$(aws iam list-user-policies --user-name $USERNAME --query 'PolicyNames' --output text)
for POLICY in $INLINE_POLICIES; do
  echo "Deleting inline policy: $POLICY"
  aws iam delete-user-policy --user-name $USERNAME --policy-name $POLICY
done

# Step 4: Remove user from groups
echo "Removing user $USERNAME from groups..."
GROUPS=$(aws iam list-groups-for-user --user-name $USERNAME --query 'Groups[*].GroupName' --output text)
for GROUP in $GROUPS; do
  echo "Removing user from group: $GROUP"
  aws iam remove-user-from-group --user-name $USERNAME --group-name $GROUP
done

# Step 5: Delete the user's login profile (password for console access)
echo "Deleting login profile for user $USERNAME..."
aws iam delete-login-profile --user-name $USERNAME 2>/dev/null || echo "No login profile found for user $USERNAME."

# Step 6: Delete SSH keys
echo "Deleting SSH keys for user $USERNAME..."
SSH_KEYS=$(aws iam list-ssh-public-keys --user-name $USERNAME --query 'SSHPublicKeys[*].SSHPublicKeyId' --output text)
for SSH_KEY in $SSH_KEYS; do
  echo "Deleting SSH key: $SSH_KEY"
  aws iam delete-ssh-public-key --user-name $USERNAME --ssh-public-key-id $SSH_KEY
done

# Step 7: Finally, delete the IAM user
echo "Deleting IAM user $USERNAME..."
aws iam delete-user --user-name $USERNAME

echo "User $USERNAME and associated resources have been deleted successfully."

