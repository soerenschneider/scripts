#!/usr/bin/env bash

VAULT_DB_MOUNT=dbs
VAULT_ROLE=mysql_reader

MARIADB_HOST=dbs.ha.soeren.cloud
MARIADB_PORT=3307
MARIADB_SSL_ARGS="--ssl"

function parse_args() {
    while [[ $# -gt 0 ]]; do
      case $1 in
        -m|--mount)
          VAULT_DB_MOUNT="$2"
          shift # past argument
          shift # past value
          ;;
        -r|--role)
          VAULT_ROLE="$2"
          shift # past argument
          shift # past value
          ;;
        -h|--host)
          MARIADB_HOST="$2"
          shift # past argument
          shift # past value
          ;;
        -p|--port)
          MARIADB_PORT="$2"
          shift # past argument
          shift # past value
          ;;
        --no-ssl)
          MARIADB_SSL_ARGS=""
          shift # past argument
          ;;
        -*|--*)
          echo "Unknown option $1"
          exit 1
          ;;
        *)
          POSITIONAL_ARGS+=("$1") # save positional arg
          shift # past argument
          ;;
      esac
    done
}


set -e

parse_args "$@"

RESP=$(vault read --format=json "${VAULT_DB_MOUNT}/creds/${VAULT_ROLE}")

USER=$(echo "${RESP}" | jq -r '.data.username')
PASS=$(echo "${RESP}" | jq -r '.data.password')

echo -e "[client]user=${USER}\npassword=${PASS}" | mysql --defaults-file=/dev/stdin -h "${MARIADB_HOST}" -P "${MARIADB_PORT}" "${MARIADB_SSL_ARGS}"

