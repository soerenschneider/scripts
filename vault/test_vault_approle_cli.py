from unittest import TestCase
import datetime
from vault_approle_cli import VaultClient


class TestVaultClient(TestCase):
    def test__parse_validity_period_dates_empty_none(self):
        ret = VaultClient._parse_validity_period_dates(None)
        assert ret == (None, None)

    def test__parse_validity_period_dates_empty_dict(self):
        ret = VaultClient._parse_validity_period_dates({})
        assert ret == (None, None)

    def test__parse_validity_period_dates_empty_happy_path(self):
        data = {
            "creation_time": "2022-02-07T16:26:54.32952+01:00",
            "expiration_time": "2022-03-09T16:26:54.32952+01:00"
        }

        ret = VaultClient._parse_validity_period_dates(data)
        c = datetime.datetime(2022, 2, 7, 16, 26, 54, 329520,
                              datetime.timezone(datetime.timedelta(seconds=3600), '+01:00'))
        e = datetime.datetime(2022, 3, 9, 16, 26, 54, 329520,
                              datetime.timezone(datetime.timedelta(seconds=3600), '+01:00'))

        assert ret == (c, e)

    def test__parse_validity_period_dates_empty_happy_path_ttl(self):
        data = {
            "creation_time": "2022-02-07T16:26:54.32952+01:00",
            "secret_id_ttl": 2592000,
        }

        ret = VaultClient._parse_validity_period_dates(data)
        c = datetime.datetime(2022, 2, 7, 16, 26, 54, 329520,
                              datetime.timezone(datetime.timedelta(seconds=3600), '+01:00'))
        e = datetime.datetime(2022, 3, 9, 16, 26, 54, 329520,
                              datetime.timezone(datetime.timedelta(seconds=3600), '+01:00'))

        assert ret == (c, e)

    def test__parse_validity_period_dates_empty_happy_path_only_creation_time(self):
        data = {
            "creation_time": "2022-02-07T16:26:54.32952+01:00",
        }

        ret = VaultClient._parse_validity_period_dates(data)
        c = datetime.datetime(2022, 2, 7, 16, 26, 54, 329520,
                              datetime.timezone(datetime.timedelta(seconds=3600), '+01:00'))

        assert ret == (c, None)

    def test__parse_validity_period_dates_empty_happy_path_only_garbage(self):
        data = {
            "creation_time": "i'm not even a date",
        }

        ret = VaultClient._parse_validity_period_dates(data)
        assert ret == (None, None)

