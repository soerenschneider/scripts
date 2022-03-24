from unittest import TestCase
from daily import Daily, IllegalDateException


class TestDaily(TestCase):
    def test_validate_filename_valid(self):
        Daily._validate_date("2021-06-01")

    def test_validate_filename_whitespaces(self):
        Daily._validate_date(" 2021-06-01  ")

    def test_validate_filename_invalid(self):
        try:
            Daily._validate_date("2021/06/01")
            self.fail("Expected validation to fail")
        except IllegalDateException:
            pass

    def test_validate_filename_invalid_two_digit_year(self):
        try:
            Daily._validate_date("21-06-01")
        except IllegalDateException:
            pass

    def test_validate_filename_empty(self):
        try:
            Daily._validate_date("")
            self.fail("Expected validation to fail")
        except IllegalDateException:
            pass

    def test_validate_filename_nil(self):
        try:
            Daily._validate_date(None)
            self.fail("Expected validation to fail")
        except IllegalDateException:
            pass