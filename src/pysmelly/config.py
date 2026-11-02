"""Config file support — .pysmelly.toml and pyproject.toml [tool.pysmelly]."""

from __future__ import annotations

import glob
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from pysmelly.catalog import CatalogError, parse_user_catalog
from pysmelly.pyproject import PYPROJECT, read_pyproject

# File-pattern pairs whose co-change is by design (fnmatch patterns)
EXPECTED_COUPLING_KEY = "expected-coupling"

# The key whose value is a table in catalog.toml's schema: stdlib-alternatives
# patterns and dual-stack-dependency pairs the project adds to the shipped ones
CATALOG_KEY = "catalog"

# pysmelly's own config file; it wins over a pyproject.toml [tool.pysmelly]
DOTFILE = ".pysmelly.toml"

VALID_SEVERITIES = frozenset({"low", "medium", "high"})

VALID_COMMIT_MESSAGES = frozenset({"auto", "structured", "unstructured"})

_WINDOW_RE = re.compile(r"^\d+[dmy]$")


# Characters a TOML basic string may not hold raw: the C0 controls and DEL
_TOML_ESCAPED_CONTROLS = frozenset(map(chr, [*range(0x20), 0x7F]))


class ConfigError(Exception):
    """Raised for invalid config file contents."""


def _toml_char(char: str) -> str:
    if char in '"\\':
        return "\\" + char
    if char in _TOML_ESCAPED_CONTROLS:
        return f"\\u{ord(char):04X}"
    return char


def _toml_string(text: str) -> str:
    """``text`` as a TOML basic string: quote, backslash and the control
    characters TOML forbids raw are escaped, everything else kept."""
    return '"' + "".join(_toml_char(char) for char in text) + '"'


def expected_coupling_line(pairs: Iterable[tuple[str, str]]) -> str:
    """The config line declaring each file pair in ``pairs`` expected coupling.

    Paths are fnmatch-escaped, so each pattern matches its own file and no
    other, then written as TOML strings. The line is a complete assignment,
    valid on its own in ``[tool.pysmelly]`` or ``.pysmelly.toml``; a project
    that already sets the key adds the pairs to its list instead.
    """
    items = ", ".join(
        f"[{_toml_string(glob.escape(file_a))}, {_toml_string(glob.escape(file_b))}]"
        for file_a, file_b in pairs
    )
    return f"{EXPECTED_COUPLING_KEY} = [{items}]"


def _pyproject_table(target_dir: Path) -> dict | None:
    """The ``[tool.pysmelly]`` table of ``target_dir``'s pyproject.toml.

    Returns:
        The table, or None when there is no pyproject.toml or it has no
        ``[tool.pysmelly]``. None never means "there is config I could not
        read".

    Raises:
        ConfigError: if pyproject.toml exists but cannot be parsed. Reporting
            an unreadable pyproject as "no config" would silently run with
            default settings — no skips, no excludes — and look like a clean
            result.
    """
    data = read_pyproject(target_dir, ConfigError)
    if "tool" in data and "pysmelly" in data["tool"]:
        return data["tool"]["pysmelly"]
    return None


# The shape checks below each take a config value and yield what is wrong with
# it, worded to follow "'<key>' "; _validate_config reports the first.


def _string_list_problems(value: Any) -> Iterator[str]:
    if not isinstance(value, list):
        yield f"must be a list, got {type(value).__name__}"
    else:
        for item in value:
            if not isinstance(item, str):
                yield f"items must be strings, got {type(item).__name__}"


def _string_problems(value: Any) -> Iterator[str]:
    if not isinstance(value, str):
        yield f"must be a string, got {type(value).__name__}"


_PAIR_LENGTH = 2


def _pair_list_problems(value: Any) -> Iterator[str]:
    if not isinstance(value, list):
        yield f"must be a list of pairs, got {type(value).__name__}"
    else:
        for i, item in enumerate(value):
            if not isinstance(item, list) or len(item) != _PAIR_LENGTH:
                yield f"items must be 2-element lists, got {item!r} at index {i}"
            elif not all(isinstance(s, str) for s in item):
                yield f"items must contain strings, got {item!r} at index {i}"


# Each type-checked key and the shape check its value must pass. The catalog
# table is absent: parse_user_catalog validates it against catalog.toml's schema.
_KEY_SHAPES: dict[str, Callable[[Any], Iterator[str]]] = {
    "exclude": _string_list_problems,
    "skip": _string_list_problems,
    "min-severity": _string_problems,
    "check": _string_problems,
    "git-window": _string_problems,
    "commit-messages": _string_problems,
    EXPECTED_COUPLING_KEY: _pair_list_problems,
}

VALID_KEYS = frozenset(_KEY_SHAPES) | {CATALOG_KEY}

# String keys whose value must be one of a fixed set
_ENUMERATED_VALUES = {
    "min-severity": VALID_SEVERITIES,
    "commit-messages": VALID_COMMIT_MESSAGES,
}


def _validate_config(config: dict, source: str, valid_check_names: set[str]) -> dict:
    """Validate config keys, types, and values.

    Returns:
        The config, with a ``catalog`` table replaced by the ``Catalog`` it
        parses to.

    Raises:
        ConfigError: on the first problem found.
    """
    unknown = set(config) - VALID_KEYS
    if unknown:
        raise ConfigError(
            f"{source}: unknown key(s): {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(VALID_KEYS))}"
        )

    for key, shape_problems in _KEY_SHAPES.items():
        if key in config:
            for problem in shape_problems(config[key]):
                raise ConfigError(f"{source}: '{key}' {problem}")

    for key, valid_values in _ENUMERATED_VALUES.items():
        if key in config and config[key] not in valid_values:
            raise ConfigError(
                f"{source}: invalid {key} '{config[key]}'. "
                f"Valid values: {', '.join(sorted(valid_values))}"
            )

    if "check" in config and config["check"] not in valid_check_names:
        raise ConfigError(
            f"{source}: unknown check '{config['check']}'. "
            f"Use --list-checks to see available checks"
        )

    if "skip" in config:
        bad = [s for s in config["skip"] if s not in valid_check_names]
        if bad:
            raise ConfigError(
                f"{source}: unknown check(s) in skip: {', '.join(bad)}. "
                f"Use --list-checks to see available checks"
            )

    if "git-window" in config and not _WINDOW_RE.match(config["git-window"]):
        raise ConfigError(
            f"{source}: invalid git-window '{config['git-window']}'. "
            f"Expected format like 6m, 1y, 90d (number + d/m/y)"
        )

    if CATALOG_KEY not in config:
        return config
    try:
        catalog = parse_user_catalog(config[CATALOG_KEY], f"{source} {CATALOG_KEY}")
    except CatalogError as e:
        raise ConfigError(str(e)) from e
    return {**config, CATALOG_KEY: catalog}


def _warn_parent_config(target_dir: Path) -> None:
    """Warn if a parent directory has pysmelly config that isn't being used.

    An unparseable pyproject.toml in an ancestor directory is skipped rather
    than raised: this warning is advisory and about a directory the run was
    not pointed at.
    """
    parent = target_dir.parent
    while parent != parent.parent:
        try:
            found = (parent / DOTFILE).is_file() or _pyproject_table(parent) is not None
        except ConfigError:
            found = False
        if found:
            print(
                f"Warning: no config in {target_dir}, but {parent} has pysmelly config. "
                f"Run from {parent} (or pass it as the target) to use that config.",
                file=sys.stderr,
            )
            return
        parent = parent.parent


def load_config(target_dir: Path, valid_check_names: set[str]) -> dict:
    """Find and load config from .pysmelly.toml or pyproject.toml.

    First match wins: ``.pysmelly.toml``, then a pyproject.toml that has a
    ``[tool.pysmelly]`` table.

    Returns:
        A dict with validated config keys (``catalog`` parsed into a
        ``Catalog``), or an empty dict when the directory has no pysmelly
        config.

    Raises:
        ConfigError: when a config file exists but is unparseable or invalid.
            Invalid config is never silently defaulted; the CLI turns the
            error into exit status 1.
    """
    dotfile = target_dir / DOTFILE
    if dotfile.is_file():
        try:
            config = tomllib.loads(dotfile.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{dotfile}: {e}") from e
        source = str(dotfile)
    else:
        config = _pyproject_table(target_dir)
        if config is None:
            _warn_parent_config(target_dir)
            return {}
        source = f"{target_dir / PYPROJECT} [tool.pysmelly]"

    return _validate_config(config, source, valid_check_names)
