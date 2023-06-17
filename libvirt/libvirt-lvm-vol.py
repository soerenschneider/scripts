#!/usr/bin/env python
import argparse
import subprocess
import sys

from abc import ABC, abstractmethod
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="manage libvirt lvm volumes")

    # Define the command-line arguments
    parser.add_argument("--vg-name", "-v", type=str, default=None, help="Name of the LVM")
    parser.add_argument("--vol-size", "-s", type=int, default=None, help="Size of the VM in GB")
    parser.add_argument("--vol-name", "-n", required=True, type=str, default=None, help="Name of the VM")
    parser.add_argument("--base-image", "-b", required=True, type=str, default=None, help="Source file")
    parser.add_argument("--domain-name", "-d", type=str, default=None, help="Name of the domain (virtual machine)")
    parser.add_argument("--force-recreate", "-f", type=bool, default=None, action=argparse.BooleanOptionalAction, help="Delete and re-create existing volumes")

    # Parse the command-line arguments
    args = parser.parse_args()

    # Assign the parsed values to the variables
    if not args.vg_name:
        args.vg_name = "libvirt"

    if not args.domain_name:
        args.domain_name = args.vol_name

    if not args.vol_size:
        args.vol_size = 30

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


def main():
    args = parse_args()
    impl = NativeBinaries()

    # Check if logical volume exists
    if impl.volume_exists(vg_name=args.vg_name, vol_name=args.vol_name):
        print(f"volume '{args.vol_name}' already exists")
        if not args.force_recreate:
            print("Not forcing re-creation of volume, exiting.")
            sys.exit(1)

        impl.shutdown_domain(args.domain_name)
        impl.remove_volume(vg_name=args.vg_name, volume_name=args.vol_name)
        impl.start_domain(args.domain_name)

    impl.create_volume(vg_name=args.vg_name, vol_name=args.vol_name, base_image=args.base_image, vm_size_g=args.vol_size)


if __name__ == "__main__":
    main()