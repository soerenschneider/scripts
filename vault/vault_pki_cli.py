#!/usr/bin/env python3

import argparse
import functools
import io
import json
import logging
import os
import sys
import shutil
import socket
import stat
import time
import urllib.parse
import uuid

from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Any

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

DEFAULT_MIN_VALIDITY_PERIOD_PERCENT = 34
TOKEN_HEADER = "X-VAULT-TOKEN"
BACKOFF_ATTEMPTS = 12


class CertRotationStrategy(ABC):
    @abstractmethod
    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        pass


class JsonOutput(ABC):
    @abstractmethod
    def communicate(self, success: bool, pairs: Dict) -> None:
        pass


class VaultException(Exception):
    def __init__(self, status_code: int, url: str = None, text: str = None):
        self.status_code = status_code
        self.url = url
        self.text = text


class VaultClient:
    def __init__(self, addr: str = None,
                 token: str = None,
                 approle_mount_path: str = "approle",
                 backoff_attempts: int = BACKOFF_ATTEMPTS):
        if addr:
            self._vault_address = addr
        else:
            self._vault_address = os.getenv("VAULT_ADDR")
            if not self._vault_address:
                raise ValueError("No 'VAULT_ADDR' defined")

        self._vault_token = token

        # define mount path for the AppRole auth
        if not approle_mount_path:
            raise ValueError(f"Illegal mount path: {approle_mount_path}")
        self._approle_mount_path = approle_mount_path

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

    def _get_vault_token(self) -> str:
        # lazy-load vault token as it's not required for all operations
        if not self._vault_token:
            self._load_vault_token()
        return self._vault_token

    def login(self, role_id: str, secret_id: str) -> str:
        """ Login using an Approle. Returns the client token after successful login. """
        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._approle_mount_path}/login")
        data = {"role_id": role_id, "secret_id": secret_id}
        resp = self._http_pool.post(url=url, data=data)
        if resp.ok:
            return resp.json()["auth"]["client_token"]
        raise VaultException(resp.status_code, url, resp.text)

    def unwrap(self, token: str) -> Dict[str, Any]:
        """ Unwraps a secret_id. """
        url = urllib.parse.urljoin(self._vault_address, "v1/sys/wrapping/unwrap")
        resp = self._http_pool.post(url=url, headers={TOKEN_HEADER: token})
        if resp.ok:
            return resp.json()["data"]
        raise VaultException(resp.status_code, url, resp.text)

    def issue(self, data: Dict[str, str], pki_path: str, role_name: str) -> Dict[str, str]:
        url = urllib.parse.urljoin(self._vault_address, f"v1/{pki_path}/issue/{role_name}")
        resp = self._http_pool.post(url=url, data=data, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]
        raise VaultException(resp.status_code, url, resp.text)


def run_cmd(vault_client: VaultClient, args: argparse.Namespace, json_output: JsonOutput) -> None:
    available_commands = {}
    for clazz in Command.__subclasses__():
        available_commands[clazz.cmd_id] = clazz

    if args.subparser_name not in available_commands:
        raise ValueError(f"No such cmd: {args.subparser_name}")

    cmd_class = available_commands[args.subparser_name]
    obj = cmd_class(vault_client, json_output)
    obj.run(args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    try:
        args = ParsingUtils.parse_args()
    except ValueError as err:
        logging.error("Could not parse arguments: %s", err)
        sys.exit(1)

    if args.quiet:
        logging.disable(logging.WARNING)

    output = DisabledOutput()
    if args.json_output:
        output = JsonStdOutput()

    try:
        ParsingUtils.validate_args(args)
        vault_client = VaultClient(addr=args.vault_address, token=args.vault_token, approle_mount_path=args.mount_path)
        run_cmd(vault_client, args, output)
    except ValueError as err:
        logging.error("Value error: %s", err)
        output.communicate(False, {"error": f"Missing value: {err}"})
        sys.exit(1)
    except VaultException as err:
        logging.error("Vault returned status_code %d for url %s: %s", err.status_code, err.url, err.text)
        output.communicate(False, {"status_code": err.status_code})
        sys.exit(1)
    except requests.exceptions.ConnectionError as err:
        logging.error("Could not talk to vault")
        output.communicate(False, {"error": f"could not communicate with vault: {err}"})
        sys.exit(1)


class Command(ABC):
    def __init__(self, vault_client: VaultClient, output: JsonOutput):
        self.vault_client = vault_client
        self.output = output

    @abstractmethod
    def run(self, args: argparse.Namespace) -> None:
        pass


class CommandIssue(Command):
    cmd_id = "issue"

    def run(self, args: argparse.Namespace) -> None:
        data = {
            "common_name": args.common_name,
            "ttl": args.ttl
        }

        resp = self.vault_client.issue(data, pki_path=args.mount_path, role_name=args.pki_role)
        print(resp)




class CommandUnwrapSecretId(Command):
    cmd_id = "unwrap-secret-id"

    def run(self, args: argparse.Namespace) -> None:
        token = ParsingUtils.get_token(args)
        if not token:
            raise ValueError("Could not find token")

        ret = {
            "vault_response": self.vault_client.unwrap(token)
        }
        Utils.process_new_secret_id(ret, args)
        self.output.communicate(True, ret)


class CommandLoginApprole(Command):
    cmd_id = "login"

    def run(self, args: argparse.Namespace) -> None:
        secret_id = ParsingUtils.get_secret_id(args)
        logging.info("Trying to login to Approle...")
        token = self.vault_client.login(args.role_id, secret_id)
        ret = {}
        if args.token_file:
            logging.info("Writing token to file '%s'", args.token_file)
            Utils.write_text_to_file(token, args.token_file)
        else:
            logging.warning("Printing token to stdout, regard writing it to a file")
            logging.info("Login token is %s", token)
            ret["token"] = token
        self.output.communicate(True, ret)


class Utils:
    @staticmethod
    def parse_timestamp(timestamp: str) -> Optional[datetime]:
        try:
            import iso8601
            return iso8601.parse_date(timestamp)
        except ImportError:
            logging.error("Could not import package 'iso8601', please consider installing it")
        except Exception:
            pass

        # here be dragons
        try:
            return datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S.%f%z')
        except ValueError:
            logging.error("Could not parse the timestamp '%s' using using strptime, please install 'iso8601'", timestamp)

        raise ValueError(f"Could not parse timestamp '{timestamp}'")

    @staticmethod
    def lookup_host(hostname: str) -> List[str]:
        try:
            return [f"{socket.gethostbyname(hostname)}/32"]
        except socket.gaierror:
            return []

    @staticmethod
    def extract_value_from_json_file(json_path: str, json_file: str) -> Optional[Any]:
        with open(json_file, encoding="utf-8") as content:
            data = json.load(content)

            value = data
            for k in json_path.lstrip(".").split("."):
                value = value[k]

            return value

    @staticmethod
    def write_text_to_file(text: str, file_path: str) -> None:
        Path(file_path).expanduser().write_text(text)

    @staticmethod
    def upsert_json_file(value: str, json_file: str, json_path: str = ".secret_id") -> None:
        valid_json = False
        if Path(json_file).expanduser().exists():
            try:
                json.loads(json_file)
                valid_json = True
            except json.decoder.JSONDecodeError:
                pass

        content = {}
        if valid_json:
            content = json.loads(json_file)
        path = json_path.lstrip(".").split(".")
        if len(path) != 1:
            # TODO: Implement
            raise ValueError(f"Only supporting flat json_paths for now (len == 1). You supplied: {path}")
        content[path[0]] = value
        Path(json_file).expanduser().write_text(json.dumps(content))

    @staticmethod
    def is_secure_file(file_path: str) -> bool:
        st = os.stat(Path(file_path).expanduser())
        return not bool(st.st_mode & stat.S_IRGRP) and not bool(st.st_mode & stat.S_IROTH)

    @staticmethod
    def read_from_file(file_path: str) -> str:
        p = Path(file_path).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").rstrip("\n")
        raise ValueError(f"Can not read from non-existent file '{p.name}'")


class ParsingUtils:
    @staticmethod
    def get_token(args: argparse.Namespace) -> Optional[str]:
        if args.token:
            logging.info("Using token via args")
            return args.token

        if args.token_file:
            logging.info("Trying to read token from file %s", args.token_file)
            return Utils.read_from_file(args.token_file)

        logging.info("Trying env var VAULT_TOKEN for token")
        return os.getenv("VAULT_TOKEN")

    @staticmethod
    def get_role_id(args: argparse.Namespace) -> str:
        """ Try to extract role_id from whereever the user wants to retrieve it from. """
        if args.role_id_json_file:
            if not Utils.is_secure_file(args.role_id_json_file):
                logging.warning("Permissions of file '%s' too liberal, consider setting more restrictive permissions",
                                args.role_id_json_file)
            role_id = Utils.extract_value_from_json_file(args.role_id_json_path, args.role_id_json_file)
            logging.info("Read role_id '%s' from JSON file '%s'", role_id, args.role_id_json_file)
        else:
            role_id = args.role_id

        return role_id

    @staticmethod
    def get_secret_id(args: argparse.Namespace) -> Optional[str]:
        """ Try to extract secret_id from whereever the user wants to retrieve it from. """
        if args.secret_id_file:
            if not Utils.is_secure_file(args.secret_id_file):
                raise ValueError(f"Permissions of file {args.secret_id_file} too liberal, not continuing")
            secret_id = Utils.read_from_file(args.secret_id_file)
            logging.info("Read secret_id from file '%s'", args.secret_id_file)
        elif args.secret_id_json_file:
            if not Utils.is_secure_file(args.secret_id_json_file):
                raise ValueError(f"Permissions of file {args.secret_id_json_file} too liberal, not continuing")
            secret_id = Utils.extract_value_from_json_file(args.secret_id_json_path, args.secret_id_json_file)
            logging.info("Read secret_id from JSON file '%s'", args.secret_id_json_file)
        else:
            try:
                secret_id = args.secret_id
            except AttributeError:
                secret_id = None

        return secret_id

    @staticmethod
    def parse_args() -> argparse.Namespace:
        conf_parser = argparse.ArgumentParser(
            description=__doc__,  # printed with -h/--help
            # Don't mess with format of description
            formatter_class=argparse.RawDescriptionHelpFormatter,
            # Turn off help, so we print all options in response to -h
            add_help=False,
        )
        conf_parser.add_argument("-c", "--config", help="Specify config file", metavar="FILE")
        group_args, remaining_argv = conf_parser.parse_known_args()
        config_values = {}

        if group_args.config:
            try:
                config_file = Path(group_args.config).expanduser()
                with open(config_file, encoding="utf-8") as cf:
                    config = json.load(cf)
                    config_values.update(config)
            except json.decoder.JSONDecodeError as err:
                logging.error("Config file is not well formatted: %s", err)
                sys.exit(1)

        parser = argparse.ArgumentParser(parents=[conf_parser])
        parser.add_argument("-j", "--json-output", action="store_true", default=False)
        parser.add_argument("-q", "--quiet", action="store_true", default=False)
        parser.add_argument("-a", "--vault-address",
                            help="The address to reach vault. If not specified, uses VAULT_ADDR env var.")
        parser.add_argument("-t", "--vault-token",
                            help="The token to use. If not specified, uses VAULT_TOKEN env var or ~/.vault-token file.")
        parser.add_argument("-m", "--mount-path", default="pki_intermediate")

        command_subparsers = parser.add_subparsers(help="sub-command help", dest="subparser_name")

        #############################################################################################
        # login
        #############################################################################################
        login = command_subparsers.add_parser(CommandLoginApprole.cmd_id, help="Login to an approle")
        login.add_argument("--secret-id-json-path", default=".secret_id")
        login.add_argument("-r", "--role-id", required=True, help="role_id of the Approle to login.")
        login.add_argument("--token-file", help="Write acquired token to this file.")
        group = login.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--secret-id", help="secret_id to use.")
        group.add_argument("-si", "--secret-id-file", help="Read secret_id from this file.")
        group.add_argument("-sj", "--secret-id-json-file", help="Read secret_id from this JSON-encoded file.")

        #############################################################################################
        # unwrap-secret-id
        #############################################################################################
        unwrap_secret_id = command_subparsers.add_parser(CommandUnwrapSecretId.cmd_id, help="Unwrap a secret_id from a token")
        group = unwrap_secret_id.add_mutually_exclusive_group(required=False)
        group.add_argument("-t", "--token")
        group.add_argument("-tf", "--token-file")

        group = unwrap_secret_id.add_mutually_exclusive_group(required=False)
        group.add_argument("-sj", "--secret-id-json-file")
        group.add_argument("-sf", "--secret-id-file")

        #############################################################################################
        # issue a new certificate
        #############################################################################################
        issue = command_subparsers.add_parser(CommandIssue.cmd_id, help="Issue a certificate")
        issue.add_argument("--role-id-json-path", default=".role_id")
        issue.add_argument("--secret-id-json-path", default=".secret_id")
        issue.add_argument("--ttl", type=int, default="86400", help="Validity period of the cert")
        issue.add_argument("-n", "--common-name", required=True, help="The common name of the cert")
        issue.add_argument("-p", "--pki-role", required=True, help="The pki role name")

        group = issue.add_mutually_exclusive_group(required=False)
        group.add_argument("-r", "--role-id", help="The role id to add the secret_id to")
        group.add_argument("-rj", "--role-id-json-file", help="The role id to add the secret_id to")
        group.set_defaults(**config_values)

        group = issue.add_mutually_exclusive_group(required=False)
        group.add_argument("-sf", "--secret-id-file", help="Flat file that contains the AppRole's secret_id",)
        group.add_argument("-sj", "--secret-id-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)


        parser.set_defaults(**config_values)

        if len(sys.argv) == 1:
            parser.print_help(sys.stderr)
            sys.exit(1)

        return parser.parse_args(remaining_argv)

    @staticmethod
    def _is_supplied_by_config(group: argparse._MutuallyExclusiveGroup, conf: Dict[str, Any]) -> bool:
        """Hacky way to check if all arguments have been provided by a config file for a mutually exclusive group."""
        group_args = []
        for arg in group._group_actions:
            group_args.append(arg.dest)

        count = 0
        for val in group_args:
            if val in conf:
                count += 1
        return count == len(group_args) or count == 0

    @staticmethod
    def validate_args(args: argparse.Namespace) -> None:
        if args.quiet and not args.json_output and not args.secret_id_file:
            raise ValueError("Can not use quiet=true, json=false and --secret_id")


class JsonStdOutput(JsonOutput):
    def communicate(self, success: bool, pairs: Dict) -> None:
        if not pairs:
            return
        pairs["success"] = success
        print(json.dumps(pairs, default=str))


class DisabledOutput(JsonOutput):
    def communicate(self, success: bool, pairs: Dict) -> None:
        pass


class PrometheusWrapperOutput:
    _metric_prefix = "vault_approle_rotation"

    def __init__(self, metric_file: Path, role_name: str, wrapper: JsonOutput = None):
        if isinstance(metric_file, str):
            metric_file = Path(metric_file)
        self.metric_file = metric_file
        if not role_name:
            raise ValueError("no role_name provided")
        self.role_name = role_name

        if not wrapper:
            wrapper = DisabledOutput()
        self.wrapper = wrapper

    def communicate(self, success: bool, pairs: Dict) -> None:
        try:
            pairs["success"] = success
            buffer = self._collect(pairs)
            logging.info("Writing metrics to file %s", self.metric_file)
            self.write_metrics(buffer)
        except OSError as err:
            logging.error("Could not write metrics: %s", err)

        self.wrapper.communicate(success, pairs)

    def write_metrics(self, metrics_data: io.StringIO) -> None:
        tmp_file = f"{self.metric_file}.{os.getpid()}"
        try:
            with open(tmp_file, mode="w", encoding="utf-8") as fd:
                print(metrics_data.getvalue(), file=fd)
            shutil.move(tmp_file, self.metric_file)
        finally:
            metrics_data.close()

    def _collect(self, pairs: Dict[str, Any]) -> io.StringIO:
        buffer = io.StringIO()
        for key in pairs:
            val = pairs[key]
            if isinstance(val, str):
                continue
            if isinstance(val, bool):
                val = 1 if val else 0
                buffer.write(f"# HELP {self._metric_prefix}_{key}_bool Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_bool gauge\n")
                buffer.write(f'{self._metric_prefix}_{key}_bool{{role_name="{self.role_name}"}} {val}\n')
            elif isinstance(val, datetime):
                val = val.timestamp()
                buffer.write(f"# HELP {self._metric_prefix}_{key}_timestamp_seconds Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_timestamp_seconds gauge\n")
                buffer.write(f'{self._metric_prefix}_{key}_timestamp_seconds{{role_name="{self.role_name}"}} {val}\n')
            elif isinstance(val, dict) and "secret_id_ttl" in val:
                val = val["secret_id_ttl"]
                buffer.write(f"# HELP {self._metric_prefix}_{key}_timestamp_seconds Time until the secret_id expires in seconds\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_timestamp_seconds gauge\n")
                buffer.write(f'{self._metric_prefix}_secret_id_ttl_timestamp_seconds{{role_name="{self.role_name}"}} {val}\n')
            else:
                buffer.write(f"# HELP {self._metric_prefix}_{key}_total Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_total gauge\n")
                buffer.write(f'{self._metric_prefix}_{key}_total{{role_name="{self.role_name}"}} {val}\n')

        buffer.write(f"# HELP {self._metric_prefix}_invocation_timestamp_seconds timestamp \n")
        buffer.write(f"# TYPE {self._metric_prefix}_invocation_timestamp_seconds gauge\n")
        buffer.write(f'{self._metric_prefix}_invocation_timestamp_seconds {{role_name="{self.role_name}"}} {time.time()}\n')

        return buffer


class StaticRotationStrategy(CertRotationStrategy):
    """Always rotate."""
    def __init__(self, rotate: bool = True):
        self._rotate = rotate

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        return self._rotate


class ValidityPeriodRotationStrategy(CertRotationStrategy):
    """Rotate when reached x percent of validity period left."""

    def __init__(self, min_validity_period: int):
        if min_validity_period < 10 or min_validity_period > 90:
            raise ValueError(f"min_lifetime_percentage should be [10, 90] but is: {min_validity_period}")
        self.min_validity_period = min_validity_period

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        try:
            lifetime_seconds = (expiration_time - creation_time).total_seconds()
            seconds_until_expiration = (expiration_time - datetime.now(timezone.utc)).total_seconds()
            if seconds_until_expiration <= 0:
                return True

            validity_period = seconds_until_expiration * 100. / lifetime_seconds
            logging.info("secret_id validity period at %f%%, valid from %s until %s", validity_period, creation_time, expiration_time)
            return validity_period <= self.min_validity_period
        except TypeError as err:
            logging.error("Can not compute remaining validity period of secret_id: %s", err)
            return True


if __name__ == "__main__":
    main()
