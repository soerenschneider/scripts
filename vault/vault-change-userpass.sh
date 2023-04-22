#!/usr/bin/env bash

# Disable command history
set +o history

usage() {
  echo "Usage: $0 [-u <username>]"
  echo "  -u, --user     Specify the username to update password. If not provided, the current username will be used."
}

# Parse command-line options
while [[ $# -gt 0 ]]; do
  case $1 in
    -u|--user)
      if [[ -n $2 ]]; then
        username=$2
        shift
      fi
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: Unknown option: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

# If username is not set, ask for it
if [[ -z $username ]]; then
  read -p "Enter your username: " username
fi

# Ask for a new password and confirmation
while true; do
  read -s -p "Enter a new password: " password
  echo
  read -s -p "Confirm password: " password_confirm
  echo
  if [[ "${password}" == "${password_confirm}" ]]; then
    break
  else
    echo "Passwords do not match. Please try again."
  fi
done

# Update the password in HashiCorp Vault
vault write "auth/userpass/users/${username}" password="${password}"

# Re-enable command history
set -o history

