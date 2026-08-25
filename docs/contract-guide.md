# Contract guide

An ArtifactFit-MM contract freezes four things before execution: artifact identity,
scientific invariants, permitted transformations, and the target resource envelope.
Unknown fields fail schema validation. Unknown transformations are never silently
accepted, and a contract is never rewritten by the runner.

Use `artifactfit validate examples/minimal_contract.yaml` to validate syntax and
semantics. Use `artifactfit plan ...` to inspect the state sequence, policy decision,
commands, and budgets without executing external code. Only `artifactfit run` launches
an artifact stage.

The working directory of every stage must remain within the pinned artifact workspace.
Both Windows and POSIX/WSL absolute or parent-traversal forms are rejected.

## Replay boundary

Replay verifies the canonical contract hash, repository commit, argv vectors, and
SHA-256 digests of directly referenced command files before execution. A mismatch is
`REPLAY_FAIL_CLOSED`; the old command is not executed. Environment differences are
reported separately rather than hidden.
