#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

if [ -z "$VIRTUAL_ENV" ]; then
    source "$VENV_DIR/bin/activate"
fi

pip install -q -r requirements.txt

if [ "$1" = "--install-dcs" ]; then
    shift
    exec python install_dcs.py "$@"
fi

exec python main.py "$@"
