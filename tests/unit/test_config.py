from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_backup_toolkit.config import ToolkitConfig, load_config, write_starter_config
from agent_backup_toolkit.errors import ConfigError

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def valid_config(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "age_recipient": VALID_RECIPIENT,
        "state_dir": str(tmp_path / "state"),
        "sources": [
            {
                "type": "directory",
                "name": "durable-notes",
                "path": str(tmp_path / "notes"),
                "include": ["**/*.md"],
                "exclude": ["**/.git/**"],
            }
        ],
        "destination": {"type": "local", "path": str(tmp_path / "backups")},
    }


def test_valid_config_parses(tmp_path: Path) -> None:
    parsed = ToolkitConfig.model_validate(valid_config(tmp_path))

    assert parsed.schema_version == 1
    assert parsed.sources[0].name == "durable-notes"
    assert parsed.destination.type == "local"


def test_unknown_fields_and_credentials_are_rejected(tmp_path: Path) -> None:
    data = valid_config(tmp_path)
    destination = data["destination"]
    assert isinstance(destination, dict)
    destination["access_key"] = "not-a-real-secret"

    with pytest.raises(Exception, match="access_key"):
        ToolkitConfig.model_validate(data)


@pytest.mark.parametrize("source_path", ["/", str(Path.home())])
def test_broad_source_paths_are_rejected(tmp_path: Path, source_path: str) -> None:
    data = valid_config(tmp_path)
    sources = data["sources"]
    assert isinstance(sources, list)
    assert isinstance(sources[0], dict)
    sources[0]["path"] = source_path

    with pytest.raises(Exception, match="source path"):
        ToolkitConfig.model_validate(data)


def test_duplicate_logical_names_are_rejected(tmp_path: Path) -> None:
    data = valid_config(tmp_path)
    sources = data["sources"]
    assert isinstance(sources, list)
    sources.append(dict(sources[0]))

    with pytest.raises(Exception, match="unique"):
        ToolkitConfig.model_validate(data)


def test_load_config_redacts_invalid_value(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    marker = "private-value-must-not-be-reflected"
    config_path.write_text(f"schema_version: 1\nage_recipient: {marker}\n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(config_path)

    assert marker not in str(caught.value)
    assert "age_recipient" in str(caught.value)


def test_starter_config_is_private_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.yaml"
    write_starter_config(path)

    assert "AGE_RECIPIENT_HERE" in path.read_text(encoding="utf-8")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        pytest.fail("starter config was not created with mode 0600")

    original = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="refusing to overwrite"):
        write_starter_config(path)
    assert path.read_text(encoding="utf-8") == original
