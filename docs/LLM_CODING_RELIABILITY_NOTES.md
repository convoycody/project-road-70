# LLM Coding Reliability Notes (for maintainers)

This project uses AI-assisted coding at times; common observed failure modes include:
- Hallucinating files/paths/endpoints that do not exist
- Dropping required constraints during iteration (e.g., privacy rules)
- Inconsistent variable names and “half-applied” refactors
- Forgetting auth layers (proxy vs app auth) and mis-attributing 401s
- Assuming a command works without verifying exit codes/logs

Mitigations we require:
- Make changes in small increments and verify with curl + journalctl
- Keep a single source of truth for constraints (PROJECT_CHARTER.md)
- Snapshot infra state and commit redacted configs regularly
