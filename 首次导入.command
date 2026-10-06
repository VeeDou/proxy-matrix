#!/bin/zsh
set -e
cd "${0:A:h}"
if ! command -v python3 >/dev/null; then
  echo '请先安装 Python 3，再运行此文件。'
  exit 1
fi
python3 manage.py check
open -R "$PWD/dist/Clash-Verge-Rev.yaml"
echo '将选中的 Clash-Verge-Rev.yaml 文件拖入 Clash Verge 的 Profiles 页面，然后选中即可。'
read '?按回车关闭此窗口。'
