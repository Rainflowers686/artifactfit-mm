from __future__ import annotations

from copy import deepcopy
from typing import Any

CURRENT_SCHEMA_VERSION = "1.0.0"


class MigrationError(ValueError):
    """Raised when a contract version cannot be migrated safely."""


def migrate_contract(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Migrate only explicitly supported versions; undeclared content fails closed."""
    version = raw.get("schema_version")
    if version == CURRENT_SCHEMA_VERSION:
        return deepcopy(raw), []
    if version != "0.1.0":
        raise MigrationError(
            f"unsupported schema_version {version!r}; expected 1.0.0 or migratable 0.1.0"
        )
    migrated = deepcopy(raw)
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    runtime = migrated.setdefault("runtime", {})
    runtime.setdefault("network_allowed", False)
    runtime.setdefault("sample_interval_ms", 100)
    runtime.setdefault("stdout_limit_bytes", 1_048_576)
    runtime.setdefault("stderr_limit_bytes", 1_048_576)
    runtime.setdefault("require_clean_exit", True)
    return migrated, ["0.1.0->1.0.0: added explicit fail-closed runtime defaults"]
