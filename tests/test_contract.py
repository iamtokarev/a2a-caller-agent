"""Loading a Brief: the committed examples load, and malformed Briefs are rejected at the edge."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from a2a_voice_agent.contract import Brief

COMMITTED_BRIEFS = sorted((Path(__file__).parents[1] / "briefs").glob("*.json"))


@pytest.mark.parametrize("path", COMMITTED_BRIEFS, ids=lambda path: path.name)
def test_committed_brief_loads(path: Path) -> None:
    Brief.model_validate_json(path.read_text())


def _load(data: dict[str, Any]) -> Brief:
    return Brief.model_validate_json(json.dumps(data))


def test_brief_without_language_is_rejected(brief_data: dict[str, Any]) -> None:
    del brief_data["language"]

    with pytest.raises(ValidationError) as exc:
        _load(brief_data)

    assert exc.value.errors()[0]["loc"] == ("language",)


def test_brief_in_unsupported_language_is_rejected(brief_data: dict[str, Any]) -> None:
    brief_data["language"] = "de"

    with pytest.raises(ValidationError) as exc:
        _load(brief_data)

    assert exc.value.errors()[0]["loc"] == ("language",)


def test_brief_with_unknown_field_is_rejected(brief_data: dict[str, Any]) -> None:
    brief_data["principle"] = {"name": "Alex Morgan"}

    with pytest.raises(ValidationError) as exc:
        _load(brief_data)

    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_callee_number_is_normalised_to_e164(brief_data: dict[str, Any]) -> None:
    brief_data["callee"]["number"] = "+44 20 7946 0123"

    assert _load(brief_data).callee.number == "+442079460123"
