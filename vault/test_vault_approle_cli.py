from unittest import TestCase
import datetime
from vault_approle_cli import VaultClient
from vault_approle_cli import ValidityPeriodApproleRotationStrategy


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


class TestValidityPeriodRotationStrategy(TestCase):
    def test_rotate_invalid_arg(self):
        try:
            ValidityPeriodApproleRotationStrategy(100)
        except ValueError:
            return
        self.fail("expected exception")

    def test_rotate_empty_args(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        self.assertTrue(impl.rotate(None, None))

    def test_rotate_empty_creation(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        expiry = now + datetime.timedelta(hours=24)

        self.assertTrue(impl.rotate(None, expiry))

    def test_rotate_empty_expiry(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        creation = now + datetime.timedelta(hours=24)

        self.assertTrue(impl.rotate(creation, None))

    def test_rotate_almost_zero(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        creation = now
        expiry = now + datetime.timedelta(hours=24)

        self.assertFalse(impl.rotate(creation, expiry))

    def test_rotate_almost_75_percent_validity_period(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        creation = now - datetime.timedelta(hours=3)
        expiry = now + datetime.timedelta(hours=1)

        self.assertTrue(impl.rotate(creation, expiry))

    def test_rotate_expiry_passed(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        creation = now - datetime.timedelta(hours=3)
        expiry = now - datetime.timedelta(hours=1)

        self.assertTrue(impl.rotate(creation, expiry))

    def test_rotate_mixed_up_params(self):
        impl = ValidityPeriodApproleRotationStrategy(50)
        now = datetime.datetime.now(datetime.timezone.utc)
        creation = now - datetime.timedelta(hours=3)
        expiry = now + datetime.timedelta(hours=1)

        self.assertTrue(impl.rotate(expiry, creation))

