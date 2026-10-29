from __future__ import annotations

import pytest

from agent.backends.mock import DEMO_SCREENS, MockBackend

FIXTURES = DEMO_SCREENS


@pytest.fixture
def mock_device() -> MockBackend:
    return MockBackend.from_dir(FIXTURES)
