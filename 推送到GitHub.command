#!/bin/bash
# 双击：把本地仓库推送到 GitHub。第一次可能会要求登录 GitHub（按终端提示操作一次即可）。
cd "$HOME/SynologyDrive/智创未来AI大赛" || exit 1
echo "当前分支：$(git branch --show-current)   最近提交：$(git log -1 --pretty='%h %s')"
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "还没有设置 GitHub 远程仓库（origin）。让 Claude 设置，或手动执行：git remote add origin <仓库地址>"
  exit 1
fi
git push -u origin "$(git branch --show-current)" --tags
echo
echo "推送完成。"
