#!/usr/bin/env python3

import argparse
import logging
import os
import re
import socket
import subprocess
import sys

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, List, Tuple

import requests
import yaml

DEFAULT_VG_NAME = "libvirt"
DEFAULT_VOL_SIZE_G = 20
KNOWN_DATACENTERS = ["dd", "ez", "pt", "rs"]

subcommands = {
    "sync": "sync",
    "create": "create"
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="manage libvirt lvm volumes")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force-recreate", "-f", type=bool, default=False, action=argparse.BooleanOptionalAction, help="Delete and re-create existing volumes")
    group.add_argument("--interactive", "-i", type=bool, default=None, action=argparse.BooleanOptionalAction, help="Interactively prompt for confirmation")
    parser.add_argument("--dry-run", "-n", type=bool, default=None, action=argparse.BooleanOptionalAction, help="Delete and re-create existing volumes")

    subparsers = parser.add_subparsers(title='Subcommands', dest='subcommand')
    sync = subparsers.add_parser(subcommands["sync"], help='Read information from hosts file and (re-)create volumes')
    sync.add_argument("--hosts-file", type=str, required=True, help="File or http link to hosts definition file")
    sync.add_argument("--base-image-dir", "-b", required=True, type=str, default=None, help="Dir containing base images")
    sync.add_argument("--vm-host", type=str, default=None, help="The host name of the host the VMs should be scheduled. Usually this is auto detected.")

    cmd_create_volume = subparsers.add_parser(subcommands["create"], help='Create a new volume on-the-fly')
    cmd_create_volume.add_argument("--vg-name", "-v", type=str, default=None, help="Name of the volume group")
    cmd_create_volume.add_argument("--vol-size", "-s", type=int, default=None, help="Size of the volume in GiB")
    cmd_create_volume.add_argument("--vol-name", "-n", required=True, type=str, default=None, help="Name of the volume")
    cmd_create_volume.add_argument("--base-image", "-b", required=True, type=str, default=None, help="Base image to use")
    cmd_create_volume.add_argument("--domain-name", "-d", type=str, default=None, help="Name of the domain (virtual machine)")

    # Parse the command-line arguments
    args = parser.parse_args()

    if args.subcommand not in subcommands.values():
        parser.print_help()
        print()
        print(f"No subcommand given, expected one of {subcommands.values()}")
        sys.exit(1)

    if args.subcommand == subcommands["create"]:
        # Assign the parsed values to the variables
        if not args.vg_name:
            args.vg_name = DEFAULT_VG_NAME

        if not args.domain_name:
            args.domain_name = args.vol_name

        if not args.vol_size:
            args.vol_size = DEFAULT_VOL_SIZE_G

    return args


# Is used when a choice is to be made, for example when a volume needs to be recreated
class UserInteraction(ABC):  # pylint: disable=too-few-public-methods
    def proceed(self) -> bool:
        pass


# Always answer with a preconfigured answer.
class NonInteractive(UserInteraction):  # pylint: disable=too-few-public-methods
    def __init__(self, proceed: bool):
        self._proceed = proceed

    def proceed(self) -> bool:
        return self._proceed


# Let the user interactively decide.
class Interactive(UserInteraction):  # pylint: disable=too-few-public-methods
    def proceed(self) -> bool:
        while True:
            user_input = input("Do you want to proceed (y/n): ").strip().lower()
            if user_input in ['y', 'yes']:
                return True
            if user_input in ['n', 'no']:
                return False
            print("Invalid input. Please enter 'y' or 'n'.")


class Calls(ABC):
    @abstractmethod
    def vg_exists(self, vg_name: str) -> bool:
        pass

    @abstractmethod
    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        pass

    @abstractmethod
    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vol_size: int) -> None:
        pass

    @abstractmethod
    def remove_volume(self, vg_name: str, volume_name: str):
        pass

    @abstractmethod
    def is_domain_running(self, vm_name: str) -> bool:
        return False

    @abstractmethod
    def shutdown_domain(self, domain_name: str):
        pass

    @abstractmethod
    def start_domain(self, domain_name):
        pass


class NoopCalls(Calls):
    def vg_exists(self, vg_name: str) -> bool:
        return True

    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        return False

    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vol_size: int) -> None:
        print(f"create volume for {vg_name}/{vol_name} using {base_image} ({vol_size}GiB)")

    def remove_volume(self, vg_name: str, volume_name: str):
        print(f"remove volume for {vg_name}/{volume_name}")

    def shutdown_domain(self, domain_name: str):
        print(f"shutdown domain {domain_name}")

    def start_domain(self, domain_name):
        print(f"start domain {domain_name}")

    def is_domain_running(self, vm_name: str) -> bool:
        return False


class NativeBinaries(Calls):
    def vg_exists(self, vg_name: str) -> bool:
        try:
            subprocess.run(["lvdisplay", "-C", "--select", f"vg_name={vg_name}"], capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        if not self.vg_exists(vg_name):
            return False

        result = subprocess.run(["lvdisplay", "-C", "--select", f"vg_name={vg_name}"], capture_output=True, text=True, check=True)
        output = result.stdout
        existing_vms = [line.split()[0] for line in output.splitlines()[1:]]
        return vol_name in existing_vms

    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vol_size: int = None):
        if not vol_size:
            vol_size = DEFAULT_VOL_SIZE_G

        subprocess.run(["lvcreate", "-L", f"{vol_size}G", "-n", vol_name, vg_name], check=True)
        dst = f"/dev/mapper/{vg_name}-{vol_name}"
        subprocess.run(["qemu-img", "convert", base_image, "-O", "raw", dst], check=True)
        #subprocess.run(["lvresize", "-L", f"{vol_size}G", f"{vg_name}/{vol_name}"], check=True)

    def remove_volume(self, vg_name: str, volume_name: str):
        lv_name = f"{vg_name}/{volume_name}"
        command = ["lvremove", "-f", lv_name]
        subprocess.run(command, check=True)

    def is_domain_running(self, vm_name: str) -> bool:
        try:
            result = subprocess.run(['virsh', 'list', '--name'], capture_output=True, text=True, check=True)
            output = result.stdout
            return vm_name in output.splitlines()
        except subprocess.CalledProcessError as err:
            logging.error("Error talking to libvirt: %s", err)
            return False

    def shutdown_domain(self, domain_name: str):
        # todo: check if actually running and add error handling
        command = ["virsh", "destroy", domain_name]
        subprocess.run(command, check=True)

    def start_domain(self, domain_name: str):
        command = ["virsh", "start", domain_name]
        subprocess.run(command, check=True)


def find_baseimage(base_dir: str, file_name: str) -> Optional[str]:
    matching_files = []

    file_name = file_name.lower()

    for root, _, files in os.walk(base_dir):
        for file in files:
            if file_name in str(file).lower():
                matching_files.append(os.path.join(root, file))

    return _filter_images(matching_files)


def _filter_images(matching_files: List[str]) -> Optional[str]:
    if not matching_files:
        return None

    sorted_files = sorted(matching_files, key=_extract_date_from_filename, reverse=True)
    return sorted_files[0]


def _extract_date_from_filename(filename: str) -> str:
    pattern = r'\d{8}'
    match = re.search(pattern, filename)
    if match:
        return match.group(0)
    return ""


def _hostname_without_domain(hostname: str) -> str:
    parts = hostname.split('.')
    if len(parts) > 1:
        return parts[0]
    return hostname


def iterate_vms(datacenter: str, vm_host: str, hosts_data: Dict[str, any], args: argparse.Namespace, impl: Calls, prompt: UserInteraction) -> None:
    if datacenter not in hosts_data['local_hosts']:
        return

    simple_hostname = _hostname_without_domain(vm_host)
    for host in hosts_data["local_hosts"][datacenter]:
        if "vm_config" not in host or host["vm_config"]["host"] not in [simple_hostname, vm_host]:
            continue

        wanted_os = host["vm_config"]["os"]
        base_image = find_baseimage(args.base_image_dir, wanted_os)
        if not base_image:
            logging.error("could not find any images for '%s' in dir '%s'", wanted_os, args.base_image_dir)
            continue

        block_devices = host["vm_config"]["block_devices"]
        if not block_devices:
            logging.error("no block devices configured, skipping")
            continue

        vg_name, vol_name = find_lvm_info(block_devices)
        if not vg_name or not vol_name:
            logging.error("no vg_name / vol_name found, skipping")
            continue

        vm_name = host["host"]
        disk_size = host["vm_config"]["disk_size_b"] / (1024 ** 3)

        create_volume(vg_name=vg_name, vol_name=vol_name, base_image=base_image, vol_size=disk_size, domain_name=vm_name, force_recreate=args.force_recreate, impl=impl, prompt=prompt)


def _detect_datacenter(hostname: str) -> Optional[str]:
    pattern = r'\.([^.\s]+)\.[^.]+\.[^.]+$'
    match = re.search(pattern, hostname)

    if match and  match.group(1) in KNOWN_DATACENTERS:
        return match.group(1)

    return None


def _get_hosts_data(hosts_file: str) -> Dict[str, any]:
    if hosts_file.startswith("http://") or hosts_file.startswith("https://"):
        data = requests.get(hosts_file, timeout=5)
        return yaml.safe_load(data)

    with open(hosts_file, 'r', encoding="utf8") as file:
        return yaml.safe_load(file)


def find_lvm_info(block_devices: List[str]) -> Optional[Tuple[str, str]]:
    for device in block_devices:
        if is_dm_device(device):
            return _parse_volgroup_volname(device)
    return None


def is_dm_device(name: str) -> bool:
    pattern = r'^/dev/mapper/[a-zA-Z0-9_-]+-[a-zA-Z0-9_-]+$'
    return bool(re.match(pattern, name))


def _parse_volgroup_volname(name: str) -> Tuple[str, str]:
    if not name.startswith("/dev/mapper/"):
        raise ValueError(f"{name} is not a valid dm device")

    parts = name.replace("/dev/mapper/", "", 1).split('-')
    if len(parts) != 2:
        raise ValueError(f"{name} is not a valid dm device")

    return parts[0], parts[1]


def create_volume(vg_name: str, vol_name: str, base_image: str, impl: Calls, vol_size: int = None, prompt = UserInteraction, domain_name: str = None, force_recreate: bool = False):
    if not domain_name:
        domain_name = vol_name

    if not vol_size:
        vol_size = DEFAULT_VOL_SIZE_G

    logging.info("Creating volume for %s/%s", vg_name, vol_name)
    if impl.volume_exists(vg_name=vg_name, vol_name=vol_name):
        logging.warning("volume '%s' already exists", vol_name)
        if not prompt.proceed() and not force_recreate:
            logging.error("Not forcing re-creation of volume, exiting.")
            return

        power_cycle_domain = impl.is_domain_running(domain_name)
        if power_cycle_domain:
            impl.shutdown_domain(domain_name)

        impl.remove_volume(vg_name=vg_name, volume_name=vol_name)

        if power_cycle_domain:
            impl.start_domain(domain_name)

    try:
        impl.create_volume(vg_name=vg_name, vol_name=vol_name, base_image=base_image, vol_size=vol_size)
    except subprocess.CalledProcessError as err:
        logging.error("creating volume failed: %s", err)


def main():
    logging.basicConfig(format='%(levelname)-8s %(message)s')
    logging.getLogger().setLevel(logging.INFO)
    args = parse_args()

    impl = NoopCalls() if args.dry_run else NativeBinaries()
    prompt = Interactive() if args.force_recreate is None else NonInteractive(proceed=args.force_recreate)

    if args.subcommand == subcommands["sync"]:
        vm_host = args.vm_host if args.vm_host else socket.gethostname()
        datacenter = _detect_datacenter(vm_host)
        if not datacenter:
            logging.error("could not detect datacenter from hostname %s", vm_host)
            sys.exit(1)
        else:
            logging.info("Detected datacenter '%s' from hostname '%s'", datacenter, vm_host)

        hosts_data = _get_hosts_data(args.hosts_file)
        logging.info("Loaded hosts_data with %d entries for dc %s", len(hosts_data["local_hosts"][datacenter]), datacenter)
        iterate_vms(datacenter=datacenter, vm_host=vm_host, hosts_data=hosts_data, args=args, impl=impl, prompt=prompt)
    elif args.subcommand == subcommands["create"]:
        if not impl.vg_exists(args.vg_name):
            logging.error("volume group '%s' does not exist", args.vg_name)
            sys.exit(1)
        base_image = Path(args.base_image)
        if not base_image.is_file() or not base_image.exists():
            raise ValueError(f"base image '{base_image}' must be a file and must exist")

        create_volume(vg_name=args.vg_name, vol_name=args.vol_name, base_image=args.base_image, impl=impl, prompt=prompt, vol_size=args.vol_size, domain_name=args.domain_name, force_recreate=args.force_recreate)


if __name__ == "__main__":
    main()
