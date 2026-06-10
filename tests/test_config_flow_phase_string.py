"""Regression: phases dropdown value submitted as string (issue #4)."""
from __future__ import annotations

import conftest  # noqa: F401
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.eveus.config_flow import build_user_data_schema
from custom_components.eveus.const import CONF_PHASES


def test_user_schema_accepts_phase_count_submitted_as_string():
    # The mobile-app frontend submits select values as strings; the schema
    # must coerce "1"/"3" instead of failing "value must be one of [1, 3]".
    result = build_user_data_schema()(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "model": "16A",
            CONF_PHASES: "3",
            "soc_mode": "basic",
        }
    )
    assert result[CONF_PHASES] == 3
