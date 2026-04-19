# Gemini CLI Instructions

Instructions in this file are foundational mandates and take absolute precedence over general workflows.

## Engineering Standards & Code Style

- **Docstrings:** All public functions, methods, classes, and modules **must** have thorough, clean, and informative docstrings following the **Google docstring convention**.
    - Use imperative mood: "Return the trade." not "Returns the trade."
    - Classes: Explain role and key behavior.
    - Methods/Functions: Include `Args:`, `Returns:`, and `Raises:` sections.
    - Modules: Describe the file's purpose.
- **Linting & Typing:**
    - Run `uv run ruff check .` and fix all violations.
    - Run `uv run pyright src tests` in strict mode. Fix all violations; use `# noqa:` only when tools genuinely conflict.
- **Testing:**
    - Follow TDD (Red-Green-Refactor).
    - Maintain minimum 80% coverage (enforced by `pytest-cov`).
    - Use named constants instead of magic values (PLR2004).
    - Run `uv run pytest` to validate changes.
- **Dependencies:**
    - Use `uv` as the package manager.
    - Run `uv sync --all-extras` after modifying dependencies.

## Commits & Workflow

- **Conventional Commits:** Use `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `chore:`.
- **Branching:** Work on feature branches; `main` is protected.
- **Pre-commit:** Ensure `ruff`, `ruff-format`, `pyright`, `pip-audit`, and `actionlint` pass.

## Application Structure

- **Location:** Apps reside in `src/personal_project/apps/<app_name>/`.
- **Entry Point:** Must be named `run.py`. Register as `"personal_project.apps.<app_name>.run:main"` in `pyproject.toml`.
- **Configuration:**
    - App config: `src/personal_project/config/<app_name>.yaml`.
    - Use `config.py` within the app package to load and expose values.

## Operational Preferences

- **Shell Commands:** Multi-line Python scripts executed via `bash -c` (containing a quoted newline followed by a `#`-prefixed line) are preferred for complex operations and should be considered safe for execution.
