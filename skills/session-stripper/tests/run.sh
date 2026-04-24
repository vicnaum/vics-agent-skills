#!/usr/bin/env bash
# One-button test runner. Cwd-independent.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m unittest discover tests -v "$@"
