import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SERVICE_AUTH_TOKEN", "")

FIXTURES = Path(__file__).parent / "fixtures" / "real-sanitized"


@pytest.fixture
def fx():
    def load(name: str):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert payload["_fixture"]["captured_at"] and payload["_fixture"]["source"]
        return payload["data"]

    return load


@pytest.fixture
def fx_bytes():
    def load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return load
