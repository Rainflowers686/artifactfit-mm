from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from artifactfit_mm.adapters.repository import inspect_repository
from artifactfit_mm.contracts.loader import load_contract
from artifactfit_mm.receipts.writer import capture_environment, read_json, utc_now
from artifactfit_mm.runners.executor import RunResult, run_contract


@dataclass(frozen=True, slots=True)
class ReplayResult:
    comparison_path: str
    original_summary: str
    replay_summary: str | None
    contract_hash_match: bool
    commit_match: bool
    commands_match: bool
    stage_agreement: bool | None
    environment_drift: dict[str, object]
    verdict: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _recorded_commands(loaded_contract: Any) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {
        "P0": ["artifactfit:internal:p0"],
        "P1": ["artifactfit:internal:p1"],
    }
    for index in range(2, 9):
        key = f"P{index}"
        spec = loaded_contract.stages.get(key)
        if spec is None or not spec.enabled:
            break
        commands[key] = list(spec.command)
    return commands


def _environment_drift(original: dict[str, Any], current: dict[str, Any]) -> dict[str, object]:
    keys = ("platform", "python", "python_executable", "ram_total_bytes", "is_wsl")
    return {
        key: {"original": original.get(key), "current": current.get(key)}
        for key in keys
        if original.get(key) != current.get(key)
    }


def replay_run(summary_path: str | Path, *, receipt_root: str | Path | None = None) -> ReplayResult:
    original_path = Path(summary_path).resolve()
    original = read_json(original_path)
    contract_path = Path(str(original["contract_path"]))
    workspace = Path(str(original["workspace"]))
    loaded = load_contract(contract_path)
    hash_match = loaded.contract_hash == original.get("contract_hash")
    inspection = inspect_repository(workspace, loaded.contract.artifact)
    commit_match = inspection.commit_matches and (
        original.get("observed_commit") in (None, inspection.observed_commit)
    )
    current_commands = _recorded_commands(loaded.contract)
    commands_match = current_commands == original.get("recorded_commands")
    current_environment = capture_environment()
    drift = _environment_drift(original.get("environment", {}), current_environment)
    output_root = Path(receipt_root).resolve() if receipt_root else original_path.parent.parent
    comparison_path = original_path.parent / f"replay_comparison_{utc_now().replace(':', '')}.json"
    replay: RunResult | None = None
    if hash_match and commit_match and commands_match:
        replay = run_contract(
            contract_path,
            workspace=workspace,
            receipt_root=output_root,
            run_label=f"{loaded.contract.artifact.id}_replay",
        )
        stage_agreement = replay.observed_highest_stage == original.get(
            "observed_highest_stage"
        ) and replay.blocking_state == original.get("blocking_state")
        verdict = "REPLAY_AGREEMENT" if stage_agreement else "REPLAY_DIVERGENCE"
    else:
        stage_agreement = None
        verdict = "REPLAY_FAIL_CLOSED"
    result = ReplayResult(
        comparison_path=str(comparison_path),
        original_summary=str(original_path),
        replay_summary=replay.run_summary if replay else None,
        contract_hash_match=hash_match,
        commit_match=commit_match,
        commands_match=commands_match,
        stage_agreement=stage_agreement,
        environment_drift=drift,
        verdict=verdict,
    )
    comparison_path.write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result
