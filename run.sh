#!/usr/bin/env bash
# Fetch data (if missing), build the database (if missing) and serve the app.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -f data/raw/manifest.json ] || .venv/bin/python -m ingest.fetch_opencity
[ -f data/raw/kppp/search.json ] || .venv/bin/python -m ingest.fetch_kppp
[ -d data/raw/kppp_docs ] || .venv/bin/python -m ingest.fetch_kppp_docs   # tender documents + comparative statements (~240 MB)
[ -f data/koramangala.db ] || .venv/bin/python -m ingest.build
exec .venv/bin/python -m app.app
