#!/usr/bin/env python3

import argparse
import functools
import logging
import os
import sys
import urllib.parse

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# optional imports
try:
    import segno
except ImportError:
    segno = None

TOKEN_HEADER = "X-VAULT-TOKEN"
BACKOFF_ATTEMPTS = 12


class VaultException(Exception):
    def __init__(self, status_code: int, url: str = None, text: str = None):
        self.status_code = status_code
        self.url = url
        self.text = text


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


class VaultClient:
    def __init__(self, addr: str = None,
                 token: str = None,
                 backoff_attempts: int = BACKOFF_ATTEMPTS):
        if addr:
            self._vault_address = addr
        else:
            self._vault_address = os.getenv("VAULT_ADDR")
            if not self._vault_address:
                raise ValueError("No 'VAULT_ADDR' defined")

        if token:
            self._vault_token = token
        else:
            self._load_vault_token()

        self._http_pool = requests.Session()
        # set timeout globally
        self._http_pool.request = functools.partial(self._http_pool.request, timeout=10)
        if backoff_attempts:
            retries = Retry(total=backoff_attempts, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
            self._http_pool.mount("http://", HTTPAdapter(max_retries=retries))
            self._http_pool.mount("https://", HTTPAdapter(max_retries=retries))

    def _load_vault_token(self):
        self._vault_token = os.getenv("VAULT_TOKEN")
        vault_token_file = Path.home() / ".vault-token"
        if not self._vault_token:
            logging.info("Could not find 'VAULT_TOKEN', trying to read token from '%s'", vault_token_file)
            if vault_token_file.is_file():
                self._vault_token = vault_token_file.read_text(encoding="utf-8").rstrip("\n")

        if not self._vault_token:
            raise ValueError(f"Neither 'VAULT_TOKEN' defined nor '{vault_token_file}' existent")

    def list_totp_methods(self) -> List[str]:
        """ Returns all defined TOTP methods. """
        url = urllib.parse.urljoin(self._vault_address, "/v1/identity/mfa/method/totp?list=true")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]["keys"]

    def destroy_totp_secret_admin(self, method_id: str, entity_id: str) -> None:
        """ Destroys an existing TOTP secret for a given entity. """
        logging.info("Destroying existing TOTP secret...")
        url = urllib.parse.urljoin(self._vault_address, "/v1/identity/mfa/method/totp/admin-destroy")
        data = {
            "method_id": method_id,
            "entity_id": entity_id
        }
        resp = self._http_pool.post(url=url, data=data, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

    def generate_totp_secret_admin(self, method_id: str, entity_id: str, force: bool = False) -> Optional[str]:
        """ Generates new TOTP secret for a given entity. """
        url = urllib.parse.urljoin(self._vault_address, "/v1/identity/mfa/method/totp/admin-generate")
        data = {
            "method_id": method_id,
            "entity_id": entity_id
        }
        resp = self._http_pool.post(url=url, data=data, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        resp_json = resp.json()
        if not resp_json["data"] and len(resp_json["warnings"]) > 0:
            logging.info("Entity already has TOTP defined")
            if force:
                self.destroy_totp_secret_admin(method_id=method_id, entity_id=entity_id)
                # try again but set force=False, so we don't enter a loop
                return self.generate_totp_secret_admin(method_id=method_id, entity_id=entity_id, force=False)
            else:
                logging.warning("Not going to delete existing TOTP secret. Use '--force' to delete and re-generate")
                return

        return resp.json()["data"]["url"]

    def generate_totp_secret(self, method_id: str) -> Optional[str]:
        """ Generates new TOTP secret. """
        url = urllib.parse.urljoin(self._vault_address, "/v1/identity/mfa/method/totp/generate")
        data = {
            "method_id": method_id
        }
        resp = self._http_pool.post(url=url, data=data, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        resp_json = resp.json()
        if not resp_json["data"] and len(resp_json["warnings"]) > 0:
            logging.warning("Entity already has TOTP secret defined")
            logging.error("TOTP secret can only be destroyed using the admin endpoint, therefore an entity_id needs "
                          "to be supplied to this script")
            return

        return resp_json["data"]["url"]

    def autodetect_entity_id(self, name: str) -> Optional[str]:
        """ Returns all defined TOTP methods. """
        url = urllib.parse.urljoin(self._vault_address, f"/v1/identity/entity/name/{name}")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]["id"]

    def autodetect_method_id(self) -> Optional[str]:
        method_ids = self.list_totp_methods()
        if not method_ids:
            logging.error("No TOTP method_ids available. Please configure them first.")
            sys.exit(1)

        if len(method_ids) > 1:
            logging.error(f"Multiple TOTP method_ids found, don't know which one to pick: {method_ids}")
            sys.exit(1)

        return method_ids[0]


def build_output(args: argparse.Namespace) -> AbstractTotpSecretOutput:
    if args.output_std:
        return TotpStdOutput()
    if args.output_qr:
        if not segno:
            raise ValueError("Could not import package 'segno'. Please install 'segno' or chose another output.")

        return TotpQrOutput()
    if args.output_file:
        return TotpFileOutput(args.output_file)


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

        output = build_output(args)
        method_id = args.method_id
        if not method_id:
            method_id = vault_client.autodetect_method_id()
            logging.info(f"Auto-detected TOTP method_id '{method_id}'")

        if args.entity_id or args.entity_name:
            if args.entity_name:
                entity_id = vault_client.autodetect_entity_id(args.entity_name)
                logging.info(f"Auto-detected entity_id '{entity_id}' for identity named '{args.entity_name}'")
            else:
                entity_id = args.entity_id

            otp_url = vault_client.generate_totp_secret_admin(method_id=method_id, entity_id=entity_id, force=args.force)
        else:
            logging.info("No entity_id provided, using entity tied to VAULT_TOKEN")
            otp_url = vault_client.generate_totp_secret(method_id=method_id)

        if otp_url:
            logging.info("Successfully created new TOTP secret")
            output.consume(otp_url)

    except ValueError as err:
        logging.error("Value error: %s", err)
        sys.exit(1)
    except VaultException as err:
        logging.error("Vault returned status_code %d for url %s: %s", err.status_code, err.url, err.text)
        sys.exit(1)
    except requests.exceptions.ConnectionError as err:
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
