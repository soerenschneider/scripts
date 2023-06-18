#!/usr/bin/env python3

import argparse
import logging
import re
import socket
import subprocess
import sys

import requests
import yaml
import os

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, List


default_vg_name = "libvirt"
default_vol_size_g = 20
known_datacenters = ["dd", "ez", "pt", "rs"]

subcommands = {
    "sync": "sync",
    "create": "create"
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="manage libvirt lvm volumes")
    parser.add_argument("--vg-name", "-v", type=str, default=None, help="Name of the volume group")
    parser.add_argument("--force-recreate", "-f", type=bool, default=None, action=argparse.BooleanOptionalAction, help="Delete and re-create existing volumes")
    parser.add_argument("--dry-run", "-n", type=bool, default=None, action=argparse.BooleanOptionalAction, help="Delete and re-create existing volumes")

    subparsers = parser.add_subparsers(title='Subcommands', dest='subcommand')
    sync = subparsers.add_parser(subcommands["sync"], help='Subcommand 1 help')
    sync.add_argument("--hosts-file", type=str, required=True, help="File or http link to hosts definition file")
    sync.add_argument("--base-image-dir", "-b", required=True, type=str, default=None, help="Dir containing base images")
    sync.add_argument("--vm-host", type=str, default=None, help="The host name of the host the VMs should be scheduled. Usually this is auto detected.")

    cmd_create_volume = subparsers.add_parser(subcommands["create"], help='Subcommand 1 help')
    cmd_create_volume.add_argument("--vol-size", "-s", type=int, default=None, help="Size of the volume in GiB")
    cmd_create_volume.add_argument("--vol-name", "-n", required=True, type=str, default=None, help="Name of the volume")
    cmd_create_volume.add_argument("--base-image", "-b", required=True, type=str, default=None, help="Base image to use")
    cmd_create_volume.add_argument("--domain-name", "-d", type=str, default=None, help="Name of the domain (virtual machine)")

    # Parse the command-line arguments
    args = parser.parse_args()

    if args.subcommand not in subcommands.values():
        print(f"No subcommand given, expected one of {subcommands.values()}")
        sys.exit(1)

    # Assign the parsed values to the variables
    if not args.vg_name:
        args.vg_name = default_vg_name

    if args.subcommand == subcommands["create"]:
        if not args.domain_name:
            args.domain_name = args.vol_name

        if not args.vol_size:
            args.vol_size = default_vol_size_g

    return args


class Calls(ABC):
    @abstractmethod
    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        pass

    @abstractmethod
    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vm_size_g: int) -> None:
        pass

    @abstractmethod
    def remove_volume(self, vg_name: str, volume_name: str):
        pass

    @abstractmethod
    def shutdown_domain(self, domain_name: str):
        pass

    @abstractmethod
    def start_domain(self, domain_name):
        pass


class NoopCalls(Calls):
    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        return False

    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vm_size_g: int) -> None:
        logging.info("create volume for %s/%s using %s (%dGiB)", vg_name, vol_name, base_image, vm_size_g)

    def remove_volume(self, vg_name: str, volume_name: str):
        print(f"remove volume for %s/%s", vg_name, volume_name)

    def shutdown_domain(self, domain_name: str):
        print(f"shutdown domain %s", domain_name)

    def start_domain(self, domain_name):
        print(f"start domain %s", domain_name)


class NativeBinaries(Calls):
    def volume_exists(self, vg_name: str, vol_name: str) -> bool:
        output = subprocess.run(["lvdisplay", "-C", "--select", f"vg_name={vg_name}"], capture_output=True, text=True).stdout
        existing_vms = [line.split()[0] for line in output.splitlines()[1:]]
        return vol_name in existing_vms

    def create_volume(self, vg_name: str, vol_name: str, base_image: Path, vm_size_g: int = None):
        if not vm_size_g:
            vm_size_g = 30

        subprocess.run(["lvcreate", "-L", f"{vm_size_g}G", "-n", vol_name, vg_name])
        dst = f"/dev/mapper/{vg_name}-{vol_name}"
        subprocess.run(["qemu-img", "convert", base_image, "-O", "raw", dst])
        subprocess.run(["lvresize", "-L", f"{vm_size_g}G", f"{vg_name}/{vol_name}"])

    def remove_volume(self, vg_name: str, volume_name: str):
        lv_name = f"{vg_name}/{volume_name}"
        command = ["lvremove", "-f", lv_name]
        subprocess.run(command, check=True)

    def shutdown_domain(self, domain_name: str):
        # todo: check if actually running and add error handling
        command = ["virsh", "shutdown", domain_name]
        subprocess.run(command, check=True)

    def start_domain(self, domain_name: str):
        command = ["virsh", "start", domain_name]
        subprocess.run(command, check=True)


def find_baseimage(base_dir: str, file_name: str) -> Optional[str]:
    matching_files = []

    file_name = file_name.lower()

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file_name in str(file).lower():
                matching_files.append(os.path.join(root, file))

    return _filter_images(matching_files)


def _filter_images(matching_files: List[str]) -> Optional[str]:
    sorted_files = sorted(matching_files, key=lambda x: _extract_date_from_filename(x), reverse=True)
    if sorted_files:
        return sorted_files[0]

    return None


def _extract_date_from_filename(filename: str) -> str:
    pattern = r'\d{8}'
    match = re.search(pattern, filename)
    if match:
        return match.group(0)
    return ""


def hostname_without_domain(hostname: str) -> str:
    parts = hostname.split('.')
    if len(parts) > 1:
        return parts[0]
    return hostname


def iterate_vms(datacenter: str, vm_host: str, hosts_data: Dict[str, any], args: argparse.Namespace) -> None:
    if datacenter not in hosts_data['local_hosts']:
        return

    impl = NoopCalls() if args.dry_run else None
    simple_hostname = hostname_without_domain(vm_host)
    for host in hosts_data["local_hosts"][datacenter]:
        if "vm_config" not in host or host["vm_config"]["host"] not in [simple_hostname, vm_host]:
            continue

        vm_name = host["host"]
        disk_size = host["vm_config"]["disk_size_b"] / (1024 ** 3)
        os = host["vm_config"]["os"]
        base_image = find_baseimage(args.base_image_dir, os)
        if not base_image:
            logging.error("could not find any images for '%s' in dir '%s'", os, args.base_image_dir)
            continue

        create_volume(vg_name=args.vg_name, vol_name=vm_name, base_image=base_image, vol_size=disk_size, domain_name=vm_name, force_recreate=args.force_recreate, impl=impl)


def detect_datacenter(hostname: str) -> Optional[str]:
    pattern = r'\.([^.\s]+)\.[^.]+\.[^.]+$'
    match = re.search(pattern, hostname)

    if match and  match.group(1) in known_datacenters:
        return match.group(1)

    return None


def get_hosts_data(hosts_file: str) -> Dict[str, any]:
    if hosts_file.startswith("http://") or hosts_file.startswith("https://"):
        data = requests.get(hosts_file)
        return yaml.safe_load(data)

    with open(hosts_file, 'r', encoding="utf8") as file:
        return yaml.safe_load(file)


def create_volume(vg_name: str, vol_name: str, base_image: str, vol_size: int = None, domain_name: str = None, force_recreate: bool = False, impl: Calls = None):
    if not impl:
        impl = NoopCalls()
    if not domain_name:
        domain_name = vol_name
    if not vol_size:
        vol_size = default_vol_size_g

    logging.info("Creating volume for %s/%s", vg_name, vol_name)
    if impl.volume_exists(vg_name=vg_name, vol_name=vol_name):
        logging.warning("volume '%s' already exists", vol_name)
        if not force_recreate:
            logging.error("Not forcing re-creation of volume, exiting.")
            return

        impl.shutdown_domain(domain_name)
        impl.remove_volume(vg_name=vg_name, volume_name=vol_name)
        impl.start_domain(domain_name)

    impl.create_volume(vg_name=vg_name, vol_name=vol_name, base_image=base_image, vm_size_g=vol_size)


def main():
    logging.basicConfig(format='%(levelname)s %(message)s')
    logging.getLogger().setLevel(logging.INFO)
    args = parse_args()

    if args.subcommand == subcommands["sync"]:
        vm_host = args.vm_host if args.vm_host else socket.gethostname()
        datacenter = detect_datacenter(vm_host)
        if not datacenter:
            logging.error("could not detect datacenter from hostname %s", vm_host)
            sys.exit(1)
        else:
            logging.info("Detected datacenter '%s' from hostname '%s'", datacenter, vm_host)

        hosts_data = get_hosts_data(args.hosts_file)
        logging.info("Loaded hosts_data with %d entries for this dc", len(hosts_data["local_hosts"][datacenter]))
        iterate_vms(datacenter=datacenter, vm_host=vm_host, hosts_data=hosts_data, args=args)
    elif args.subcommand == subcommands["create"]:
        if args.dry_run:
            impl = NoopCalls()
        create_volume(vg_name=args.vg_name, vol_name=args.vol_name, base_image=args.base_image, vol_size=args.vol_size, domain_name=args.domain_name, force_recreate=args.force_recreate, impl=impl)


if __name__ == "__main__":
    main()