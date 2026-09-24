"""Shared test fixtures."""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()
