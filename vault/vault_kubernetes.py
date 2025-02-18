#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import pathlib
import sys

from vault import VaultClient, VaultException, KubernetesCredentials


CMD_GEN = "gen"

DEFAULT_KUBERNETES_MOUNT_PATH = "kubernetes"
CREDENTIALS_FILENAME = os.path.expanduser("~/.vault-kubernetes-{role}")


def parse_args() -> argparse.Namespace:
    conf_parser = argparse.ArgumentParser(
        description=__doc__,  # printed with -h/--help
        # Don't mess with format of description
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # Turn off help, so we print all options in response to -h
        add_help=False,
    )

    parser = argparse.ArgumentParser(parents=[conf_parser])

    parser.add_argument("-m", "--mount-template", default="kubernetes/{cluster}")
    parser.add_argument("-c", "--cluster", required=True)
    parser.add_argument("-a", "--vault-address",
                        help="The address to reach vault. If not specified, uses VAULT_ADDR env var.")
    parser.add_argument("-t", "--vault-token",
                        help="The token to use. If not specified, uses VAULT_TOKEN env var or ~/.vault-token file.")

    subparsers = parser.add_subparsers(dest="cmd")
    generate_credentials_parser = subparsers.add_parser(CMD_GEN)
    generate_credentials_parser.add_argument("-r", "--role-name", required=True, help="Specifies a name of a role to generate credentials for")
    generate_credentials_parser.add_argument("-t", "--ttl", default="3600s", help="Specify how long the credentials should be valid for")
    generate_credentials_parser.add_argument("-n", "--namespace", help="Specify how long the credentials should be valid for")
    generate_credentials_parser.add_argument("-cr", "--cluster-role-binding", help="Specify how long the credentials should be valid for")

    read_role_parser = subparsers.add_parser('read')
    read_role_parser.add_argument("-r", "--role-name", required=True, help="Specifies a name of a role to read configuration from")
    read_role_parser.add_argument("-j", "--json-output", action="store_true", help="Prints json formatted output")

    subparsers.add_parser('list')

    return parser.parse_args()

def try_old(credentials_file: str):
    try:
        with open(credentials_file, 'r', encoding="utf-8") as file:
            data = json.load(file)
            return data
    except Exception as err:
        logging.error("Could not use existing credentials from file %s: %s", credentials_file, err)

    if "expiration" not in data:
        logging.error("No field 'expiration' found in file %s", credentials_file)
        return None

    expiration_date = datetime.datetime.utcfromtimestamp(data["expiration"])
    if expiration_date > datetime.datetime.now():
        logging.warning("Credentials expired: %s", expiration_date)
        return None

    return data

def update_credentials_file(credentials_file: str, creds: KubernetesCredentials) -> None:
    with open(credentials_file, 'w', encoding='utf-8') as f:
        json.dump(creds.to_dict(), f, ensure_ascii=False, indent=4)

def run(client: VaultClient, args: argparse.Namespace) -> None:
    match args.cmd:
        case "list":
            roles = client.kubernetes_list_roles()
            print(roles)
        case "gen":
            credentials_file = pathlib.Path(CREDENTIALS_FILENAME.format(role=args.role_name))
            credentials = None
            if credentials_file.exists():
                logging.info("Trying existing credentials...")
                existing_creds = try_old(credentials_file)
                if existing_creds:
                    credentials = KubernetesCredentials(**existing_creds)

            if not credentials:
                logging.info("Requesting new credentials")
                credentials = client.kubernetes_create_credentials(args.role_name, args.namespace, args.cluster_role_binding, args.ttl)
                update_credentials_file(credentials_file, credentials)
            print(credentials)

        case _:
            print("No cmd given")
            sys.exit(1)

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    args = parse_args()
    mount_path = args.mount_template.format(cluster=args.cluster)
    print(mount_path)
    client = VaultClient(addr=args.vault_address, token=args.vault_token, kubernetes_mount_path=mount_path)
    try:
        run(client, args)
    except VaultException as err:
        logging.error("No valid vault auth: %s", err)
        sys.exit(1)



if __name__ == "__main__":
    main()
