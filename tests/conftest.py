"""Shared test fixtures for trading terminal tests."""

import os
import sys

import pytest

# Ensure the trading-terminal directory is FIRST in sys.path
# so our config.py wins over ~/latpfn-trading/config/ package
_PROJECT_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(_PROJECT_DIR))


@pytest.fixture
def client():
    """Create a Flask test client."""
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
