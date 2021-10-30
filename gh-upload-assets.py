#!/usr/bin/env python3

import argparse
import logging
import os
import json
import sys

from urllib.parse import urljoin
from typing import Optional, List

import requests
import backoff

defaultTag = "latest"


def main():
    logging.basicConfig(format='%(asctime)s %(message)s')
    logging.getLogger().setLevel(logging.INFO)
    args = parse_args()
    try:
        token = get_token(args)
    except Exception as err:
        logging.error("Could not read token: %s", err)
        sys.exit(1)

    uploader = AssetUploader(owner=args.owner, repo=args.repo, token=token)
    try:
        release_id = uploader.get_release_id(tag=args.tag)
    except Exception as err:
        logging.error("Could not fetch release_id: %s", err)
        sys.exit(1)

    target = os.path.abspath(args.target)
    if os.path.isdir(target):
        files = get_files_from_dir(target)
    else:
        files = [target]

    success = True
    for file_path in files:
        logging.info("Uploading file %s", file_path)
        try:
            uploader.upload_release(release_id=release_id, file_path=file_path)
        except AssetAlreadyExists as err:
            success = False
            logging.error("Asset '%s' already exists", os.path.basename(file_path))

    if not success:
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Upload GitHub release assets')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-t', '--token', help="The GitHub token")
    group.add_argument('-f', '--token-file', help="File that contains the GitHub token")
    group.add_argument('-v', '--token-vault-path', help="File that contains the GitHub token")

    parser.add_argument('-o', '--owner', help='GitHub owner of the repo', required=True)
    parser.add_argument('-r', '--repo', help='GitHub repo', required=True)

    parser.add_argument('--tag', default=defaultTag, help='Git tag to find release id')

    parser.add_argument(dest="target", help="File/directory to upload as asset")

    return parser.parse_args()


@backoff.on_exception(backoff.expo, requests.exceptions.RequestException)
def read_token_from_vault(vault_secret_path: str) -> str:
    addr = os.getenv("VAULT_ADDR")
    if not addr:
        raise ValueError("No VAULT_ADDR defined")

    token = os.getenv("VAULT_TOKEN")
    if not token:
        raise ValueError("No VAULT_TOKEN defined")

    url = urljoin(addr, f"/v1/secret/data/{vault_secret_path}")
    resp = requests.get(headers={'X-Vault-Token': token}, url=url)
    if resp.status_code > 204:
        raise VaultException(f"Couldn't fetch secret, got HTTP {resp.status_code}: {resp.content} for {url}")

    content = resp.json()
    try:
        return content["data"]["data"]["value"]
    except KeyError:
        raise VaultException("Could not extract GitHub token from secret")


def get_token(args: argparse.Namespace) -> Optional[str]:
    if args.token:
        return args.token

    if args.token_vault_path:
        return read_token_from_vault(args.token_vault_path)

    with open(os.path.expanduser(args.token_file), 'r') as token:
        return token.readline().strip()

    return None


def get_files_from_dir(dir_name: str) -> List[str]:
    return [os.path.join(dir_name, f) for f in os.listdir(dir_name) if os.path.isfile(os.path.join(dir_name, f))]


class AssetUploader:
    def __init__(self, owner: str, repo: str, token: str):
        self.owner = owner
        self.repo = repo
        self.token = token

    @backoff.on_exception(backoff.expo,
                          requests.exceptions.RequestException,
                          max_tries=3)
    def get_release_id(self, tag: str) -> int:
        headers = {
            'Authorization': f'Bearer {self.token}',
        }
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/{tag}"
        resp = requests.get(url=url, headers=headers)

        if resp.status_code == 200:
            parsed = resp.json()
            return parsed["id"]

        if resp.status_code == 401:
            raise InsufficientAccessException("Token invalid or wrong oauth scopes attached")

        if resp.status_code == 404:
            raise NoReleaseException("No release found")

        raise Exception("Unknown error, received status_code: %d", resp.status_code)

    @backoff.on_exception(backoff.expo,
                          requests.exceptions.RequestException,
                          max_tries=5)
    def upload_release(self, release_id: int, file_path: str):
        headers = {
            'Authorization': f'Bearer {self.token}',
        }
        params = {
            'name': os.path.basename(file_path)
        }

        files = {'name': open(file_path, 'rb')}
        url = f"https://uploads.github.com/repos/{self.owner}/{self.repo}/releases/{release_id}/assets"
        response = requests.post(url=url, headers=headers, files=files, params=params)
        if response.status_code == 201:
            return

        if response.status_code == 422:
            raise AssetAlreadyExists()

        raise Exception(f"Bad status code: {response.status_code}")


class NoReleaseException(Exception):
    pass


class AssetAlreadyExists(Exception):
    pass


class InsufficientAccessException(Exception):
    pass

class VaultException(Exception):
    pass


if __name__ == '__main__':
    main()
