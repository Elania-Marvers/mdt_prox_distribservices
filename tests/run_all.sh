#!/usr/bin/env bash
# Lance les trois séries de tests, l'une après l'autre (elles ont chacune leur propre conftest.py).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== serveur ==";   (cd server && python3 -m pytest -q)
echo "== client ==";    (cd client && python3 -m pytest -q)
echo "== bout en bout =="; python3 -m pytest tests -q
