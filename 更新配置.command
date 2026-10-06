#!/bin/zsh
cd "${0:A:h}"
if ! command -v python3 >/dev/null; then
  echo '请先安装 Python 3，再运行此文件。'
  read '?按回车关闭此窗口。'
  exit 1
fi
if python3 manage.py apply; then
  echo ''
  echo '配置已更新。请在 Clash Verge 客户端点击该配置卡片重新选中，使最新配置生效。'
else
  echo ''
  echo '应用失败，请检查上方错误提示。'
fi
read '?按回车关闭此窗口。'
