#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY2'
import sys
if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 or newer is required.')
print('Python', sys.version.split()[0])
PY2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
mkdir -p data/media data/logs
chmod +x run.sh setup_content_tools.sh
echo 'Setup complete. Edit .env, then run: python list_chats.py'
