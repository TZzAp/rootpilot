from __future__ import annotations

from pathlib import Path

import pytest

from agent.backends.mock import MockBackend

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


@pytest.fixture
def mock_device() -> MockBackend:
    return MockBackend.from_dir(FIXTURES)
