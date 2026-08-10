# Development Setup for Home Assistant Config Tools

This directory contains a complete Python development environment with modern tooling for code quality, formatting, and testing.

## Quick Start

1. **Setup development environment:**
   ```bash
   make -f Makefile.dev dev-setup
   ```

2. **Format and check code:**
   ```bash
   make -f Makefile.dev dev-format
   make -f Makefile.dev dev-lint
   ```

3. **Run tests:**
   ```bash
   make -f Makefile.dev dev-test
   ```

## Development Tools Included

### Code Quality
- **Ruff** - Fast linter and formatter (replaces Black, isort, flake8, pylint)
- **mypy** - Static type checking

### Testing
- **pytest** - Modern testing framework
- **pytest-cov** - Coverage reporting
- **pytest-mock** - Mocking utilities

### Automation
- **pre-commit** - Git hooks for automated checks
- **pre-commit-hooks** - YAML syntax, whitespace, and repository hygiene checks

## File Structure

```
├── tools/                   # Python validation scripts
├── tests/                   # Test files
├── pyproject.toml           # Python project configuration
├── .pre-commit-config.yaml  # Pre-commit hook configuration
├── Makefile                 # Main project commands
├── Makefile.dev             # Development-specific commands
└── README-DEV.md            # This file
```

> **Runtime directories** (gitignored, created by setup commands):
> - `config/` — HA configuration, created by `make pull`
> - `.venv/` — Python virtual environment, created by `make setup`

## Available Commands

### Development Workflow
```bash
# Complete development setup
make -f Makefile.dev dev-setup

# Format code automatically
make -f Makefile.dev dev-format

# Run all code quality checks
make -f Makefile.dev dev-check-all

# Run tests with coverage
make -f Makefile.dev dev-test-coverage

# Full development workflow
make -f Makefile.dev dev-workflow
```

### Pre-commit Hooks
```bash
# Install git hooks
make -f Makefile.dev dev-pre-commit

# Run hooks on all files
make -f Makefile.dev dev-pre-commit-all
```

### Maintenance
```bash
# Update dependencies
make -f Makefile.dev dev-update-deps

# Clean development artifacts
make -f Makefile.dev dev-clean-dev
```

## Configuration Details

### Ruff (Formatting & Linting)
- Line length: 88 characters
- Target Python version: 3.14.2 (project minimum)
- Lint rules: pycodestyle (E/W), pyflakes (F), isort (I), pyupgrade (UP), bugbear (B), simplify (SIM)
- Import sorting: first-party = `tools`
- Config: `pyproject.toml` under `[tool.ruff]`

### mypy (Type Checking)
- Relaxed settings for existing codebase (not strict mode)
- Ignores missing imports for homeassistant/voluptuous
- Excludes venv and build directories

### pytest (Testing)
- Coverage target: 90% minimum
- HTML coverage reports generated
- Test discovery in `tests/` directory
- Shared fixtures in `tests/conftest.py` (auto-use `_stub_load_env_file`, `config_dir`)

## Integration with Home Assistant Tools

This development setup works seamlessly with the existing Home Assistant validation tools:

- **YAML Validation**: Pre-commit's `check-yaml --unsafe` hook accepts Home Assistant-specific YAML tags
- **Full Validator Suite**: `make validate` runs 7 validators (YAML syntax, entity/device/area references, duplicate automation IDs, service references, Jinja2 templates, stale sensors, and official HA `check_config`)
- **Official HA Validation**: Integrated with Home Assistant's own validators

## Pre-commit Hooks

Automatically runs on git commits:
- Trailing whitespace removal
- End-of-file fixing
- YAML syntax checking (HA-compatible)
- Code formatting and linting (Ruff)
- Type checking (mypy)
- Spell checking (codespell)

## Tips for Development

1. **Always format before committing:**
   ```bash
   make -f Makefile.dev dev-format
   ```

2. **Run the full workflow periodically:**
   ```bash
   make -f Makefile.dev dev-workflow
   ```

3. **Use coverage reports to identify untested code:**
   ```bash
   make -f Makefile.dev dev-test-coverage
   # macOS; use a browser or file manager on other platforms
   open htmlcov/index.html
   ```

4. **Pre-commit hooks catch issues early:**
   ```bash
   git add . && git commit -m "your changes"
   # Hooks run automatically
   ```

## Troubleshooting

### Dependency Issues
```bash
# Update all development dependencies
make -f Makefile.dev dev-update-deps

# Clean and reinstall
make -f Makefile.dev dev-clean-dev
make -f Makefile.dev dev-install
```

### Pre-commit Issues
```bash
# Reinstall hooks
make -f Makefile.dev dev-pre-commit

# Skip hooks temporarily (not recommended)
git commit --no-verify -m "message"
```

This setup ensures consistent, high-quality Python code that integrates perfectly with the Home Assistant configuration management workflow.
