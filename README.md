# ArtifactFit-MM

ArtifactFit-MM is a deterministic preflight tool for asking one bounded engineering question:

> What is the highest stage this pinned research artifact demonstrably reaches on this target machine, under this immutable scientific contract and resource envelope?

It does not synthesize environments, repair scientific code, award artifact badges, or claim that paper results were reproduced.

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

