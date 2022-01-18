#!/usr/bin/env python3

import argparse
import io
import json
import logging
import os
import sys
import shutil
import socket
import stat
import urllib.parse
import uuid

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Any

import iso8601
import requests

CMD_ADD_SECRET_ID = "add-secret-id"
CMD_LIST_SECRET_ID_ACCESSORS = "list-secret-id-accessors"
CMD_DESTROY_ALL_SECRET_ACCESSOR_ID = "destroy-all-secret-ids"
CMD_DESTROY_SECRET_ACCESSOR_ID = "destroy-secret-id"
CMD_DELETE_ROLE = "delete-role"
CMD_GET_ROLE_ID = "get-role-id"
CMD_LIST_ROLES = "list-roles"
CMD_LOGIN_APPROLE = "login"
CMD_LOOKUP_SECRET_ID = "lookup-secret-id"
CMD_ROTATE_SECRET_ID = "rotate-secret-id"

TOKEN_HEADER = "X-VAULT-TOKEN"


class JsonOutput(ABC):
    @abstractmethod
    def communicate(self, success: bool, pairs: Dict) -> None:
        pass


class JsonStdOutput:
    def communicate(self, success: bool, pairs: Dict) -> None:
        if not pairs:
            return
        pairs["success"] = success
        print(json.dumps(pairs, default=str))


class JsonDisabledOutput:
    def communicate(self, success: bool, paris: Dict) -> None:
        pass


class PrometheusWrapperOutput:
    _metric_prefix = "vault_approle_rotation"

    def __init__(self, metric_file: Path, wrapper: JsonOutput = None):
        if isinstance(metric_file, str):
            metric_file = Path(metric_file)
        self.metric_file = metric_file
        if not wrapper:
            wrapper = JsonDisabledOutput()
        self.wrapper = wrapper

    def communicate(self, success: bool, pairs: Dict = {}) -> None:
        try:
            buffer = self._collect(pairs)
            logging.info("Writing metrics to file %s", self.metric_file)
            self.write_metrics(buffer)
        except Exception as err:
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
            elif isinstance(val, bool):
                val = 1 if val else 0
                buffer.write(f"# HELP {self._metric_prefix}_{key}_bool Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_bool gauge\n")
                buffer.write(f"{self._metric_prefix}_{key}_bool {val}\n")
            elif isinstance(val, datetime):
                val = val.timestamp()
                buffer.write(f"# HELP {self._metric_prefix}_{key}_timestamp_seconds Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_timestamp_seconds gauge\n")
                buffer.write(f"{self._metric_prefix}_{key}_timestamp_seconds {val}\n")
            elif isinstance(val, dict) and "secret_id_ttl" in val:
                val = val["secret_id_ttl"]
                buffer.write(f"# HELP {self._metric_prefix}_{key}_timestamp_seconds Time until the secret_id expires in seconds\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_timestamp_seconds gauge\n")
                buffer.write(f"{self._metric_prefix}_{'secret_id_ttl'}_timestamp_seconds {val}\n")
            else:
                buffer.write(f"# HELP {self._metric_prefix}_{key}_total Auto-generated, sorry\n")
                buffer.write(f"# TYPE {self._metric_prefix}_{key}_total gauge\n")
                buffer.write(f"{self._metric_prefix}_{key}_total {val}\n")
        return buffer


class SecretIdRotationStrategy(ABC):
    @abstractmethod
    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        pass


class DefaultRotationStrategy(SecretIdRotationStrategy):
    """Always rotate."""

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        return True


class MinLifetimeRotationPolicy(SecretIdRotationStrategy):
    """Rotate after passing x seconds until expiration time given there is an expiration time. Rotate if there's no
    expiration time."""

    def __init__(self, min_lifetime_seconds: int):
        self.min_lifetime_left_seconds = min_lifetime_seconds

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        if not expiration_time:
            return True

        diff = expiration_time - datetime.now(timezone.utc)
        return self.min_lifetime_left_seconds > diff.total_seconds()


class MaxAgeRotationStrategy(SecretIdRotationStrategy):
    """Rotate after x seconds have passed since creation time."""

    def __init__(self, max_age_seconds: int):
        self.max_age_seconds = max_age_seconds

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        if not expiration_time:
            return True

        diff = datetime.now(timezone.utc) - creation_time
        return diff.total_seconds() > self.max_age_seconds


class MaxPercentageRotationStrategy(SecretIdRotationStrategy):
    """Rotate after reaching x percent of the total lifetime if an expiration time is defined, otherwise rotate."""

    def __init__(self, max_lifetime_percentage: int):
        self.max_lifetime_percentage = max_lifetime_percentage

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        if not expiration_time:
            return True

        lifetime_seconds = (expiration_time - creation_time).total_seconds()
        passed_seconds = (datetime.now(timezone.utc) - creation_time).total_seconds()
        passed_percent = passed_seconds * 100 / lifetime_seconds
        return passed_percent >= self.max_lifetime_percentage


class VaultException(Exception):
    def __init__(self, status_code: int, url: str = None, text: str = None):
        self.status_code = status_code
        self.url = url
        self.text = text


class VaultClient:
    def __init__(self, mount_path: str = "approle", rotation_strategy: SecretIdRotationStrategy = None):
        self._vault_address = os.getenv("VAULT_ADDR")
        if not self._vault_address:
            raise ValueError("No 'VAULT_ADDR' defined")

        # define mount path for the appengine
        if not mount_path:
            raise ValueError(f"Illegal mount path: {mount_path}")
        self._mount_path = mount_path

        self._vault_token = None

        if not rotation_strategy:
            rotation_strategy = DefaultRotationStrategy()
        self.rotation_strategy = rotation_strategy

    def _load_vault_token(self):
        self._vault_token = os.getenv("VAULT_TOKEN")

        if not self._vault_token:
            vault_token_file = Path.home() / ".vault-token"
            logging.info("Could not find 'VAULT_TOKEN', trying to read token from '%s'", vault_token_file)
            if vault_token_file.is_file():
                self._vault_token = vault_token_file.read_text(encoding="utf-8").rstrip("\n")

        if not self._vault_token:
            raise ValueError(
                f"Neither 'VAULT_TOKEN' defined nor '{vault_token_file}' existent"
            )

    def _get_vault_token(self) -> str:
        # lazy-load vault token as it's not required for all operations
        if not self._vault_token:
            self._load_vault_token()
        return self._vault_token

    def get_secret_id_accessors(self, role_name: str) -> List[str]:
        url = urllib.parse.urljoin(
            self._vault_address,
            f"v1/auth/{self._mount_path}/role/{role_name}/secret-id?list=true",
        )
        resp = requests.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def destroy_secret_id_accessors(self, role_name: str, secret_id_accessors: List[str] = None) -> Tuple[int, int]:
        destroyed, error = 0, 0
        if not secret_id_accessors:
            secret_id_accessors = self.get_secret_id_accessors(role_name)

        for sia in secret_id_accessors:
            if self.destroy_secret_id_accessor(role_name, sia):
                destroyed += 1
            else:
                error += 1
        return destroyed, error

    def destroy_secret_id_accessor(self, role_name: str, secret_id_accessor: str) -> bool:
        return self.destroy_secret_id(role_name, secret_id_accessor, True)

    def destroy_secret_id(self, role_name: str, secret_id: str, is_accessor: bool = False) -> bool:
        data = {}
        if is_accessor:
            name = "secret-id-accessor"
            data["secret_id_accessor"] = secret_id
        else:
            name = "secret-id"
            data["secret_id"] = secret_id

        url = urllib.parse.urljoin(
            self._vault_address,
            f"v1/auth/{self._mount_path}/role/{role_name}/{name}/destroy",
        )
        resp = requests.post(
            url=url, data=data, headers={TOKEN_HEADER: self._get_vault_token()}
        )
        if resp.ok:
            return True
        raise VaultException(resp.status_code, url, resp.text)

    def delete_role(self, role_name: str) -> bool:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._mount_path}/role/{role_name}"
        )
        resp = requests.delete(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return True
        raise VaultException(resp.status_code, url, resp.text)

    def list_role_names(self) -> List[str]:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._mount_path}/role?list=true"
        )
        resp = requests.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def get_role_id(self, role_name: str) -> Optional[str]:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._mount_path}/role/{role_name}/role-id"
        )
        resp = requests.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["role_id"]
        raise VaultException(resp.status_code, url, resp.text)

    def set_secret_id(self, role_name: str, secret_id: str = None, wrap_ttl: int = None, cidrs: List[str] = None, metadata: Dict[str, Any] = None) -> Dict:
        if not isinstance(metadata, Dict) or not metadata:
            metadata = {}

        if not isinstance(cidrs, List) or cidrs is None:
            cidrs = []

        endpoint = "secret-id"
        data = {
            "cidr_list": cidrs,
            "token_bound_cidrs": cidrs,
        }

        # only attach metadata if defined
        if metadata:
            data["metadata"] = json.dumps(metadata)

        # if the secret_id is created on client side, include it in the request and adjust the endpoint accordingly
        if secret_id:
            data["secret_id"] = secret_id
            endpoint = f"custom-{endpoint}"

        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._mount_path}/role/{role_name}/{endpoint}")
        headers = {TOKEN_HEADER: self._get_vault_token()}
        if wrap_ttl:
            headers["X-Vault-Wrap-TTL"] = f"{wrap_ttl}s"

        resp = requests.post(url=url, headers=headers, data=data)
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        if wrap_ttl:
            return resp.json()["wrap_info"]
        return resp.json()["data"]

    def lookup_secret_id_accessor(self, role_name: str, secret_id_accessor: str) -> Dict[str, Any]:
        return self.lookup_secret_id(role_name, secret_id_accessor, True)

    def lookup_secret_id(self, role_name: str, secret_id: str, is_accessor: bool = False) -> Dict[str, Any]:
        data = {}
        if is_accessor:
            name = "secret-id-accessor"
            data["secret_id_accessor"] = secret_id
        else:
            name = "secret-id"
            data["secret_id"] = secret_id

        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._mount_path}/role/{role_name}/{name}/lookup")
        resp = requests.post(url=url, headers={TOKEN_HEADER: self._get_vault_token()}, data=data)
        if resp.ok:
            return resp.json()["data"]

        raise VaultException(resp.status_code, url, resp.text)

    def rotate_secret_id(self, role_id: str, secret_id: str, role_name: str = None) -> Dict:
        self._vault_token = self.login(role_id, secret_id)
        data = self.lookup_secret_id(role_name, secret_id)
        cidrs = list(set(data["cidr_list"] + data["token_bound_cidrs"]))
        metadata = data["metadata"]

        creation_time = Utils.parse_datetime(data["creation_time"])
        try:
            expiration_time = Utils.parse_datetime(data["expiration_time"])
        except ValueError:
            expiration_time = None

        ret = {
            "creation_time": creation_time,
            "expiration_time": expiration_time,
            "rotated_secret_id": False,
        }

        if self.rotation_strategy.rotate(creation_time, expiration_time):
            secret_id = Utils.gen_random_password()
            ret["vault_response"] = self.set_secret_id(
                role_name=role_name, secret_id=secret_id, wrap_ttl=None, cidrs=cidrs, metadata=metadata
            )
            ret["rotated_secret_id"] = True

        return ret

    def login(self, role_id: str, secret_id: str) -> str:
        """ Login using an Approle. Returns the client token after successful login. """
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._mount_path}/login"
        )
        data = {"role_id": role_id, "secret_id": secret_id}
        resp = requests.post(url=url, data=data)
        if resp.ok:
            return resp.json()["auth"]["client_token"]
        raise VaultException(resp.status_code, url, resp.text)


class KeyValueAction(argparse.Action):
    """ Argparse action to parse a dict. """
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, dict())

        for value in values:
            key, value = value.split("=")
            getattr(namespace, self.dest)[key] = value


class ParsingUtils:
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
    def get_role_name(args: argparse.Namespace) -> str:
        """ Try to extract role_name from whereever the user wants to retrieve it from. """
        if args.role_name_json_file:
            if not Utils.is_secure_file(args.role_name_json_file):
                logging.warning("Permissions of file '%s' too liberal, consider setting more restrictive permissions",
                                args.role_name_json_file)
            role_name = Utils.extract_value_from_json_file(args.role_name_json_path, args.role_name_json_file)
            logging.info("Read role_name '%s' from JSON '%s'", role_name, args.role_name_json_file)
        else:
            role_name = args.role_name

        return role_name

    @staticmethod
    def get_secret_id(args: argparse.Namespace) -> Optional[str]:
        """ Try to extract secret_id from whereever the user wants to retrieve it from. """
        if args.secret_id_file:
            if not Utils.is_secure_file(args.secret_id_file):
                raise ValueError(f"Permissions of file {args.secret_id_file} too liberal, not continuing")
            secret_id = Utils.read_secret_id(args.secret_id_file)
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
                with open(config_file) as cf:
                    config = json.load(cf)
                    config_values.update(config)
            except json.decoder.JSONDecodeError as err:
                logging.error("Config file is not well formatted: %s", err)
                sys.exit(1)

        parser = argparse.ArgumentParser(parents=[conf_parser])

        parser.add_argument("-j", "--json-output", action="store_true", default=False)
        parser.add_argument("-q", "--quiet", action="store_true", default=False)
        parser.add_argument("--mount-path", default="approle")

        command_subparsers = parser.add_subparsers(help="sub-command help", dest="subparser_name")
        command_subparsers.add_parser(CMD_LIST_ROLES, help="List all approles by name")

        #############################################################################################
        # get-role-id
        #############################################################################################
        get_role_id = command_subparsers.add_parser(CMD_GET_ROLE_ID, help="Get role_id for a given approle name")
        get_role_id.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # delete-role
        #############################################################################################
        get_role_id = command_subparsers.add_parser(CMD_DELETE_ROLE, help="Delete an approle")
        get_role_id.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # list-secret-accessor-id
        #############################################################################################
        list_secret_accessor_ids = command_subparsers.add_parser(CMD_LIST_SECRET_ID_ACCESSORS,
                                                                 help="List all secret_id_accessors for a role")
        list_secret_accessor_ids.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # lookup-secret-id
        #############################################################################################
        lookup_secret_id = command_subparsers.add_parser(CMD_LOOKUP_SECRET_ID, help="Lookup a secret_id")
        lookup_secret_id.add_argument("-r", "--role-name", required=True)
        lookup_secret_id.add_argument("--secret-id-json-path", default=".secret_id")
        group = lookup_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("--secret-id", help="secret_id to use.")
        group.add_argument("--secret-id-file", help="Read secret_id from this file.")
        group.add_argument("--secret-id-json-file", help="Read secret_id from this JSON-encoded file.")

        #############################################################################################
        # login
        #############################################################################################
        login = command_subparsers.add_parser(CMD_LOGIN_APPROLE, help="Login to an approle")
        login.add_argument("--secret-id-json-path", default=".secret_id")
        login.add_argument("-r", "--role-id", required=True, help="role_id of the Approle to login.")
        login.add_argument("--token-file", help="Write acquired token to this file.")
        group = login.add_mutually_exclusive_group(required=True)
        group.add_argument("--secret-id", help="secret_id to use.")
        group.add_argument("--secret-id-file", help="Read secret_id from this file.")
        group.add_argument("--secret-id-json-file", help="Read secret_id from this JSON-encoded file.")

        #############################################################################################
        # destroy-secret-accessor-id
        #############################################################################################
        destroy_secret_accessor_id = command_subparsers.add_parser(
            CMD_DESTROY_SECRET_ACCESSOR_ID,
            help="Destroy a secret_id_accessor for a given role_name",
        )
        destroy_secret_accessor_id.add_argument("-r", "--role-name", required=True)
        group = destroy_secret_accessor_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-i", "--secret-id")
        group.add_argument("-f", "--secret-id-file")
        group.add_argument("-a", "--secret-id-accessor")

        #############################################################################################
        # destroy-all-secret-accessor-ids
        #############################################################################################
        destroy_all_secret_accessor_ids = command_subparsers.add_parser(
            CMD_DESTROY_ALL_SECRET_ACCESSOR_ID,
            help="Destroy all secret_id_accessors for a given role_name",
        )
        destroy_all_secret_accessor_ids.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # add-secret-id
        #############################################################################################
        add_secret_id = command_subparsers.add_parser(CMD_ADD_SECRET_ID, help="Add another secret-id to a role")
        add_secret_id.add_argument("--role-name-json-path", default=".role_name")
        add_secret_id.add_argument("--secret-id-json-path", default=".secret_id")
        add_secret_id.add_argument(
            "-w", "--wrap-ttl", type=int, default=None,
            help="Wraps the secret_id. Argument is specified in seconds."
        )
        add_secret_id.add_argument(
            "-a",
            "--auto-limit-cidr",
            help="Limits secret_id usage and token_usage to CIDR blocks.",
        )
        add_secret_id.add_argument(
            "-l",
            "--limit-cidr",
            default=list(),
            action="append",
            help="Limits secret_id usage and token_usage to CIDR blocks.",
        )
        add_secret_id.add_argument(
            "-d",
            "--destroy-others",
            default=False,
            action="store_true",
            help="Destroys other secret_id_accesors for this role.",
        )
        add_secret_id.add_argument("--metadata", nargs="*", action=KeyValueAction)
        add_secret_id.add_argument("-p", "--push-secret-id", action="store_true", default=False)

        group = add_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("--role-name", help="The role name to add the secret_id to")
        group.add_argument("--role-name-json-file", help="The role name to add the secret_id to")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = add_secret_id.add_mutually_exclusive_group(required=False)
        group.add_argument("--secret-id-file", help="Flat file that contains the AppRole's secret_id",)
        group.add_argument("--secret-id-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)

        #############################################################################################
        # rotate-secret-id
        #############################################################################################
        rotate_secret_id = command_subparsers.add_parser(CMD_ROTATE_SECRET_ID, help="Rotate secret-id")
        rotate_secret_id.add_argument("--role-name-json-path", default=".role_name", help="JSON path to role-name")
        rotate_secret_id.add_argument("--role-id-json-path", default=".role_id", help="JSON path to role-id")
        rotate_secret_id.add_argument("--secret-id-json-path", default=".secret_id", help="JSON path to secret-id")
        rotate_secret_id.add_argument("--metric-file", help="File to write prometheus metrics to")
        rotate_secret_id.add_argument("--ignore-cidr", help="Ignore previously attached CIDRs")

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("--role-id", help="The AppRole role_id.")
        group.add_argument("--role-id-json-file", help="JSON encoded file that contains the AppRole's role_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("--secret-id", help="The secret_id to use for authentication")
        group.add_argument("--secret-id-file", help="Flat file that contains the AppRole's secret_id")
        group.add_argument("--secret-id-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("--role-name", help="The AppRole's role_name. If not specified, uses the role_id.")
        group.add_argument("--role-name-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

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

        if args.subparser_name == CMD_ADD_SECRET_ID:
            if args.wrap_ttl and (60 >= args.wrap_ttl or args.wrap_ttl > 7200):
                raise ValueError("wrap_ttl must be 60 >= args.wrap_ttl >= 7200")


class Utils:
    @staticmethod
    def parse_datetime(date: str) -> datetime:
        return iso8601.parse_date(date)

    @staticmethod
    def lookup_host(hostname: str) -> List[str]:
        try:
            return [f"{socket.gethostbyname(hostname)}/32"]
        except socket.gaierror:
            return []

    @staticmethod
    def gen_random_password() -> str:
        return str(uuid.uuid4())

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
    def read_secret_id(secret_id_file: str) -> str:
        p = Path(secret_id_file).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").rstrip("\n")
        raise ValueError(f"Can not read secret-id from non-existent file '{p.name}'")


def _log_rotation(rotated: bool, creation_time: datetime, expiration_time: datetime) -> None:
    """Calculates and logs information about secret_id_rotation."""
    if rotated:
        action = "secret_id has been rotated"
    else:
        action = "No secret_id rotation needed"

    expiry_str = ""
    if expiration_time:
        expiry = expiration_time - datetime.now(timezone.utc)
        expiry_str = f", expiry in {expiry}"

    logging.info(
        "%s, creation_time: '%s', expiration_time: '%s'%s",
        action,
        creation_time,
        expiration_time,
        expiry_str,
    )


def run_add_secret_id_subcmd(vault_client: VaultClient, args: argparse.Namespace, json_output: JsonOutput) -> None:
    role_name = ParsingUtils.get_role_name(args)
    if args.push_secret_id:
        secret_id = ParsingUtils.get_secret_id(args)
        if not secret_id:
            secret_id = Utils.gen_random_password()
            logging.info("Creating new client-side generated secret_id for role_name %s", role_name)
        else:
            logging.info("Using locally supplied secret_id for role_name %s", role_name)
    else:
        secret_id = None
        logging.info("Generating secret_id on server side")

    if args.destroy_others:
        accessors = vault_client.get_secret_id_accessors(role_name)
        if accessors:
            logging.info("Destroying other secret_id_accessors for role name %s", role_name)
            destroyed, errors = vault_client.destroy_secret_id_accessors(role_name)
            logging.info("Destroyed %d, encountered %d errors", destroyed, errors)

    cidr = args.limit_cidr
    if args.auto_limit_cidr:
        cidr = list(set(cidr + Utils.lookup_host(args.auto_limit_cidr)))

    if cidr:
        logging.info("Using CIDRs '%s' as token and secret_id_bound_cidr and token_bound_cidr", cidr)

    ret = {"vault_response": vault_client.set_secret_id(
        role_name=role_name, secret_id=secret_id, wrap_ttl=args.wrap_ttl, cidrs=cidr, metadata=args.metadata
    )}

    process_new_secret_id(ret, args, is_wrapped=args.wrap_ttl is not None and args.wrap_ttl > 0)
    json_output.communicate(True, ret)


def run_rotate_subcmd(vault_client: VaultClient, args: argparse.Namespace, json_output: JsonOutput) -> None:
    if args.metric_file:
        json_output = PrometheusWrapperOutput(args.metric_file, json_output)

    role_id = ParsingUtils.get_role_id(args)
    role_name = ParsingUtils.get_role_name(args)
    secret_id = ParsingUtils.get_secret_id(args)
    # role_name not required, if not specified use role_id
    if not role_name:
        role_name = role_id

    logging.info("Creating new secret_id for role_name '%s'", role_name)
    ret = vault_client.rotate_secret_id(role_id, secret_id, role_name)
    _log_rotation(ret["rotated_secret_id"], ret["creation_time"], ret["expiration_time"])
    if not ret["rotated_secret_id"]:
        # secret_id has not been rotated
        json_output.communicate(True, ret)
        sys.exit(0)

    process_new_secret_id(ret, args)

    accessors_data = []
    for secret_id_accessor in vault_client.get_secret_id_accessors(role_name):
        accessors_data.append(vault_client.lookup_secret_id_accessor(role_name, secret_id_accessor))

    logging.info("Fetched %d secret_id_accessors for role_name '%s'", len(accessors_data), role_name)
    sorted_accessors = sorted(accessors_data, key=lambda a: a["creation_time"], reverse=True)
    delete_secret_id_accessors = [a["secret_id_accessor"] for a in sorted_accessors[1:]]
    ret["destroyed"], ret["errors"] = vault_client.destroy_secret_id_accessors(role_name, delete_secret_id_accessors)
    logging.info("Destroyed %d secret_id_accessors, %d errors occured", ret["destroyed"], ret["errors"])
    json_output.communicate(True, ret)


def process_new_secret_id(ret: Dict[str, Any], args: argparse.Namespace, is_wrapped: bool = False) -> None:
    """Decide how to process the newly created secret_id. Either write it to a json or flat file or print it."""
    leaf = "secret_id"
    if is_wrapped:
        leaf = "token"

    secret_value = ret["vault_response"][leaf]

    if args.secret_id_file:
        del ret["vault_response"][leaf]
        Utils.write_text_to_file(secret_value, args.secret_id_file)
        logging.info("Wrote %s to file '%s'", leaf, args.secret_id_file)
    elif args.secret_id_json_file:
        del ret["vault_response"][leaf]
        Utils.upsert_json_file(secret_value, args.secret_id_json_file, args.secret_is_json_path)
        logging.info("Wrote %s to file '%s'", leaf, args.secret_id_file)
    else:
        logging.warning("Printing sensitive data is disregarded, consider writing it to a file!")
        logging.info("New %s is: %s", leaf, secret_value)


def run(vault_client: VaultClient, args: argparse.Namespace, json_output: JsonOutput) -> None:
    if args.subparser_name == CMD_LIST_ROLES:
        role_names = vault_client.list_role_names()
        json_output.communicate(True, {"role_names": role_names})

    elif args.subparser_name == CMD_GET_ROLE_ID:
        role_id = vault_client.get_role_id(args.role_name)
        json_output.communicate(True, {"role_id": role_id, "role_name": args.role_name})

    elif args.subparser_name == CMD_DELETE_ROLE:
        success = vault_client.delete_role(args.role_name)
        json_output.communicate(success, {"role_name": args.role_name})

    elif args.subparser_name == CMD_LOOKUP_SECRET_ID:
        secret_id = ParsingUtils.get_secret_id(args)
        ret = vault_client.lookup_secret_id(args.role_name, secret_id)
        json_output.communicate(True, ret)

    elif args.subparser_name == CMD_LOGIN_APPROLE:
        secret_id = ParsingUtils.get_secret_id(args)
        logging.info("Trying to login to Approle...")
        token = vault_client.login(args.role_id, secret_id)
        ret = {}
        if args.token_file:
            logging.info("Writing token to file '%s'", args.token_file)
            Utils.write_text_to_file(token, args.token_file)
        else:
            logging.warning("Printing token to stdout, regard writing it to a file")
            logging.info("Login token is %s", token)
            ret["token"] = token
        json_output.communicate(True, ret)

    elif args.subparser_name == CMD_DESTROY_SECRET_ACCESSOR_ID:
        if args.secret_id_accessor:
            destroyed = vault_client.destroy_secret_id_accessor(args.role_name, args.secret_id_accessor)
        else:
            if args.secret_id_file:
                secret_id = Utils.read_secret_id(args.secret_id_file)
            else:
                secret_id = args.secret_id
            destroyed = vault_client.destroy_secret_id(args.role_name, secret_id)

        json_output.communicate(destroyed, {"role_name": args.role_name})

    elif args.subparser_name == CMD_DESTROY_ALL_SECRET_ACCESSOR_ID:
        destroyed, errors = vault_client.destroy_secret_id_accessors(args.role_name)
        json_output.communicate(errors == 0, {"destroyed": destroyed, "errors": errors, "role_name": args.role_name})

    elif args.subparser_name == CMD_LIST_SECRET_ID_ACCESSORS:
        secret_id_accessors = vault_client.get_secret_id_accessors(args.role_name)
        json_output.communicate(True, {"secret_id_accessors": secret_id_accessors, "role_name": args.role_name})

    elif args.subparser_name == CMD_ROTATE_SECRET_ID:
        run_rotate_subcmd(vault_client, args, json_output)

    elif args.subparser_name == CMD_ADD_SECRET_ID:
        run_add_secret_id_subcmd(vault_client, args, json_output)


def main() -> None:
    args = ParsingUtils.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    if args.quiet:
        logging.disable(logging.WARNING)

    json_output = JsonDisabledOutput()
    if args.json_output:
        json_output = JsonStdOutput()

    try:
        ParsingUtils.validate_args(args)
        vault_client = VaultClient(args.mount_path)
        run(vault_client, args, json_output)
    except ValueError as err:
        logging.error("Value error: %s", err)
        json_output.communicate(False, {"error": f"Missing value: {err}"})
        sys.exit(1)
    except VaultException as err:
        logging.error("Vault returned status_code %d for url %s: %s", err.status_code, err.url, err.text)
        json_output.communicate(False, {"status_code": err.status_code})
        sys.exit(1)
    except requests.exceptions.ConnectionError as err:
        logging.error("Could not talk to vault")
        json_output.communicate(False, {"error": f"could not communicate with vault: {err}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
