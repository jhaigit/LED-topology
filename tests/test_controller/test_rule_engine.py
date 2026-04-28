"""Unit tests for rule_engine cron matching logic."""

import time

import pytest

from ltp_controller.rule_engine import _cron_field_matches


class TestCronFieldMatches:
    def test_wildcard(self):
        assert _cron_field_matches("*", 0) is True
        assert _cron_field_matches("*", 30) is True
        assert _cron_field_matches("*", 59) is True

    def test_exact(self):
        assert _cron_field_matches("5", 5) is True
        assert _cron_field_matches("5", 6) is False
        assert _cron_field_matches("0", 0) is True

    def test_list(self):
        assert _cron_field_matches("1,5,10", 1) is True
        assert _cron_field_matches("1,5,10", 5) is True
        assert _cron_field_matches("1,5,10", 10) is True
        assert _cron_field_matches("1,5,10", 3) is False

    def test_range(self):
        assert _cron_field_matches("5-10", 5) is True
        assert _cron_field_matches("5-10", 7) is True
        assert _cron_field_matches("5-10", 10) is True
        assert _cron_field_matches("5-10", 4) is False
        assert _cron_field_matches("5-10", 11) is False

    def test_wildcard_step(self):
        assert _cron_field_matches("*/5", 0) is True
        assert _cron_field_matches("*/5", 5) is True
        assert _cron_field_matches("*/5", 10) is True
        assert _cron_field_matches("*/5", 15) is True
        assert _cron_field_matches("*/5", 3) is False
        assert _cron_field_matches("*/5", 7) is False

    def test_range_step(self):
        assert _cron_field_matches("0-20/5", 0) is True
        assert _cron_field_matches("0-20/5", 5) is True
        assert _cron_field_matches("0-20/5", 10) is True
        assert _cron_field_matches("0-20/5", 20) is True
        assert _cron_field_matches("0-20/5", 25) is False
        assert _cron_field_matches("0-20/5", 3) is False

    def test_range_step_nonzero_start(self):
        assert _cron_field_matches("2-14/4", 2) is True
        assert _cron_field_matches("2-14/4", 6) is True
        assert _cron_field_matches("2-14/4", 10) is True
        assert _cron_field_matches("2-14/4", 14) is True
        assert _cron_field_matches("2-14/4", 4) is False
        assert _cron_field_matches("2-14/4", 8) is False

    def test_step_with_exact_match(self):
        assert _cron_field_matches("*/15", 0) is True
        assert _cron_field_matches("*/15", 15) is True
        assert _cron_field_matches("*/15", 30) is True
        assert _cron_field_matches("*/15", 45) is True
        assert _cron_field_matches("*/15", 1) is False

    def test_list_with_ranges(self):
        assert _cron_field_matches("1-3,7-9", 2) is True
        assert _cron_field_matches("1-3,7-9", 8) is True
        assert _cron_field_matches("1-3,7-9", 5) is False

    def test_zero_and_boundary_values(self):
        assert _cron_field_matches("0", 0) is True
        assert _cron_field_matches("59", 59) is True
        assert _cron_field_matches("23", 23) is True

    def test_step_1(self):
        assert _cron_field_matches("*/1", 0) is True
        assert _cron_field_matches("*/1", 59) is True

    def test_large_step(self):
        assert _cron_field_matches("*/30", 0) is True
        assert _cron_field_matches("*/30", 30) is True
        assert _cron_field_matches("*/30", 15) is False
