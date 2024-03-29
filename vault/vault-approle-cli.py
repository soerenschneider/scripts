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
import time
import uuid

from vault import VaultClient, VaultException, ValidityPeriodApproleRotationStrategy, StaticApproleRotationStrategy

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

DEFAULT_MIN_VALIDITY_PERIOD_PERCENT = 34
ROLE_ID_DEFAULT_JSON_PATH = ".role_id"
ROLE_NAME_DEFAULT_JSON_PATH = ".role_name"
SECRET_ID_DEFAULT_JSON_PATH = ".secret_id"


class JsonOutput(ABC):
    @abstractmethod
    def communicate(self, success: bool, pairs: Dict) -> None:
        pass

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

    if args.subparser_name == "rotate-secret-id" and args.metric_file:
        role_name = ParsingUtils.get_role_name(args)
        output = PrometheusWrapperOutput(args.metric_file, role_name, output)
    elif args.json_output:
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
    except ConnectionError as err:
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


class CommandListRoles(Command):
    cmd_id = "list-roles"

    def run(self, args: argparse.Namespace) -> None:
        role_names = self.vault_client.approle_list_role_names()
        self.output.communicate(True, {"role_names": role_names})


class CommandGetRoleId(Command):
    cmd_id = "get-role-id"

    def run(self, args: argparse.Namespace) -> None:
        role_id = self.vault_client.approle_get_role_id(args.role_name)
        self.output.communicate(True, {"role_id": role_id, "role_name": args.role_name})


class CommandGetRole(Command):
    cmd_id = "get-role"

    def run(self, args: argparse.Namespace) -> None:
        logging.info("Reading info for role %s", args.role_name)
        resp = self.vault_client.approle_get_role(args.role_name)
        self.output.communicate(True, resp)
        print(json.dumps(resp, indent=4, sort_keys=True))


class CommandDeleteRole(Command):
    cmd_id = "delete-role"

    def run(self, args: argparse.Namespace) -> None:
        success = self.vault_client.approle_delete_role(args.role_name)
        self.output.communicate(success, {"role_name": args.role_name})
        print(success)


class CommandUnwrapSecretId(Command):
    cmd_id = "unwrap-secret-id"

    def run(self, args: argparse.Namespace) -> None:
        token = ParsingUtils.get_token(args)
        if not token:
            raise ValueError("Could not find token")

        ret = {
            "vault_response": self.vault_client.wrapping_unwrap(token)
        }
        Utils.process_new_secret_id(ret, args)
        self.output.communicate(True, ret)


class CommandLookupSecretId(Command):
    cmd_id = "lookup-secret-id"

    def run(self, args: argparse.Namespace) -> None:
        secret_id = ParsingUtils.get_secret_id(args)
        resp = self.vault_client.approle_lookup_secret_id(args.role_name, secret_id)
        self.output.communicate(True, resp)
        print(json.dumps(resp, indent=4, sort_keys=True))


class CommandLoginApprole(Command):
    cmd_id = "login"

    def run(self, args: argparse.Namespace) -> None:
        role_id = ParsingUtils.get_role_id(args)
        secret_id = ParsingUtils.get_secret_id(args)
        logging.info("Trying to login to Approle...")
        token = self.vault_client.approle_login(role_id, secret_id)
        ret = {}
        if args.token_file:
            logging.info("Writing token to file '%s'", args.token_file)
            Utils.write_text_to_file(token, args.token_file)
        else:
            logging.warning("Printing token to stdout, regard writing it to a file")
            logging.info("Login token is %s", token)
            ret["token"] = token
        self.output.communicate(True, ret)


class CommandDestroySecretId(Command):
    cmd_id = "destroy-secret-id"

    def run(self, args: argparse.Namespace) -> None:
        if args.secret_id_accessor:
            destroyed = self.vault_client.approle_destroy_secret_id_accessor(args.role_name, args.secret_id_accessor)
        else:
            if args.secret_id_file:
                secret_id = Utils.read_from_file(args.secret_id_file)
            else:
                secret_id = args.secret_id
            destroyed = self.vault_client.approle_destroy_secret_id(args.role_name, secret_id)
        self.output.communicate(destroyed, {"role_name": args.role_name})


class CommandDestroyAllSecretIds(Command):
    cmd_id = "destroy-all-secret-ids"

    def run(self, args: argparse.Namespace) -> None:
        destroyed, errors = self.vault_client.approle_destroy_secret_id_accessors(args.role_name)
        self.output.communicate(errors == 0, {"destroyed": destroyed, "errors": errors, "role_name": args.role_name})


class CommandListSecretIdAccessors(Command):
    cmd_id = "list-secret-id-accessors"

    def run(self, args: argparse.Namespace) -> None:
        secret_id_accessors = self.vault_client.approle_get_secret_id_accessors(args.role_name)
        self.output.communicate(True, {"secret_id_accessors": secret_id_accessors, "role_name": args.role_name})


class CommandListEntities(Command):
    cmd_id = "list-entities"

    def run(self, args: argparse.Namespace) -> None:
        entities = self.vault_client.identity_list_entities()
        self.output.communicate(True, {"entities": entities})


class CommandGetEntity(Command):
    cmd_id = "get-entity"

    def run(self, args: argparse.Namespace) -> None:
        entity = self.vault_client.identity_read_entity(args.entity_name)
        self.output.communicate(True, entity)


class CommandListGroups(Command):
    cmd_id = "list-groups"

    def run(self, args: argparse.Namespace) -> None:
        groups = self.vault_client.identity_list_groups()
        self.output.communicate(True, {"groups": groups})


class CommandGetGroup(Command):
    cmd_id = "get-group"

    def run(self, args: argparse.Namespace) -> None:
        group = self.vault_client.identity_read_group(args.group_name)
        self.output.communicate(True, group)


class CommandAddSecretId(Command):
    cmd_id = "add-secret-id"

    def run(self, args: argparse.Namespace) -> None:
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
            accessors = self.vault_client.approle_get_secret_id_accessors(role_name)
            if accessors:
                logging.info("Destroying other secret_id_accessors for role name %s", role_name)
                destroyed, errors = self.vault_client.approle_destroy_secret_id_accessors(role_name)
                logging.info("Destroyed %d, encountered %d errors", destroyed, errors)

        cidr = args.limit_cidr
        if args.auto_limit_cidr:
            looked_up = Utils.lookup_host(args.auto_limit_cidr)
            if not looked_up:
                logging.error("Looking up host %s for automatically detect CIDR failed", args.auto_limit_cidr)
            cidr = list(set(cidr + looked_up))

        if cidr:
            logging.info("Using CIDRs '%s' as token and secret_id_bound_cidr and token_bound_cidr", cidr)

        ret = {"vault_response": self.vault_client.approle_set_secret_id(
            role_name=role_name, secret_id=secret_id, wrap_ttl=args.wrap_ttl, cidrs=cidr, metadata=args.metadata
        )}

        Utils.process_new_secret_id(ret, args, is_wrapped=args.wrap_ttl is not None and args.wrap_ttl > 0)
        self.output.communicate(True, ret)


class CommandRotateSecretId(Command):
    cmd_id = "rotate-secret-id"

    def run(self, args: argparse.Namespace) -> None:
        role_name = ParsingUtils.get_role_name(args)
        logging.info("Fetching role_id for role_name %s", role_name)
        secret_id = ParsingUtils.get_secret_id(args)
        role_id = ParsingUtils.get_role_id(args)

        rotation_strategy = ValidityPeriodApproleRotationStrategy(args.min_validity_period)
        if args.force_rotation:
            rotation_strategy = StaticApproleRotationStrategy()

        logging.info("Creating new secret_id for role_name '%s' using strategy %s", role_name, rotation_strategy.__class__.__name__)
        ret = self.vault_client.approle_rotate_secret_id(role_id, secret_id, role_name, rotation_strategy=rotation_strategy)
        CommandRotateSecretId._log_rotation(ret["rotated_secret_id"], ret["creation_time"], ret["expiration_time"])
        if not ret["rotated_secret_id"]:
            # secret_id has not been rotated
            self.output.communicate(True, ret)
            sys.exit(0)

        Utils.process_new_secret_id(ret, args)

        accessors_data = []
        for secret_id_accessor in self.vault_client.approle_get_secret_id_accessors(role_name):
            accessors_data.append(self.vault_client.approle_lookup_secret_id_accessor(role_name, secret_id_accessor))

        logging.info("Fetched %d secret_id_accessors for role_name '%s'", len(accessors_data), role_name)
        sorted_accessors = sorted(accessors_data, key=lambda a: a["creation_time"], reverse=True)
        delete_secret_id_accessors = [a["secret_id_accessor"] for a in sorted_accessors[1:]]
        ret["destroyed"], ret["errors"] = self.vault_client.approle_destroy_secret_id_accessors(role_name,
                                                                                                delete_secret_id_accessors)
        logging.info("Destroyed %d secret_id_accessors, %d errors occured", ret["destroyed"], ret["errors"])
        self.output.communicate(True, ret)

    @staticmethod
    def _log_rotation(rotated: bool, creation_time: datetime, expiration_time: datetime) -> None:
        """Calculates and logs information about secret_id_rotation."""
        action = "No secret_id rotation needed"
        if rotated:
            action = "secret_id has been rotated"

        expiry_str = ""
        if expiration_time:
            expiry = expiration_time - datetime.now(timezone.utc)
            expiry_str = f", expiry in {expiry}"

        logging.info("%s, creation_time: '%s', expiration_time: '%s'%s", action, creation_time, expiration_time, expiry_str)


class Utils:
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
    def upsert_json_file(value: str, json_file: str, json_path: str = SECRET_ID_DEFAULT_JSON_PATH) -> None:
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

    @staticmethod
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


class KeyValueAction(argparse.Action):
    """ Argparse action to parse a dict. """
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, {})

        for value in values:
            key, value = value.split("=")
            getattr(namespace, self.dest)[key] = value


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
        parser.add_argument("--mount-path", default="approle")

        command_subparsers = parser.add_subparsers(help="sub-command help", dest="subparser_name")
        command_subparsers.add_parser(CommandListRoles.cmd_id, help="List all approles by name")

        #############################################################################################
        # get-role-id
        #############################################################################################
        get_role_id = command_subparsers.add_parser(CommandGetRoleId.cmd_id, help="Get role_id for a given approle name")
        get_role_id.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # get-role
        #############################################################################################
        get_role = command_subparsers.add_parser(CommandGetRole.cmd_id, help="Get role information for a given approle name")
        get_role.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # delete-role
        #############################################################################################
        get_role_id = command_subparsers.add_parser(CommandDeleteRole.cmd_id, help="Delete an approle")
        get_role_id.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # list-secret-accessor-id
        #############################################################################################
        list_secret_accessor_ids = command_subparsers.add_parser(CommandListSecretIdAccessors.cmd_id,
                                                                 help="List all secret_id_accessors for a role")
        list_secret_accessor_ids.add_argument("-r", "--role-name", required=True)

        #############################################################################################
        # list-groups
        #############################################################################################
        command_subparsers.add_parser(CommandListGroups.cmd_id, help="List all groups by name")

        #############################################################################################
        # get-group
        #############################################################################################
        get_group = command_subparsers.add_parser(CommandGetGroup.cmd_id, help="Get a group by name")
        get_group.add_argument("-n", "--group-name", required=True)

        #############################################################################################
        # list-entities
        #############################################################################################
        command_subparsers.add_parser(CommandListEntities.cmd_id, help="List all entities by name")

        #############################################################################################
        # get-entity
        #############################################################################################
        get_entity = command_subparsers.add_parser(CommandGetEntity.cmd_id, help="Get an entity by name")
        get_entity.add_argument("-n", "--entity-name", required=True)

        #############################################################################################
        # lookup-secret-id
        #############################################################################################
        lookup_secret_id = command_subparsers.add_parser(CommandLookupSecretId.cmd_id, help="Lookup a secret_id")
        lookup_secret_id.add_argument("-r", "--role-name", required=True)
        lookup_secret_id.add_argument("--secret-id-json-path", default=SECRET_ID_DEFAULT_JSON_PATH)
        group = lookup_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--secret-id", help="secret_id to use.")
        group.add_argument("--secret-id-file", help="Read secret_id from this file.")
        group.add_argument("--secret-id-json-file", help="Read secret_id from this JSON-encoded file.")

        #############################################################################################
        # login
        #############################################################################################
        login = command_subparsers.add_parser(CommandLoginApprole.cmd_id, help="Login to an approle")
        login.add_argument("--secret-id-json-path", default=SECRET_ID_DEFAULT_JSON_PATH)
        login.add_argument("--token-file", help="Write acquired token to this file.")
        login.add_argument("--role-id-json-path", default=ROLE_ID_DEFAULT_JSON_PATH, help="JSON path to role-id")

        group = login.add_mutually_exclusive_group(required=True)
        group.add_argument("-r", "--role-id", help="The AppRole's role_id.")
        group.add_argument("-rj", "--role-id-json-file", help="JSON encoded file that contains the AppRole's role_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = login.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--secret-id", help="secret_id to use.")
        group.add_argument("-si", "--secret-id-file", help="Read secret_id from this file.")
        group.add_argument("-sj", "--secret-id-json-file", help="Read secret_id from this JSON-encoded file.")

        #############################################################################################
        # destroy-secret-accessor-id
        #############################################################################################
        destroy_secret_accessor_id = command_subparsers.add_parser(
            CommandDestroySecretId.cmd_id,
            help="Destroy a secret_id_accessor for a given role_name",
        )
        destroy_secret_accessor_id.add_argument("-r", "--role-name", required=True)
        group = destroy_secret_accessor_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--secret-id")
        group.add_argument("-sf", "--secret-id-file")
        group.add_argument("-aa", "--secret-id-accessor")

        #############################################################################################
        # destroy-all-secret-accessor-ids
        #############################################################################################
        destroy_all_secret_accessor_ids = command_subparsers.add_parser(CommandDestroyAllSecretIds.cmd_id,
                                                                        help="Destroy all secret_id_accessors for a "
                                                                             "given role_name")
        destroy_all_secret_accessor_ids.add_argument("-r", "--role-name", required=True)

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
        # add-secret-id
        #############################################################################################
        add_secret_id = command_subparsers.add_parser(CommandAddSecretId.cmd_id, help="Add another secret-id to a role")
        add_secret_id.add_argument("--role-name-json-path", default=ROLE_NAME_DEFAULT_JSON_PATH)
        add_secret_id.add_argument("--secret-id-json-path", default=SECRET_ID_DEFAULT_JSON_PATH)
        add_secret_id.add_argument("-w", "--wrap-ttl", type=int, default=None, help="Wraps the secret_id. Argument is "
                                                                                    "specified in seconds")
        add_secret_id.add_argument("-a", "--auto-limit-cidr", help="Perform a DNS lookup against a host and "
                                                                   "set CIDR validity for token and login")
        add_secret_id.add_argument("-l", "--limit-cidr", default=[], action="append", help="Limits secret_id usage and "
                                                                                           "token_usage to CIDR blocks")
        add_secret_id.add_argument("-d", "--destroy-others", default=False, action="store_true",
                                   help="Destroys other secret_ids for this role")
        add_secret_id.add_argument("--metadata", nargs="*", action=KeyValueAction)
        add_secret_id.add_argument("-p", "--push-secret-id", action="store_true", default=False)

        group = add_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-r", "--role-name", help="The role name to add the secret_id to")
        group.add_argument("-rj", "--role-name-json-file", help="The role name to add the secret_id to")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = add_secret_id.add_mutually_exclusive_group(required=False)
        group.add_argument("-sf", "--secret-id-file", help="Flat file that contains the AppRole's secret_id",)
        group.add_argument("-sj", "--secret-id-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)

        #############################################################################################
        # rotate-secret-id
        #############################################################################################
        rotate_secret_id = command_subparsers.add_parser(CommandRotateSecretId.cmd_id, help="Rotate secret-id")
        rotate_secret_id.add_argument("--role-name-json-path", default=ROLE_NAME_DEFAULT_JSON_PATH, help="JSON path to role-name")
        rotate_secret_id.add_argument("--role-id-json-path", default=ROLE_ID_DEFAULT_JSON_PATH, help="JSON path to role-id")
        rotate_secret_id.add_argument("--secret-id-json-path", default=SECRET_ID_DEFAULT_JSON_PATH, help="JSON path to secret-id")
        rotate_secret_id.add_argument("--metric-file", help="File to write prometheus metrics to")
        rotate_secret_id.add_argument("--ignore-cidr", help="Ignore previously attached CIDRs")
        rotate_secret_id.add_argument("--force-rotation", action="store_true", help="Force the rotation")
        rotate_secret_id.add_argument("--min-validity-period", type=int, default=DEFAULT_MIN_VALIDITY_PERIOD_PERCENT,
                                      help="Rotate the secret_id if the remaining validity is less than x. Value is "
                                           "in percent (0-100)")

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-s", "--secret-id", help="The secret_id to use for authentication")
        group.add_argument("-sf", "--secret-id-file", help="Flat file that contains the AppRole's secret_id")
        group.add_argument("-sj", "--secret-id-json-file", help="JSON encoded file that contains the AppRole's secret_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-ri", "--role-id", help="The AppRole's role_id.")
        group.add_argument("-rij", "--role-id-json-file", help="JSON encoded file that contains the AppRole's role_id")
        group.set_defaults(**config_values)
        group.required = ParsingUtils._is_supplied_by_config(group, config_values)

        group = rotate_secret_id.add_mutually_exclusive_group(required=True)
        group.add_argument("-rn", "--role-name", help="The AppRole's role_name.")
        group.add_argument("-rnj", "--role-name-json-file", help="JSON encoded file that contains the AppRole's role_name")
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

        if args.subparser_name == CommandAddSecretId.cmd_id:
            if args.wrap_ttl and (60 >= args.wrap_ttl or args.wrap_ttl > 7200):
                raise ValueError("wrap_ttl must be 60 >= args.wrap_ttl >= 7200")


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


if __name__ == "__main__":
    main()
