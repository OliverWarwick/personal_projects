Copilot Instructions
====================

Purpose
-------
This file is intended to be read on project startup by developer tooling and assistants. It tells the reader to consult the repository coding guidelines in `CLAUDE.md` first, and provides repository-specific operational commands and safety notes.

Required first step
-------------------
1. Read `CLAUDE.md` in the repository root. It contains the authoritative coding style, linting, testing, and commit rules that must be followed for any change.

What this file contains (short)
-------------------------------
- Operational commands and shortcuts to run linters, type checks, tests, and capture network data.
- Instructions for running the optional integration test locally.
- Security & safety reminders (do not commit sensitive captures/credentials).

Operational shortcuts and commands
----------------------------------
- Run linters and type checks (local):

```bash
uv run ruff check .
uv run pyright src tests
```

- Run unit tests (local):

```bash
PYTHONPATH=src uv run pytest
```

- Capture network HAR and page HTML for discovery (local, headful):

```bash
python3 src/personal_project/scripts/capture_better.py https://better.com
# outputs saved under: personal_project/data/better_com_capture/
```

- Run the optional live integration test (local only; requires credentials in keyring or env vars):

```bash
RUN_BETTER_INTEGRATION=1 PYTHONPATH=src python3 -m pytest tests/integration/test_better_integration.py -q
```

Security & safety
-----------------
- HAR files, screenshots, and Playwright storage_state may contain secrets. Treat them as sensitive and do not check them into git.
- If a HAR contains credentials or tokens, rotate them and delete the captured files.

If you are an automated assistant (Copilot)
-----------------------------------------
- On startup: read this file, then immediately read `CLAUDE.md` and follow it.
- Use the commands above for operational tasks; prefer programmatic endpoints over browser automation; use Playwright only as a fallback.
- Always ask the user before performing irreversible actions (e.g., booking a court).

Checklist for agents
--------------------
- [ ] Read `CLAUDE.md` and this file.
- [ ] Use the capture script to produce HAR/HTML when necessary and share them before implementing endpoint-specific code.
- [ ] Use `keyring` for credentials; do not store plaintext credentials in the repo.
- [ ] Keep integration tests opt-in and never run them in CI by default.

Thank you — `CLAUDE.md` is the source of truth for coding rules; follow it first, then use this file for operational steps.
