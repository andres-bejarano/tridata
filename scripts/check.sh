#!/usr/bin/env bash
# Same checks locally and in CI: lint (incl. security rules), types, tests,
# known-vulnerable dependencies. Needs `pip install -e ".[dev]"`.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff";      ruff check src tests
echo "== mypy";      mypy src
echo "== pytest";    pytest -q
# PYSEC-2026-3467: see .github/workflows/security.yml — drop once garminconnect >= 0.3.5 ships.
# PYSEC-2026-3447: setuptools sdist exclusion bypass, macOS-only and we don't build sdists.
echo "== pip-audit"; pip-audit --skip-editable --ignore-vuln PYSEC-2026-3467 --ignore-vuln PYSEC-2026-3447
