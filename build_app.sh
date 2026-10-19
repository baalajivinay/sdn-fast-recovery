#!/usr/bin/env bash
# Build the SDN Recovery Simulator as a standalone executable.
#
#   ./build_app.sh          # Linux executable  -> dist/sdn_app
#   ./build_app.sh --exe    # Windows cross-compile hint
#
# Prerequisites:
#   pip install pywebview pyinstaller

set -e
cd "$(dirname "$0")"

echo "=== Installing build dependencies ==="
pip install --quiet pywebview pyinstaller 2>/dev/null || pip3 install --quiet pywebview pyinstaller

echo "=== Building executable ==="
pyinstaller \
  --name "SDN_Recovery_Simulator" \
  --onefile \
  --windowed \
  --add-data "app/index.html:app" \
  --hidden-import webview \
  --collect-submodules jaraco \
  --noconfirm \
  sdn_app.py

echo ""
echo "=== Done ==="
echo "Executable: dist/SDN_Recovery_Simulator"
echo ""
echo "To run:  ./dist/SDN_Recovery_Simulator"
