#!/bin/bash
set -e
cd /var/www/stock53-7
set -a
. ./.env
set +a
exec .venv/bin/uvicorn api.index:app --host 127.0.0.1 --port 8003
