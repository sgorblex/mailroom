# Tests

This directory contains the test suite for imap-thingy.

## Running Tests

To run all tests:

```bash
pytest
```

To run with coverage:

```bash
pytest --cov=imap_thingy --cov-report=html
```

To run a specific test file:

```bash
pytest tests/test_criteria.py
```

To run a specific test:

```bash
pytest tests/test_criteria.py::TestCriteriaCombination::test_criteria_and_combination
```

## Test Structure

- `conftest.py` - Shared fixtures and pytest configuration
- `test_criteria.py` - Tests for criteria classes
- `test_actions.py` - Tests for action classes
- `test_filters.py` - Tests for filter classes
- `test_utils.py` - Tests for utility functions

## Fixtures

- `mock_account` - A mock email account for testing
