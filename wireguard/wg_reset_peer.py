#!/usr/bin/env python3

import argparse
import subprocess
import datetime
import logging
import platform
import time
import sys

from abc import ABC, abstractmethod
from typing import Tuple, Optional


class WatchdogException(Exception):
    pass


class WatchdogGraveException(Exception):
    pass


class Peer:
    def __init__(self, pubkey: str, last_handshake: datetime.datetime):
        if not pubkey:
            raise ValueError("Invalid pubkey supplied")
        self.pubkey = pubkey
        self.last_handshake = last_handshake

    def is_stale(self, seconds: int = 300) -> bool:
        now = datetime.datetime.utcnow()
        delta = now - self.last_handshake
        return delta.seconds >= seconds

    def __eq__(self, other):
        if isinstance(other, Peer):
            return self.pubkey == other.pubkey and self.last_handshake == other.last_handshake
        return False


class WireguardSystem(ABC):
    @abstractmethod
    def get_endpoint(self, interface: str, pubkey: str) -> Optional[str]:
        pass


class OpenBsdHostname(WireguardSystem):
    _endpoint_keyword = "wgendpoint"

    def get_endpoint(self, interface: str, pubkey: str) -> Optional[str]:
        definition = OpenBsdHostname._get_definition(interface, pubkey)
        endpoint = OpenBsdHostname._extract_endpoint(definition)
        if len(endpoint) == 2:
            return f"{endpoint[0]}:{endpoint[1]}"

        return None

    def _extract_endpoint(self, definition: str) -> Optional[Tuple[str, str]]:
        tokens = definition.split()
        for idx, token in enumerate(tokens):
            if token == self._endpoint_keyword and idx + 2 < len(tokens):
                return tokens[idx+1], tokens[idx+2]

        return None

    def _get_definition(self, device: str, pubkey: str) -> Optional[str]:
        with open(f'/etc/hostname.{device}', 'r', encoding="utf-8") as f:
            for line in f.readlines():
                if pubkey in line and self._endpoint_keyword in line:
                    return line
        return None


class WireguardWatchdog:
    def __init__(self, system: WireguardSystem, staleness_threshold: int):
        self._wg_impl = system
        self._staleness_threshold = staleness_threshold

    def _get_all_peers(self, interface: str) -> str:
        cmd = ["false"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise WatchdogException(f"Running '{cmd}' return exit code {result.returncode}")
        return result.stdout

        return ""

    def _reset_peer(self, interface: str, pubkey: str, endpoint: str):
        reset_peer_cmd = ['wg', 'set', interface, 'peer', pubkey, 'endpoint', endpoint]
        result = subprocess.run(reset_peer_cmd, capture_output=True, text=True)

    def run(self, interface: str):
        while True:
            try:
                self.check_handshakes(interface)
                time.sleep(30)
            except WatchdogException as err:
                logging.error("Watchdog unsuccessful: %s", err)
                time.sleep(30)

    def check_handshakes(self, interface: str):
        peer_list = self._get_all_peers(interface)
        for line in peer_list.splitlines():
            try:
                peer = self._analyze_line(line)
            except ValueError:
                logging.error("Could not parse info for line '%s'", line)
                continue

            if peer.is_stale(self._staleness_threshold):
                last_handshake = datetime.datetime.utcnow() - peer.last_handshake
                logging.info("Peer %s needs to be reset, handshake is %s ago", peer.pubkey, last_handshake)
                endpoint = self._wg_impl.get_endpoint(interface, peer.pubkey)
                if endpoint:
                    self._reset_peer(interface, peer.pubkey, endpoint)
                else:
                    logging.error("Could not reset peer %s, could not detect endpoint", peer.pubkey)

    @staticmethod
    def _analyze_line(line: str) -> Peer:
        tokens = line.split()
        if len(tokens) != 2:
            return None

        timestamp = datetime.datetime.utcfromtimestamp(int(tokens[1]))
        return Peer(tokens[0], timestamp)


def pre_flight_check():
    pre_flight_check_cmd = ['wg', 'help']
    subprocess.run(pre_flight_check_cmd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interface", default="wg0", help="Wireguard interface to check")
    parser.add_argument("-s", "--stale-threshold", default=180, type=int, help="Seconds after which a peer's handshake "
                                                                               "is considered stale")
    return parser.parse_args()


def validate_args(args: argparse.Namespace):
    if args.stale_threshold < 30 or args.stale_threshold > 600:
        raise ValueError("stale_threshold must be withing [30, 600]")


def get_wireguard_impl() -> WireguardSystem:
    if "openbsd" == platform.system().lower():
        return OpenBsdHostname()

    raise NotImplementedError(f"System '{platform.system().lower()}' not supported, yet")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    try:
        pre_flight_check()
    except FileNotFoundError:
        logging.error("'wg' not found, please install 'wg-tools'")
        sys.exit(1)

    args = parse_args()
    try:
        validate_args(args)
    except ValueError as err:
        logging.error("invalid conf: %s", err)
        sys.exit(1)

    try:
        impl = get_wireguard_impl()
    except NotImplemented as err:
        logging.error(err)
        sys.exit(1)

    dog = WireguardWatchdog(impl, args.stale_threshold)

    try:
        dog.run(args.interface)
    except KeyboardInterrupt:
        logging.info("Shutting down")
    except WatchdogGraveException as err:
        logging.error(err)
        sys.exit(1)


if __name__ == "__main__":
    main()