#!/usr/bin/env bash
# Set up the Home Assistant test harness locally, mirroring CI.
# Creates .venv with Python 3.14 and installs requirements_test.txt.
# Requires: uv (https://docs.astral.sh/uv/getting-started/installation/)
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install it first:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

uv python install 3.14.2
uv venv --python 3.14.2 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt

echo "Done. Run the tests with:"
echo "  .venv/bin/python -m pytest tests/ -q"
