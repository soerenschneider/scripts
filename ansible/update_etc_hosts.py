#!/usr/bin/env python3

import argparse
import logging
import re

from pathlib import Path
from typing import List

import yaml
from lineinfile import remove_lines_from_file, add_line_to_file, AfterFirst


KEY_LOCAL_HOSTS = "local_hosts"
KEY_HA_RECORDS = "ha_records"
KEY_LOGICAL = "logical"
KEY_HOSTNAME = "host"


def build_host_list(hosts_file: Path):
    with open(hosts_file, encoding='utf-8') as f:
        return yaml.safe_load(f)

    return None


def get_entries(document) -> List[str]:
    hosts = []
    for datacenter in document[KEY_LOCAL_HOSTS]:
        for host in document[KEY_LOCAL_HOSTS][datacenter]:
            ip = host[KEY_LOGICAL]
            if KEY_HA_RECORDS in host:
                for ha in host[KEY_HA_RECORDS]:
                    hosts.append(f"{ip} {ha}")

            hosts.append(f"{ip} {host[KEY_HOSTNAME]}.{datacenter}")

    return hosts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='hosts updater')
    parser.add_argument('--source', '-s', dest='source', required=True, help="Source of truth for host definitions")
    parser.add_argument('--dest', '-d', dest='dest', default="/etc/hosts", help="Where to write the hosts to")
    parser.add_argument('--domains', dest='domains', nargs='+', default=["soeren.cloud", "soerenschneider.net"],
                        help="Domains to add to the hosts")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dest = Path(args.dest)

    # delete old dns entries
    domains_joined = r"|".join(args.domains)
    regex = re.compile(r"^.*(" + domains_joined + r")")
    modified = remove_lines_from_file(dest, regex)
    if modified:
        logging.info("Removed old dns entries")

    # add my dns entries
    hosts_to_add = build_host_list(args.source)
    hosts = get_entries(hosts_to_add)
    inserter = AfterFirst(r"^# start custom hosts")
    for domain in args.domains:
        for host in hosts:
            formatted = f"{host}.{domain}"
            modified = add_line_to_file(dest, formatted, inserter=inserter)

    if modified:
        logging.info("Wrote %d host entries", len(host))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    main()
