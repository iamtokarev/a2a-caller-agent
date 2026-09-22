from pathlib import Path

from pydantic import ValidationError

from a2a_voice_agent.contract import Brief


def load_brief(path: str | Path) -> Brief:
    """Load brief from path"""

    raw = Path(path).read_bytes()
    try:
        return Brief.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid brief model: {exc.errors()}") from exc
