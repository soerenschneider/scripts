#!/usr/bin/env python3

import functools
import logging
import json
import os
import sys

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import requests
import urllib
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

CMD_GEN = "gen"

DEFAULT_MIN_VALIDITY_PERIOD_PERCENT = 34
BACKOFF_ATTEMPTS = 4
TOKEN_HEADER = "X-VAULT-TOKEN"
DEFAULT_APPROLE_MOUNT_PATH = "approle"
DEFAULT_AWS_MOUNT_PATH = "aws"
DEFAULT_PROFILE = "default"
CREDENTIALS_FILENAME = os.path.expanduser("~/.aws/credentials")


class ApproleSecretIdRotationStrategy(ABC):
    @abstractmethod
    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        pass


class StaticApproleRotationStrategy(ApproleSecretIdRotationStrategy):
    """Always rotate."""
    def __init__(self, rotate: bool = True):
        self._rotate = rotate

    def rotate(self, creation_time: datetime, expiration_time: datetime) -> bool:
        return self._rotate


class ValidityPeriodApproleRotationStrategy(ApproleSecretIdRotationStrategy):
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


@dataclass
class AwsCredentials:
    access_key_id: str
    secret_access_key: str

    def __init__(self, access_key_id: str, secret_access_key: str):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key

    def to_dict(self) -> Dict[str, str]:
        return {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
        }


class VaultException(Exception):
    def __init__(self, status_code: int, url: str = None, text: str = None):
        self.status_code = status_code
        self.url = url
        self.text = text


class VaultClient:
    def __init__(self, addr: str = None,
                 token: str = None,
                 approle_mount_path: str = None,
                 aws_mount_path: str = None,
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

        if not approle_mount_path:
            self._approle_mount_path = DEFAULT_APPROLE_MOUNT_PATH
        else:
            self._approle_mount_path = approle_mount_path

        if not aws_mount_path:
            self._aws_mount_path = DEFAULT_AWS_MOUNT_PATH
        else:
            self._aws_mount_path = aws_mount_path

        # set timeout globally
        self._http_pool = requests.Session()
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

    def aws_generate_credentials(self, role: str) -> AwsCredentials:
        path = f"v1/{self._aws_mount_path}/creds/{role}"
        url = urllib.parse.urljoin(self._vault_address, path)
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        json_data = resp.json()
        logging.info("Received credentials, valid for {} (req-id: {})", json_data["request_id"], json_data["lease_duration"])
        return AwsCredentials(access_key_id=json_data["data"]["access_key"],
                              secret_access_key=json_data["data"]["secret_key"])

    def aws_read_role(self, role: str) -> Dict[str, str]:
        path = f"v1/{self._aws_mount_path}/roles/{role}"
        url = urllib.parse.urljoin(self._vault_address, path)
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]

    def aws_list_roles(self) -> List[str]:
        path = f"v1/{self._aws_mount_path}/roles?list=true"
        url = urllib.parse.urljoin(self._vault_address, path)
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]["keys"]

    def totp_list_methods(self) -> List[str]:
        """ Returns all defined TOTP methods. """
        url = urllib.parse.urljoin(self._vault_address, "/v1/identity/mfa/method/totp?list=true")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]["keys"]

    def totp_destroy_secret_admin(self, method_id: str, entity_id: str) -> None:
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

    def totp_generate_secret_admin(self, method_id: str, entity_id: str, force: bool = False) -> Optional[str]:
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
                self.totp_destroy_secret_admin(method_id=method_id, entity_id=entity_id)
                # try again but set force=False, so we don't enter a loop
                return self.totp_generate_secret_admin(method_id=method_id, entity_id=entity_id, force=False)
            else:
                logging.warning("Not going to delete existing TOTP secret. Use '--force' to delete and re-generate")
                return

        return resp.json()["data"]["url"]

    def totp_generate_secret(self, method_id: str) -> Optional[str]:
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

    def totp_autodetect_method_id(self) -> Optional[str]:
        method_ids = self.totp_list_methods()
        if not method_ids:
            logging.error("No TOTP method_ids available. Please configure them first.")
            sys.exit(1)

        if len(method_ids) > 1:
            logging.error(f"Multiple TOTP method_ids found, don't know which one to pick: {method_ids}")
            sys.exit(1)

        return method_ids[0]

    def identity_entity_autodetect_id(self, name: str) -> Optional[str]:
        """ Returns all defined TOTP methods. """
        url = urllib.parse.urljoin(self._vault_address, f"/v1/identity/entity/name/{name}")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._vault_token})
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        return resp.json()["data"]["id"]

    def identity_list_groups(self) -> List[str]:
        url = urllib.parse.urljoin(self._vault_address, "v1/identity/group/name?list=true")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def identity_read_group(self, group_name: str) -> Dict[str, Any]:
        url = urllib.parse.urljoin(self._vault_address, f"v1/identity/group/name/{group_name}")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def identity_list_entities(self) -> List[str]:
        url = urllib.parse.urljoin(self._vault_address, "v1/identity/entity/name?list=true")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def identity_read_entity(self, entity_name: str) -> Dict[str, Any]:
        url = urllib.parse.urljoin(self._vault_address, f"v1/identity/entity/name/{entity_name}")
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def approle_get_secret_id_accessors(self, role_name: str) -> List[str]:
        url = urllib.parse.urljoin(
            self._vault_address,
            f"v1/auth/{self._approle_mount_path}/role/{role_name}/secret-id?list=true",
        )
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def approle_destroy_secret_id_accessors(self, role_name: str, secret_id_accessors: List[str] = None) -> Tuple[int, int]:
        destroyed, error = 0, 0
        if not secret_id_accessors:
            secret_id_accessors = self.approle_get_secret_id_accessors(role_name)

        for sia in secret_id_accessors:
            if self.approle_destroy_secret_id_accessor(role_name, sia):
                destroyed += 1
            else:
                error += 1
        return destroyed, error

    def approle_destroy_secret_id_accessor(self, role_name: str, secret_id_accessor: str) -> bool:
        return self.approle_destroy_secret_id(role_name, secret_id_accessor, True)

    def approle_destroy_secret_id(self, role_name: str, secret_id: str, is_accessor: bool = False) -> bool:
        data = {}
        if is_accessor:
            name = "secret-id-accessor"
            data["secret_id_accessor"] = secret_id
        else:
            name = "secret-id"
            data["secret_id"] = secret_id

        url = urllib.parse.urljoin(
            self._vault_address,
            f"v1/auth/{self._approle_mount_path}/role/{role_name}/{name}/destroy",
        )
        resp = self._http_pool.post(
            url=url, data=data, headers={TOKEN_HEADER: self._get_vault_token()}
        )
        if resp.ok:
            return True
        raise VaultException(resp.status_code, url, resp.text)

    def approle_delete_role(self, role_name: str) -> bool:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._approle_mount_path}/role/{role_name}"
        )
        resp = self._http_pool.delete(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return True
        raise VaultException(resp.status_code, url, resp.text)

    def approle_list_role_names(self) -> List[str]:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._approle_mount_path}/role?list=true"
        )
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["keys"]
        # vault actually misuses this status code instead of returning an empty list with a correct status code
        if resp.status_code == 404:
            return []
        raise VaultException(resp.status_code, url, resp.text)

    def approle_get_role(self, role_name: str) -> Optional[str]:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._approle_mount_path}/role/{role_name}"
        )
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]
        raise VaultException(resp.status_code, url, resp.text)

    def approle_get_role_id(self, role_name: str) -> Optional[str]:
        url = urllib.parse.urljoin(
            self._vault_address, f"v1/auth/{self._approle_mount_path}/role/{role_name}/role-id"
        )
        resp = self._http_pool.get(url=url, headers={TOKEN_HEADER: self._get_vault_token()})
        if resp.ok:
            return resp.json()["data"]["role_id"]
        raise VaultException(resp.status_code, url, resp.text)

    def approle_set_secret_id(self, role_name: str, secret_id: str = None, wrap_ttl: int = None, cidrs: List[str] = None, metadata: Dict[str, Any] = None) -> Dict:
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

        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._approle_mount_path}/role/{role_name}/{endpoint}")
        headers = {TOKEN_HEADER: self._get_vault_token()}
        if wrap_ttl:
            headers["X-Vault-Wrap-TTL"] = f"{wrap_ttl}s"

        resp = self._http_pool.post(url=url, headers=headers, data=data)
        if not resp.ok:
            raise VaultException(resp.status_code, url, resp.text)

        if wrap_ttl:
            return resp.json()["wrap_info"]
        return resp.json()["data"]

    def approle_lookup_secret_id_accessor(self, role_name: str, secret_id_accessor: str) -> Dict[str, Any]:
        return self.approle_lookup_secret_id(role_name, secret_id_accessor, True)

    def approle_lookup_secret_id(self, role_name: str, secret_id: str, is_accessor: bool = False) -> Dict[str, Any]:
        data = {}
        if is_accessor:
            name = "secret-id-accessor"
            data["secret_id_accessor"] = secret_id
        else:
            name = "secret-id"
            data["secret_id"] = secret_id

        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._approle_mount_path}/role/{role_name}/{name}/lookup")
        resp = self._http_pool.post(url=url, headers={TOKEN_HEADER: self._get_vault_token()}, data=data)
        if resp.ok:
            return resp.json()["data"]

        raise VaultException(resp.status_code, url, resp.text)

    @staticmethod
    def _parse_validity_period_dates(data: Dict[str, str]) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not data or "creation_time" not in data:
            return None, None

        try:
            creation_time = Utils.parse_timestamp(data["creation_time"])
        except (ValueError, KeyError, TypeError):
            logging.error("creation_time and expiration_time could not be parsed")
            return None, None

        try:
            expiration_time = Utils.parse_timestamp(data["expiration_time"])
        except (ValueError, KeyError, TypeError):
            logging.error("creation_time and expiration_time could not be parsed")
            return creation_time, None

        return creation_time, expiration_time

    def approle_rotate_secret_id(self, role_id: str, secret_id: str, role_name: str = None, rotation_strategy: ApproleSecretIdRotationStrategy = None) -> Dict:
        if not rotation_strategy:
            rotation_strategy = ValidityPeriodApproleRotationStrategy(DEFAULT_MIN_VALIDITY_PERIOD_PERCENT)

        self._vault_token = self.approle_login(role_id, secret_id)
        data = self.approle_lookup_secret_id(role_name, secret_id)
        cidrs = list(set(data["cidr_list"] + data["token_bound_cidrs"]))
        metadata = data["metadata"]

        validity_period_percent = -1
        creation_time, expiration_time = VaultClient._parse_validity_period_dates(data)
        if creation_time and expiration_time:
            validity_period_percent = max(0., (expiration_time - datetime.now(timezone.utc)).total_seconds() * 100. / (
                    expiration_time - creation_time).total_seconds())

        ret = {
            "creation_time": creation_time,
            "expiration_time": expiration_time,
            "rotated_secret_id": False,
            "validity_period_percent": validity_period_percent,
            "parsing_errors": 1 if not creation_time or not expiration_time else 0
        }

        if rotation_strategy.rotate(creation_time, expiration_time):
            ret["vault_response"] = self.approle_set_secret_id(
                role_name=role_name, secret_id=None, wrap_ttl=None, cidrs=cidrs, metadata=metadata
            )
            ret["rotated_secret_id"] = True

        return ret

    def approle_login(self, role_id: str, secret_id: str) -> str:
        """ Login using an Approle. Returns the client token after successful login. """
        url = urllib.parse.urljoin(self._vault_address, f"v1/auth/{self._approle_mount_path}/login")
        data = {"role_id": role_id, "secret_id": secret_id}
        resp = self._http_pool.post(url=url, data=data)
        if resp.ok:
            return resp.json()["auth"]["client_token"]
        raise VaultException(resp.status_code, url, resp.text)

    def wrapping_unwrap(self, token: str) -> Dict[str, Any]:
        """ Unwraps a secret_id. """
        url = urllib.parse.urljoin(self._vault_address, "v1/sys/wrapping/unwrap")
        resp = self._http_pool.post(url=url, headers={TOKEN_HEADER: token})
        if resp.ok:
            return resp.json()["data"]
        raise VaultException(resp.status_code, url, resp.text)


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