# ArtifactFit-MM

ArtifactFit-MM is a deterministic preflight tool for asking one bounded engineering question:

> What is the highest stage this pinned research artifact demonstrably reaches on this target machine, under this immutable scientific contract and resource envelope?

It does not synthesize environments, repair scientific code, award artifact badges, or claim that paper results were reproduced.

## Status

This repository contains a public alpha vertical slice. It is source publication for
transparent review, not a tagged release, scientific experiment authorization, or paper
submission.

## Install from source

Python 3.11 is the primary supported runtime. With `uv` installed:

```text
git clone https://github.com/Rainflowers686/artifactfit-mm.git
cd artifactfit-mm
uv sync --extra dev --locked
uv run artifactfit doctor
```

Dependency resolution is the only step above that normally needs network access.

## Non-claims

Every receipt fixes these fields:

- `ENGINEERING_FEASIBILITY_ONLY: true`
- `SCIENTIFIC_RESULT: false`
- `PAPER_CLAIM_REPRODUCED: not_assessed`

## CLI

```text
artifactfit init PATH
artifactfit validate CONTRACT
artifactfit inspect CONTRACT
artifactfit plan CONTRACT
artifactfit run CONTRACT
artifactfit replay RECEIPT
artifactfit report RECEIPT
artifactfit doctor
```

`plan` never executes external code. Network access is disabled by contract default. Runtime enforcement is reported per metric as hard-enforced, soft-monitored or unavailable.

Receipts intentionally capture host, environment, repository and command metadata. Review
them before sharing: local paths, hostnames and operator-supplied environment overrides may
be sensitive. Never place credentials in a contract or stage environment.

Start with [the minimal contract](examples/minimal_contract.yaml), then read the
[contract guide](docs/contract-guide.md) and [enforcement limitations](docs/enforcement-limitations.md).

## Vertical-slice scope

Version 0.1 reports only the highest observed engineering stage. It does not repair an
environment, infer scientific invariants, replace ACM Artifact Evaluation, or assess
paper claims. Replay fails closed if a contract, commit, command, or directly referenced
command file changes.
