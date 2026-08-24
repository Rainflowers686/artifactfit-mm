from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from artifactfit_mm.contracts.models import ArtifactSpec

LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt")


@dataclass(frozen=True, slots=True)
class RepositoryInspection:
    workspace: str
    exists: bool
    repository_kind: str
    observed_commit: str | None
    commit_matches: bool
    license_file: str | None
    detected_spdx: str | None
    license_accepted: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "exists": self.exists,
            "repository_kind": self.repository_kind,
            "observed_commit": self.observed_commit,
            "commit_matches": self.commit_matches,
            "license_file": self.license_file,
            "detected_spdx": self.detected_spdx,
            "license_accepted": self.license_accepted,
            "reason": self.reason,
        }


def _git_head(workspace: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _detect_license(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text.casefold())
    if "mit license" in normalized and "permission is hereby granted" in normalized:
        return "MIT"
    if "apache license" in normalized and "version 2.0" in normalized:
        return "Apache-2.0"
    if "gnu general public license" in normalized and "version 3" in normalized:
        return "GPL-3.0-only"
    if "bsd 3-clause" in normalized or (
        "redistribution and use" in normalized and "neither the name" in normalized
    ):
        return "BSD-3-Clause"
    return None


def inspect_repository(workspace: Path, artifact: ArtifactSpec) -> RepositoryInspection:
    resolved = workspace.resolve()
    if not resolved.is_dir():
        return RepositoryInspection(
            workspace=str(resolved),
            exists=False,
            repository_kind="missing",
            observed_commit=None,
            commit_matches=False,
            license_file=None,
            detected_spdx=None,
            license_accepted=False,
            reason="workspace directory does not exist",
        )
    git_head = _git_head(resolved) if (resolved / ".git").exists() else None
    if git_head:
        repository_kind = "git"
        commit_matches = git_head.casefold() == artifact.commit.casefold()
    else:
        repository_kind = (
            "local_fixture" if artifact.repository.startswith("local://") else "non_git"
        )
        commit_matches = repository_kind == "local_fixture" and artifact.commit.startswith(
            "fixture"
        )
    license_path = next(
        (resolved / name for name in LICENSE_NAMES if (resolved / name).is_file()),
        None,
    )
    detected = None
    if license_path is not None:
        try:
            detected = _detect_license(license_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            detected = None
    policy = artifact.license_policy
    if license_path is None and policy.require_license_file:
        accepted = False
        reason = "required license file is absent"
    elif detected is None and not policy.allow_unknown:
        accepted = False
        reason = "license could not be mapped to an allowed SPDX identifier"
    elif detected is not None and detected not in policy.allowed_spdx:
        accepted = False
        reason = f"detected license {detected} is outside allowed_spdx"
    else:
        accepted = True
        reason = "license policy accepted"
    return RepositoryInspection(
        workspace=str(resolved),
        exists=True,
        repository_kind=repository_kind,
        observed_commit=git_head,
        commit_matches=commit_matches,
        license_file=str(license_path) if license_path else None,
        detected_spdx=detected,
        license_accepted=accepted,
        reason=reason,
    )
