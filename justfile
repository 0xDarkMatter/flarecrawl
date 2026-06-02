# Flarecrawl development task runner.
#
# `just` (https://github.com/casey/just) is preferred over Makefiles —
# tab-vs-space free, predictable variable expansion, native Windows
# support.

set windows-shell := ["bash", "-cu"]

default:
    @just --list

# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------

# Install the pre-commit hook (one-time per clone)
install-hooks:
    git config core.hooksPath .githooks
    @echo "✓ Hooks configured. .githooks/ is now active."

# Install all dev / test extras (per pmail #205)
sync:
    uv sync --extra dev --extra perf --extra recipes --extra cdp \
        --extra local-browser --extra secure --extra stealth
    uv pip install pytest-asyncio

# ----------------------------------------------------------------------
# Quality
# ----------------------------------------------------------------------

# Run the unit test suite (excludes live tests)
test:
    uv run pytest tests/ --ignore=tests/live -q

# Run the live test suite (needs CF auth + network)
test-live:
    uv run pytest tests/live/ -v -m live

# Lint
lint:
    uv run ruff check src/

# Format
format:
    uv run ruff format src/ tests/

# ----------------------------------------------------------------------
# Release
# ----------------------------------------------------------------------

# Pre-tag local validation: build the wheel and smoke-install it.
# Catches the hatchling duplicate-file bug, missing manifest entries,
# and any "wheel builds but won't run" failure mode BEFORE the tag is
# pushed. v0.30.0 and v0.30.1 both shipped broken because this step
# wasn't run locally before tagging.
release-check:
    @echo "── Cleaning previous build artifacts ──"
    rm -rf dist/ build/
    @echo "── Building wheel ──"
    uv build --wheel
    @echo "── Validating wheel contents ──"
    uv run python -c "import zipfile; \
        whl = next(__import__('pathlib').Path('dist').glob('flarecrawl-*-py3-none-any.whl')); \
        z = zipfile.ZipFile(whl); n = z.namelist(); \
        assert any('AGENTS.md' in f for f in n), 'AGENTS.md missing'; \
        assert any('py.typed' in f for f in n), 'py.typed missing'; \
        assert sum(1 for f in n if 'wappalyzer_data' in f) >= 20, 'wappalyzer_data missing'; \
        assert any('LICENSE.wappalyzer_data' in f for f in n), 'LICENSE.wappalyzer_data missing'; \
        print(f'✓ {whl.name}: {len(n)} files, all required artifacts present')"
    @echo "── Smoke-installing wheel into a throwaway venv ──"
    rm -rf .release-check-venv
    uv venv .release-check-venv
    uv pip install --python .release-check-venv dist/flarecrawl-*-py3-none-any.whl
    @echo "── Running --version against installed wheel ──"
    .release-check-venv/Scripts/flarecrawl --version 2>/dev/null || .release-check-venv/bin/flarecrawl --version
    @echo "── Running guide (proves AGENTS.md ships in the wheel) ──"
    .release-check-venv/Scripts/flarecrawl guide --list 2>/dev/null | head -5 || .release-check-venv/bin/flarecrawl guide --list | head -5
    rm -rf .release-check-venv
    @echo ""
    @echo "✓ release-check PASSED. Wheel builds, installs, and runs."
    @echo "  Safe to tag and push."

# Full pre-release gate: lint + test + release-check
release-ready: lint test release-check
    @echo ""
    @echo "✓ ALL CHECKS PASSED. Ready to bump version, commit, tag, push."
