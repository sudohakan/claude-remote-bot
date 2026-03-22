# Contributing

Contributions are welcome — bug fixes, features, tests, and documentation improvements.

---

## Development Setup

**Requirements:** Python 3.12+, Git

```bash
git clone https://github.com/sudohakan/claude-remote-bot.git
cd claude-remote-bot

# Install with dev dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_ID
```

---

## Running Tests

```bash
# Full test suite
pytest

# With coverage report
pytest --cov=src --cov-report=term-missing

# Single test file
pytest tests/test_auth.py -v
```

Test files are in `tests/`. The suite covers: auth, bot core, Claude bridge, config, monitor, storage, tunnel, and validators.

---

## Code Standards

This project enforces consistent style and type safety. All checks must pass before submitting a PR.

### Formatting

```bash
# Format code
black src tests

# Sort imports
isort src tests
```

Black is configured at line length 88 with `target-version = ["py312"]`. isort uses the `black` profile.

### Linting

```bash
flake8 src tests
```

### Type checking

```bash
mypy src
```

mypy is configured with `disallow_untyped_defs = true` and `warn_return_any = true`. All new functions must be fully annotated.

### Run all checks together

```bash
black src tests && isort src tests && flake8 src tests && mypy src && pytest
```

---

## Project Conventions

- **Async everywhere** — All I/O is `async/await`. Do not use blocking calls in handlers or service methods.
- **Pydantic settings** — Add new configuration values to `src/config/settings.py` with a validator, and document them in `.env.example`.
- **Event bus** — Use typed events on `EventBus` for cross-module communication. Do not import service modules from each other.
- **Repository pattern** — Database access goes through repository classes in `src/storage/`. Do not write SQL in handler code.
- **structlog** — Use `structlog.get_logger(__name__)` and pass context as keyword arguments, not string interpolation.
- **No silent failures** — Every `except` block must log the exception. Do not swallow errors.

---

## Pull Request Process

1. **Fork** the repository and create a branch from `master`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Write tests** for any new behaviour. Aim for coverage on the changed paths.

3. **Run all checks** locally (formatting, lint, type check, tests) before pushing.

4. **Open a PR** against `master`. Include:
   - What the change does and why
   - Any relevant issue numbers
   - Notes on testing approach

5. **Review** — A maintainer will review and may request changes. Address feedback in new commits, not force-pushes.

6. PRs are squash-merged once approved and CI passes.

---

## Commit Message Format

```
<type>: <short description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

Examples:
```
feat: add session export to HTML format
fix: handle ngrok restart race condition
docs: update configuration table in README
test: add coverage for cost tracker monthly reset
```

---

## Reporting Bugs

Open a [GitHub issue](https://github.com/sudohakan/claude-remote-bot/issues) with:
- Python version and OS
- Steps to reproduce
- Expected vs actual behaviour
- Relevant log output (redact any tokens or credentials)

For security vulnerabilities, see [SECURITY.md](SECURITY.md).
