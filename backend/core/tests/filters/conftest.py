"""Pytest configuration and shared fixtures."""

from unittest.mock import MagicMock

import pytest

from imap_thingy.accounts import EMailAccount


@pytest.fixture
def mock_account() -> EMailAccount:
    """Create a mock email account for testing."""
    account = EMailAccount(
        name="test_account",
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="test_password",
    )
    account._connection = MagicMock()
    return account
