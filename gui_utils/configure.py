from __future__ import annotations

from pathlib import Path
import math
import tomllib
from typing import Any, Mapping, Sequence

FORMAT_VERSION = 1
ScalarState = bool | int | float | str


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


def toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def toml_bool(value: bool) -> str:
    return "true" if value else "false"


def toml_scalar(value: ScalarState) -> str:
    if isinstance(value, bool):
        return toml_bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Scalar float values must be finite.")
        return repr(float(value))
    if isinstance(value, str):
        return toml_string(value)
    raise TypeError(f"Unsupported TOML scalar type: {type(value)!r}")


def toml_float_array(values: Sequence[float]) -> str:
    rendered = ", ".join(_render_finite_float(value) for value in values)
    return f"[{rendered}]"


def toml_string_array(values: Sequence[str]) -> str:
    rendered = ", ".join(toml_string(value) for value in values)
    return f"[{rendered}]"


def parse_color_rgba(value: Any, field_name: str = "color_rgba") -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or len(value) != 4:
        raise ValueError(f"{field_name} must be a sequence of four floats.")

    rgba = tuple(float(channel) for channel in value)
    for channel in rgba:
        if not math.isfinite(channel) or channel < 0.0 or channel > 1.0:
            raise ValueError(
                f"{field_name} values must be finite floats between 0.0 and 1.0."
            )
    return rgba  # type: ignore[return-value]


def parse_optional_color_rgba(
    value: Any,
    field_name: str = "color_rgba",
) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    return parse_color_rgba(value, field_name=field_name)


def parse_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of strings.")
    parsed = tuple(str(item) for item in value)
    return parsed


def parse_scalar_state(value: Any, field_name: str = "state") -> ScalarState:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite.")
        return float(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"{field_name} must be bool, int, float, or str.")


def _render_finite_float(value: float) -> str:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError("Float values must be finite.")
    return repr(rendered)
