#!/usr/bin/env bash
# Build the three submission artifacts under submission/:
#   code.zip    the code/ directory (no caches, no dataset, no virtualenv)
#   output.csv  the final predictions from the repository root
#   log.txt     the development record (chat transcript)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p submission
rm -f submission/code.zip
zip -qr submission/code.zip code \
  -x 'code/__pycache__/*' 'code/*/__pycache__/*' 'code/*/*/__pycache__/*' 'code/.pytest_cache/*' 'code/tests/__pycache__/*'
cp output.csv submission/output.csv
cp log.txt submission/log.txt
echo "submission/ contents:"; ls -la submission
echo "zip entries: $(unzip -l submission/code.zip | tail -1)"
