import sys
from pathlib import Path
from dataclasses import dataclass, field

import yaml

KEY_MAC = "physical"
KEY_HOSTNAME = "host"


@dataclass
class Duplicates:
    duplicated_macs: list = field(default_factory=list)
    duplicated_hostnames: dict = field(default_factory=dict)

    def has_errors(self):
        return len(self.duplicated_macs) + len(self.duplicated_hostnames) > 0


def read_hosts(hosts_file: Path):
    with open(hosts_file, encoding='utf-8') as f:
        return yaml.safe_load(f)

    return None


def check_for_dups(document) -> Duplicates:
    # check duplicated macs globally
    macs_seen = {}
    
    dups = Duplicates()
    for datacenter in document["local_hosts"]:
        # check duplicated hosts per datacenter
        hosts_seen = {}
        for host in document["local_hosts"][datacenter]:
            if KEY_MAC in host:
                macs_seen[host[KEY_MAC]] = 1 + macs_seen.get(host[KEY_MAC], 0)

            hosts_seen[host[KEY_HOSTNAME]] = 1 + hosts_seen.get(host[KEY_HOSTNAME], 0)

        duplicated_hosts = list(filter(lambda x: hosts_seen[x] > 1, hosts_seen.keys()))
        if duplicated_hosts:
            dups.duplicated_hostnames[datacenter] = duplicated_hosts

    duplicated_macs = list(filter(lambda x: macs_seen[x] > 1, macs_seen.keys()))
    if duplicated_macs:
        dups.duplicated_macs.extend(duplicated_macs)

    return dups


def main(hosts_file: Path) -> Duplicates:
    document = read_hosts(hosts_file)
    dups = check_for_dups(document)

    if dups.has_errors():
        print(dups)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("hosts_file must be supplied as argument to this function")
        sys.exit(1)

    main(Path(sys.argv[1]))
