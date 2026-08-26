# Security Policy

ArtifactFit-MM executes untrusted research commands only when an operator supplies an explicit contract. The current alpha is not a hardened sandbox. Use a disposable account, VM or container for untrusted artifacts. Never place credentials in contracts, stage environments, or receipts.

Network blocking, per-process GPU attribution and process-tree cleanup are reported at their observed enforcement level. A missing enforcement mechanism is a blocking or degraded result, never an implied guarantee.

Receipts may contain hostnames, absolute paths, commands, repository metadata, and
operator-supplied environment overrides. Treat them as potentially sensitive and audit them
before publication. Do not post vulnerabilities with private artifact content in a public
issue.
