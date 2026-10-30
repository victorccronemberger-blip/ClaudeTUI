"""Canonical installed Free Claude Code package version."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

_DISTRIBUTION_NAME = "free-claude-code"
_UNKNOWN_VERSION = "0+unknown"

# Verified against the original import (8e92b86): src, pyproject.toml and uv.lock
# match this upstream release exactly before ClaudeTUI's changes.
UPSTREAM_BASE_VERSION = "6.18.5"
UPSTREAM_BASE_COMMIT = "8dfff83287d3133642ef8ef44e55518c6f41c287"


def package_version() -> str:
    """Return installed metadata, or an explicit source-only fallback."""
    try:
        return distribution_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _UNKNOWN_VERSION
