import datetime

from unittest import TestCase

from wg_reset_peer import OpenBsdHostname, WireguardWatchdog, Peer


class TestOpenBsd(TestCase):
    def test__extract_endpoint_happy_path(self):
        line = "ifconfig wg1 wgpeer $PUB2 wgendpoint 127.0.0.1 7222 wgaip 192.168.5.2/32"
        endpoint = OpenBsdHostname()._extract_endpoint(line)
        self.assertEqual(endpoint, ("127.0.0.1", "7222"))

    def test__extract_endpoint_end_of_line(self):
        line = "ifconfig wg1 wgpeer $PUB2 wgaip 192.168.5.2/32 wgendpoint 127.0.0.1 7222"
        endpoint = OpenBsdHostname()._extract_endpoint(line)
        self.assertEqual(endpoint, ("127.0.0.1", "7222"))

    def test__extract_endpoint_no_port(self):
        line = "ifconfig wg1 wgpeer $PUB2 wgaip 192.168.5.2/32 wgendpoint 127.0.0.1"
        endpoint = OpenBsdHostname()._extract_endpoint(line)
        self.assertEqual(endpoint, None)

    def test__extract_endpoint_no_endpoint(self):
        line = "ifconfig wg1 wgpeer $PUB2 wgaip 192.168.5.2/32"
        endpoint = OpenBsdHostname()._extract_endpoint(line)
        self.assertEqual(endpoint, None)


class TestWireguardWatchdog(TestCase):
    def test__analyze_line_zero(self):
        ret = WireguardWatchdog._analyze_line("asd 0")
        peer = Peer("asd", datetime.datetime(1970, 1, 1, 0, 0))
        self.assertEqual(ret, peer)

    def test__analyze_line_invalid_timestamp(self):
        try:
            WireguardWatchdog._analyze_line("asd asd")
        except ValueError:
            return
        self.fail("expected value error")

    def test__analyze_line_happy_path(self):
        ret = WireguardWatchdog._analyze_line("UguPyBThx/+xMXeTbRYkKlP0Wh/QZT3vTLPOVaaXTD8= 1643508287")
        peer = Peer("UguPyBThx/+xMXeTbRYkKlP0Wh/QZT3vTLPOVaaXTD8=", datetime.datetime(2022, 1, 30, 2, 4, 47))
        self.assertEqual(ret, peer)
