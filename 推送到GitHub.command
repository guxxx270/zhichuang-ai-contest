#!/bin/bash
# 双击：把本地仓库推送到 GitHub。
# 第一次会要求登录：Username 填 GitHub 用户名（guxxx270），Password 处粘贴 Personal Access Token（不是账号密码）。
# 登录一次后 macOS 钥匙串会记住，以后双击即推。
cd "$HOME/SynologyDrive/智创未来AI大赛" || exit 1
echo "当前分支：$(git branch --show-current)   最近提交：$(git log -1 --pretty='%h %s')"
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "还没有设置 GitHub 远程仓库（origin）。让 Claude 设置，或手动执行：git remote add origin <仓库地址>"
  exit 1
fi
echo
if git push -u origin "$(git branch --show-current)" --tags; then
  echo
  echo "✅ 推送完成：$(git remote get-url origin)"
else
  echo
  echo "❌ 推送失败。常见原因："
  echo "   1) Password 处填了账号密码 —— GitHub 只接受 Personal Access Token（github.com → Settings → Developer settings → Personal access tokens，勾 repo）"
  echo "   2) Username 不是仓库所属账号（本仓库属于 guxxx270）"
  echo "   3) 网络不通"
  exit 1
fi
