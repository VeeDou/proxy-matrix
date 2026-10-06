#!/bin/zsh
set -e
cd "${0:A:h}"
if ! command -v python3 >/dev/null; then
  echo '请先安装 Python 3。'
  exit 1
fi
python3 manage.py console
