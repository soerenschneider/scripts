from unittest import TestCase

from vault_approle import Utils

class TestUtils(TestCase):
    def test_parse_datetime(self):
        Utils.parse_datetime("2022-01-16T17:45:16.7255+01:00")
