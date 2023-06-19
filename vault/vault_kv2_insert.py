#!/usr/bin/env python3

import argparse
import getpass
import re
import subprocess
import sys


def parse_args() -> None:
    args = argparse.ArgumentParser(description="vault - feed me secrets")
    args.add_argument("--mount", "-m", type=str, default="/secret")
    args.add_argument("--secret-key", "-k", type=str, default="value")
    args.add_argument("--vault-path", "-v", type=str, required=True)
    args.add_argument("--pass-path", "-p", type=str, required=False)

    parsed = args.parse_args()

    if not parsed.mount or parsed.mount.endswith("/"):
        raise ValueError("mount must not be null nor end with an '/'")

    if not parsed.secret_key:
        raise ValueError("secret-key must not be null")

    return parsed


def confirm(msg: str = None) -> bool:
    if msg:
        print(msg)

    while True:
        user_input = input("Do you want to proceed (y/n): ").strip().lower()
        if user_input in ['y', 'yes']:
            return True
        if user_input in ['n', 'no']:
            return False
        print("Invalid input. Please enter 'y' or 'n'.")


def read_password() -> str:
    try:
        return getpass.getpass("Enter password: ")
    except KeyboardInterrupt:
        sys.exit(0)


def get_password_from_pass(path: str) -> str:
    command = ["pass", "show", path]
    try:
        return subprocess.check_output(command, text=True).strip().splitlines()[0]
    except subprocess.CalledProcessError as err:
        print(f"Error getting password from pass: {err}")
        sys.exit(1)


def is_full_path(path: str) -> bool:
    return re.search(r"^\/?[^\/]+\/data\/\w+", path) is not None


def get_full_path(path: str, mount: str) -> str:
    sep = "" if path.startswith("/") else "/"
    if "/data/" in path:
        return f"{mount}{sep}{path}"

    return f"{mount}/data{sep}{path}"


def vault_kv_put(path: str, key: str, secret: str) -> None:
    command = ["vault", "kv", "put", path, f"{key}={secret}"]

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        print(f"Error running vault kv put {path}")
        sys.exit(1)


def main():
    try:
        args = parse_args()
    except ValueError as err:
        print(err)
        sys.exit(1)

    path = args.vault_path
    if not is_full_path(path):
        path = get_full_path(path, args.mount)
        print(f"Automatically updated path to {path}")

    if args.pass_path:
        password = get_password_from_pass(args.pass_path)
    else:
        password = read_password()

    if not confirm(f"Trying to write {args.secret_key}=<redacted> to '{path}'"):
        sys.exit(0)

    vault_kv_put(path, args.secret_key, password)


if __name__ == "__main__":
    main()
