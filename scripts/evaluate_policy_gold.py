from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from artifactfit_mm.contracts.models import ArtifactContract
from artifactfit_mm.policies.engine import evaluate_transforms

CLASSES = ("SAFE", "REVIEW_REQUIRED", "CONTRACT_VIOLATION")


def _contract(request: dict[str, Any]) -> ArtifactContract:
    internal = {
        "enabled": True,
        "command": ["artifactfit:internal"],
        "expected_outputs": [],
        "timeout": 1,
        "success_predicates": [{"kind": "exit_code_zero"}],
    }
    return ArtifactContract.model_validate(
        {
            "schema_version": "1.0.0",
            "artifact": {
                "id": "gold-standard-policy-evaluation",
                "repository": "local://policy-gold",
                "commit": "fixturegold1",
                "license_policy": {"allowed_spdx": ["MIT"]},
                "official_entrypoints": ["none"],
                "declared_framework": "taxonomy-only",
            },
            "target": {
                "platform": "windows",
                "gpu_count": 0,
                "max_vram_gb": 0,
                "max_system_ram_gb": 1,
                "max_wall_time": 1,
                "max_workspace_growth_gb": 0,
                "max_download_gb": 0,
            },
            "declared_invariants": {
                "task": "frozen",
                "modalities": ["frozen"],
                "model_family": "frozen",
                "backbone_constraints": ["frozen"],
                "input_constraints": ["frozen"],
                "dataset_semantics": "frozen",
                "evaluation_protocol": "frozen",
                "objective": "frozen",
            },
            "transforms": {
                "allowed": [],
                "review_required": [],
                "forbidden": [],
                "requested": [request],
            },
            "stages": {"P0": internal, "P1": internal},
            "evidence": {"files_to_hash": []},
        }
    )


def evaluate(gold_path: Path, output_directory: Path) -> dict[str, Any]:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if gold.get("frozen_before_policy_evaluation") is not True:
        raise ValueError("gold standard is not marked frozen before evaluation")
    predictions: list[dict[str, Any]] = []
    matrix = Counter[tuple[str, str]]()
    correct = 0
    violation_false_negatives = 0
    for case in gold["cases"]:
        request = {
            "path": case["path"],
            "original": case["original_value"],
            "proposed": case["proposed_value"],
        }
        policy = evaluate_transforms(_contract(request))
        decision = policy.decisions[0]
        expected = str(case["expected_class"])
        predicted = policy.final_class.value
        is_correct = expected == predicted
        correct += int(is_correct)
        violation_false_negatives += int(
            expected == "CONTRACT_VIOLATION" and predicted != "CONTRACT_VIOLATION"
        )
        matrix[(expected, predicted)] += 1
        predictions.append(
            {
                "Case_ID": case["Case_ID"],
                "path": case["path"],
                "expected": expected,
                "predicted": predicted,
                "correct": is_correct,
                "matched_rule": decision.rule,
                "engine_rationale": decision.rationale,
            }
        )
    count = len(predictions)
    metrics = {
        "FEASIBILITY_ONLY": True,
        "SCIENTIFIC_RESULT": False,
        "gold_standard_path": str(gold_path.resolve()),
        "gold_standard_version": gold["gold_standard_version"],
        "case_count": count,
        "correct": correct,
        "accuracy": correct / count if count else 0.0,
        "contract_violation_false_negatives": violation_false_negatives,
        "gate_accuracy_pass": correct / count >= 0.9 if count else False,
        "gate_violation_false_negative_pass": violation_false_negatives == 0,
        "confusion_matrix": {
            expected: {predicted: matrix[(expected, predicted)] for predicted in CLASSES}
            for expected in CLASSES
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "policy_predictions.json").write_text(
        json.dumps(predictions, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_directory / "policy_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_directory / "confusion_matrix.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["expected\\predicted", *CLASSES])
        for expected in CLASSES:
            writer.writerow([expected, *(matrix[(expected, predicted)] for predicted in CLASSES)])
    rows = [
        "# Frozen gold-standard policy evaluation",
        "",
        f"- Cases: {count}",
        f"- Accuracy: {metrics['accuracy']:.4f}",
        f"- CONTRACT_VIOLATION false negatives: {violation_false_negatives}",
        f"- Accuracy gate: {'PASS' if metrics['gate_accuracy_pass'] else 'FAIL'}",
        f"- Violation false-negative gate: {'PASS' if metrics['gate_violation_false_negative_pass'] else 'FAIL'}",
        "",
        "This evaluates agreement with the frozen first-version taxonomy. It does not establish universal scientific-semantic equivalence.",
    ]
    (output_directory / "policy_evaluation.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("gold", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.gold, args.output_directory), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
