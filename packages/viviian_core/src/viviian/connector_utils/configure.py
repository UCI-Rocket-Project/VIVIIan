from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any, Mapping

FORMAT_VERSION = 1


def read_toml_document(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    require_format_version(data)
    return data


def write_toml_document(target: str | Path, content: str) -> Path:
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def require_format_version(data: Mapping[str, Any]) -> None:
    version = data.get("format_version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported format_version={version!r}. Expected {FORMAT_VERSION}."
        )


def require_kind(data: Mapping[str, Any], *expected_kinds: str) -> str:
    kind = data.get("kind")
    if kind not in expected_kinds:
        expected = ", ".join(repr(item) for item in expected_kinds)
        raise ValueError(f"Expected kind in ({expected}), got {kind!r}.")
    return str(kind)


def require_keys(section: Mapping[str, Any], section_name: str, *keys: str) -> None:
    missing = [key for key in keys if key not in section]
    if not missing:
        return
    joined = ", ".join(repr(key) for key in missing)
    raise ValueError(f"{section_name} is missing required keys: {joined}.")


def toml_header(kind: str) -> list[str]:
    return [
        f"format_version = {FORMAT_VERSION}",
        f"kind = {toml_string(kind)}",
        "",
    ]


def toml_bool(value: bool) -> str:
    return "true" if value else "false"


def toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def toml_key(value: str) -> str:
    return toml_string(value)
