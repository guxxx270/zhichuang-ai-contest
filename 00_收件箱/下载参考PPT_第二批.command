#!/bin/bash
# 第二批（2026-09-07，围绕"个人常用型投研 Agent"方向）：8 份 InfoQ 大会 PPT 直链下载到 02_参考PPT/
DEST="$HOME/SynologyDrive/智创未来AI大赛/02_参考PPT"
mkdir -p "$DEST"; cd "$DEST" || exit 1
ok=0; fail=0
dl() {
  local name="$1" url="$2"
  if [ -s "$name" ]; then echo "已存在，跳过：$name"; ok=$((ok+1)); return; fi
  echo "下载：$name"
  if curl -L --fail --retry 3 --retry-delay 2 -o "$name.part" "$url" && mv "$name.part" "$name"; then ok=$((ok+1)); else echo "  ✗ 失败：$name"; rm -f "$name.part"; fail=$((fail+1)); fi
}
dl "2026-06_OPPO_小布记忆_全模态碎片化内容的理解与智能整理.pdf" "https://static001.geekbang.org/con/171/pdf/1899389521/file/%E5%B0%8F%E5%B8%83%E8%AE%B0%E5%BF%86%EF%BC%9A%E5%85%A8%E6%A8%A1%E6%80%81%E7%A2%8E%E7%89%87%E5%8C%96%E5%86%85%E5%AE%B9%E7%9A%84%E7%90%86%E8%A7%A3%E4%B8%8E%E6%99%BA%E8%83%BD%E6%95%B4%E7%90%86%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-12_OPPO_小布智能助手_从多智能体到Agentic Model的个性化深度研究.pdf" "https://static001.geekbang.org/con/162/pdf/3301221346/file/%E6%9D%A8%E4%BF%8A-%E5%B0%8F%E5%B8%83%E6%99%BA%E8%83%BD%E5%8A%A9%E6%89%8B.pdf"
dl "2026-06_阿里巴巴_QoderWork桌面Agent的设计密码与效能突破.pdf" "https://static001.geekbang.org/con/171/pdf/2702442760/file/2-%E9%87%8D%E5%A1%91%E6%9C%AC%E5%9C%B0%E7%94%9F%E4%BA%A7%E5%8A%9B_QoderWork_%E6%A1%8C%E9%9D%A2_Agent_%E7%9A%84%E8%AE%BE%E8%AE%A1%E5%AF%86%E7%A0%81%E4%B8%8E%E6%95%88%E8%83%BD%E7%AA%81%E7%A0%B4_%E8%B5%B5%E6%98%8E.pdf"
dl "2025-08_众安银行_AI时代超级个体的做事方法论.pdf" "https://static001.geekbang.org/con/160/pdf/809094423/file/%E6%B2%88%E6%96%8C_AI+%E6%97%B6%E4%BB%A3%E8%B6%85%E7%BA%A7%E4%B8%AA%E4%BD%93%E7%9A%84%E5%81%9A%E4%BA%8B%E6%96%B9%E6%B3%95%E8%AE%BA.pdf"
dl "2025-05_蚂蚁集团_CIO智能转型助力业务提效的实践.pdf" "https://static001.geekbang.org/con/158/pdf/1773958093/file/%E8%9A%82%E8%9A%81%E9%9B%86%E5%9B%A2CIO%E6%99%BA%E8%83%BD%E8%BD%AC%E5%9E%8B%E5%8A%A9%E5%8A%9B%E4%B8%9A%E5%8A%A1%E6%8F%90%E6%95%88%E7%9A%84%E5%AE%9E%E8%B7%B5-%E6%9D%A8%E6%B5%A9.pdf"
dl "2025-12_腾讯_面向Skills的上下文工程与CodeBuddy Spec-Coding.pdf" "https://static001.geekbang.org/con/162/pdf/4144134875/file/%E6%B1%AA%E6%99%9F%E6%9D%B0-CodeBuddy%E9%9D%A2%E5%90%91Skills%E7%9A%84%E4%B8%8A%E4%B8%8B%E6%96%87%E5%B7%A5%E7%A8%8B.pdf"
dl "2026-04_质变科技_构建AI的第二大脑_大规模多模态记忆平台.pdf" "https://static001.geekbang.org/con/170/pdf/440030912/file/%E5%91%A8%E7%A5%A5-%E6%9E%84%E5%BB%BA+AI+%E7%9A%84%26ldquo%3B%E7%AC%AC%E4%BA%8C%E5%A4%A7%E8%84%91%26rdquo%3B%EF%BC%9A%E5%A4%A7%E8%A7%84%E6%A8%A1%E5%A4%9A%E6%A8%A1%E6%80%81%E8%AE%B0%E5%BF%86%E5%B9%B3%E5%8F%B0%E6%8A%80%E6%9C%AF%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-12_飞书_多维表格的产品演进_从表格到AI时代的智能业务底座.pdf" "https://static001.geekbang.org/con/162/pdf/1340395862/file/1-%E6%96%BD%E5%87%AF%E6%96%87-%E9%A3%9E%E4%B9%A6%E5%A4%9A%E7%BB%B4%E8%A1%A8%E6%A0%BC%E7%9A%84%E4%BA%A7%E5%93%81%E6%BC%94%E8%BF%9B%E6%80%9D%E8%80%83%EF%BC%9A%E4%BB%8E%E8%A1%A8%E6%A0%BC%E5%88%B0+AI+%E6%97%B6%E4%BB%A3%E7%9A%84%E6%99%BA%E8%83%BD%E4%B8%9A%E5%8A%A1%E5%BA%95%E5%BA%A7.pdf"
echo; echo "完成：成功 $ok 份，失败 $fail 份。文件在：$DEST"
