from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from artifactfit_mm.contracts.migrations import migrate_contract
from artifactfit_mm.contracts.models import ArtifactContract


class ContractValidationError(ValueError):
    """Raised when a contract fails syntax, schema, or semantic validation."""


@dataclass(frozen=True, slots=True)
class LoadedContract:
    contract: ArtifactContract
    source_path: Path
    contract_hash: str
    migrations: tuple[str, ...]


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "artifact-contract.schema.json"


def _read_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractValidationError(f"cannot read contract {path}: {exc}") from exc
    try:
        value = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ContractValidationError(f"contract syntax error in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("contract root must be an object")
    return value


def _schema_errors(document: dict[str, Any]) -> list[str]:
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def canonical_contract_bytes(contract: ArtifactContract) -> bytes:
    payload = contract.model_dump(mode="json", exclude_none=False)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def contract_hash(contract: ArtifactContract) -> str:
    return hashlib.sha256(canonical_contract_bytes(contract)).hexdigest()


def load_contract(path: str | Path) -> LoadedContract:
    source = Path(path).resolve()
    raw = _read_document(source)
    try:
        migrated, migration_notes = migrate_contract(raw)
    except ValueError as exc:
        raise ContractValidationError(str(exc)) from exc
    errors = _schema_errors(migrated)
    if errors:
        raise ContractValidationError("contract schema validation failed:\n- " + "\n- ".join(errors))
    try:
        contract = ArtifactContract.model_validate(migrated)
    except ValidationError as exc:
        rendered = []
        for issue in exc.errors(include_url=False):
            location = ".".join(str(part) for part in issue["loc"])
            rendered.append(f"{location}: {issue['msg']}")
        raise ContractValidationError(
            "contract semantic validation failed:\n- " + "\n- ".join(rendered)
        ) from exc
    return LoadedContract(
        contract=contract,
        source_path=source,
        contract_hash=contract_hash(contract),
        migrations=tuple(migration_notes),
    )


def validate_contract_file(path: str | Path) -> list[str]:
    loaded = load_contract(path)
    notes = [f"VALID: {loaded.source_path}", f"CONTRACT_SHA256: {loaded.contract_hash}"]
    notes.extend(f"MIGRATION: {note}" for note in loaded.migrations)
    return notes
