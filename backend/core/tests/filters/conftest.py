"""Pytest configuration and shared fixtures."""

from unittest.mock import MagicMock

import pytest

from imap_thingy.accounts import Account


class MockEMailAccount(Account):
    """Account with connect replaced by a MagicMock for tests."""

    connect: MagicMock


@pytest.fixture
def mock_account() -> MockEMailAccount:
    """Create a mock email account for testing."""
    account = MockEMailAccount(
        name="test_account",
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="test_password",
    )
    account.connect = MagicMock(return_value=MagicMock())
    return account
