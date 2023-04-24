#!/usr/bin/env python3

import argparse
import logging
import subprocess
import sys

from abc import ABC, abstractmethod
from pathlib import Path

from vault import VaultClient, VaultException

# optional imports
try:
    import segno
except ImportError:
    segno = None


class AbstractTotpSecretOutput(ABC):
    @abstractmethod
    def consume(self, totp_secret: str):
        pass


class TotpQrOutput(AbstractTotpSecretOutput):
    def consume(self, totp_secret: str) -> None:
        qrcode = segno.make(totp_secret)
        qrcode.terminal()


class TotpStdOutput(AbstractTotpSecretOutput):
    def consume(self, totp_secret: str) -> None:
        print(totp_secret)


class TotpFileOutput(AbstractTotpSecretOutput):
    def __init__(self, path: Path):
        self._path = path

    def consume(self, totp_secret: str) -> None:
        logging.info("Writing totp secret to file '{self._path}'")
        Utils.write_text_to_file(totp_secret, self._path)


class PassPasswordManagerConsumer(AbstractTotpSecretOutput):
    def __init__(self, secret_path: str):
        self._secret_path = secret_path

    def consume(self, totp_secret: str) -> None:
        cmd = ['pass', 'insert', '--multiline', self._secret_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, error = proc.communicate(input=totp_secret.encode())
        if error:
            raise Exception(f"Error inserting string into pass: {error.decode()}")


def build_output(args: argparse.Namespace) -> AbstractTotpSecretOutput:
    if args.output_std:
        return TotpStdOutput()
    if args.output_qr:
        if not segno:
            raise ValueError("Could not import package 'segno'. Please install 'segno' or chose another output.")

        return TotpQrOutput()
    if args.output_file:
        return TotpFileOutput(args.output_file)


def run(vault_client: VaultClient, args: argparse.Namespace) -> None:
    output = build_output(args)
    method_id = args.method_id
    if not method_id:
        method_id = vault_client.totp_autodetect_method_id()
        logging.info(f"Auto-detected TOTP method_id '{method_id}'")

    if args.entity_id or args.entity_name:
        if args.entity_name:
            entity_id = vault_client.identity_entity_autodetect_id(args.entity_name)
            logging.info(f"Auto-detected entity_id '{entity_id}' for identity named '{args.entity_name}'")
        else:
            entity_id = args.entity_id

        otp_url = vault_client.totp_generate_secret_admin(method_id=method_id, entity_id=entity_id, force=args.force)
    else:
        logging.info("No entity_id provided, using entity tied to VAULT_TOKEN")
        otp_url = vault_client.totp_generate_secret(method_id=method_id)

    if not otp_url:
        return

    logging.info("Successfully created new TOTP secret")
    output.consume(otp_url)
    if args.pass_path:
        pass_output = PassPasswordManagerConsumer(args.pass_path)
        pass_output.consume(otp_url)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    try:
        args = ParsingUtils.parse_args()
    except ValueError as err:
        logging.error("Could not parse arguments: %s", err)
        sys.exit(1)

    if args.quiet:
        logging.disable(logging.WARNING)

    try:
        vault_client = VaultClient(addr=args.vault_address, token=args.vault_token)
        run(vault_client, args)
    except ValueError as err:
        logging.error("Value error: %s", err)
        sys.exit(1)
    except VaultException as err:
        logging.error("Vault returned status_code %d for url %s: %s", err.status_code, err.url, err.text)
        sys.exit(1)
    except ConnectionError as err:
        logging.error("Could not talk to vault")
        sys.exit(1)


class Utils:
    @staticmethod
    def write_text_to_file(text: str, file_path: str) -> None:
        Path(file_path).expanduser().write_text(text)

    @staticmethod
    def read_from_file(file_path: str) -> str:
        p = Path(file_path).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").rstrip("\n")
        raise ValueError(f"Can not read from non-existent file '{p.name}'")


class ParsingUtils:
    @staticmethod
    def parse_args() -> argparse.Namespace:
        conf_parser = argparse.ArgumentParser(
            description=__doc__,  # printed with -h/--help
            # Don't mess with format of description
            formatter_class=argparse.RawDescriptionHelpFormatter,
            # Turn off help, so we print all options in response to -h
            add_help=False,
        )

        parser = argparse.ArgumentParser(parents=[conf_parser])
        parser.add_argument("-q", "--quiet", action="store_true", default=False)

        entity_group = parser.add_mutually_exclusive_group(required=True)
        entity_group.add_argument("-e", "--entity-id")
        entity_group.add_argument("-n", "--entity-name")

        parser.add_argument("-m", "--method-id")
        parser.add_argument("-f", "--force", help="Delete and create new secret if existing", action="store_true",
                            default=False)

        parser.add_argument("-p", "--pass-path", help="Also write TOTP to 'pass' password manager under the given path")

        output_group = parser.add_mutually_exclusive_group(required=False)
        output_group.add_argument('--output-qr', action="store_true", default=True)
        output_group.add_argument('--output-std', action="store_true")
        output_group.add_argument('--output-file', action="store", type=str, help="Write TOTP secret to file")

        parser.add_argument("-a", "--vault-address",
                            help="The address to reach vault. If not specified, uses VAULT_ADDR env var.")
        parser.add_argument("-t", "--vault-token",
                            help="The token to use. If not specified, uses VAULT_TOKEN env var or ~/.vault-token file.")

        return parser.parse_args()


if __name__ == "__main__":
    main()
