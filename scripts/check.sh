#!/usr/bin/env bash
# Same checks locally and in CI: lint (incl. security rules), types, tests,
# leaked secrets, known-vulnerable dependencies. Needs `pip install -e ".[dev]"`
# and gitleaks (see AGENTS.md). Each command lives in scripts/ci/.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/ci/lint
scripts/ci/full-suite
scripts/ci/security
