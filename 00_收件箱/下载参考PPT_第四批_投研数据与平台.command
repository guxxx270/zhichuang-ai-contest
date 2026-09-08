#!/bin/bash
# 第四批（2026-09-08，围绕方案乙选题清单 v2 的 ①问数管家 / ②数据哨兵 / ③投研工具箱 / ④需求翻译官）：16 份 InfoQ 大会 PPT 直链下载到 02_参考PPT/
# 直链由 ppt.infoq.cn 的下载接口（/serv/ppt/download，参数 conid+pptid）返回，是公开静态文件链接，不需要登录。用完可删。
DEST="$HOME/SynologyDrive/智创未来AI大赛/02_参考PPT"
mkdir -p "$DEST"; cd "$DEST" || exit 1
ok=0; fail=0
dl() {
  local name="$1" url="$2"
  if [ -s "$name" ]; then echo "已存在，跳过：$name"; ok=$((ok+1)); return; fi
  echo "下载：$name"
  if curl -L --fail --retry 3 --retry-delay 2 -o "$name.part" "$url" && mv "$name.part" "$name"; then ok=$((ok+1)); else echo "  ✗ 失败：$name"; rm -f "$name.part"; fail=$((fail+1)); fi
}
# ---- ① 问数管家 / ② 数据哨兵：Data Agent、ChatBI、数据开发治理 ----
dl "2026-08_腾讯_DataBuddy数据语义驱动的企业Agent Runtime设计与落地.pdf" "https://static001.geekbang.org/con/172/pdf/326066490/file/%E5%BD%AD%E9%94%A6%E6%96%87-DataBuddy%EF%BC%9A%E6%95%B0%E6%8D%AE%E8%AF%AD%E4%B9%89%E9%A9%B1%E5%8A%A8%E7%9A%84%E4%BC%81%E4%B8%9A+Agent+Runtime+%E8%AE%BE%E8%AE%A1%E4%B8%8E%E8%90%BD%E5%9C%B0.pdf"
dl "2026-08_云器科技_从Tool Calling到Data Agent_企业数据智能体的生产化路径.pdf" "https://static001.geekbang.org/con/172/pdf/1300896794/file/%E5%B7%B4%E5%BF%97%E6%AC%A3-%E4%BB%8E+Tool+Calling+%E5%88%B0+Data+Agent%EF%BC%9A%E4%BC%81%E4%B8%9A%E6%95%B0%E6%8D%AE%E6%99%BA%E8%83%BD%E4%BD%93%E7%9A%84%E7%94%9F%E4%BA%A7%E5%8C%96%E8%B7%AF%E5%BE%84.pdf"
dl "2026-08_数新智能_基于DataCyber的Data Agent_构建企业数据智能中枢的工程范式.pdf" "https://static001.geekbang.org/con/172/pdf/1233148009/file/%E8%AE%B8%E9%94%A1%E5%BD%AC-%E5%9F%BA%E4%BA%8E+DataCyber+%E7%9A%84+Data+Agent%EF%BC%9A%E6%9E%84%E5%BB%BA%E4%BC%81%E4%B8%9A%E6%95%B0%E6%8D%AE%E6%99%BA%E8%83%BD%E4%B8%AD%E6%9E%A2%E7%9A%84%E5%B7%A5%E7%A8%8B%E8%8C%83%E5%BC%8F.pdf"
dl "2025-10_喜马拉雅_多智能体驱动的企业级ChatBI落地实践.pdf" "https://static001.geekbang.org/con/161/pdf/1357607038/file/%E9%99%88%E5%8F%B6%E8%B6%85-%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E9%A9%B1%E5%8A%A8%E7%9A%84%E4%BC%81%E4%B8%9A%E7%BA%A7ChatBI+%E8%90%BD%E5%9C%B0%E5%AE%9E%E8%B7%B5-Final.pdf"
dl "2026-04_网易数帆_从Copilot到DataAgent_企业级智能数据开发治理平台的技术演进和实践.pdf" "https://static001.geekbang.org/con/170/pdf/1399741158/file/%E6%9D%8E%E5%8D%93%E8%B1%AA-%E4%BB%8E+Copilot+%E5%88%B0+DataAgent%EF%BC%9A%E4%BC%81%E4%B8%9A%E7%BA%A7%E6%99%BA%E8%83%BD%E6%95%B0%E6%8D%AE%E5%BC%80%E5%8F%91%E6%B2%BB%E7%90%86%E5%B9%B3%E5%8F%B0%E7%9A%84%E6%8A%80%E6%9C%AF%E6%BC%94%E8%BF%9B%E5%92%8C%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-04_阿里云瓴羊_从数据到决策_AI驱动的Quick BI架构设计与实践.pdf" "https://static001.geekbang.org/con/156/pdf/1363078289/file/%E7%8E%8B%E7%92%9F%E5%B0%A7_%E8%84%B1%E6%95%8F_%E4%BB%8E%E6%95%B0%E6%8D%AE%E5%88%B0%E5%86%B3%E7%AD%96.pdf"
dl "2025-08_腾讯云_WeData Agent的落地思考与实践.pdf" "https://static001.geekbang.org/con/160/pdf/1321970829/file/%E8%99%8E%E5%85%B4%E9%BE%99-%E8%85%BE%E8%AE%AF%E4%BA%91wedata+agent%E7%9A%84%E6%80%9D%E8%80%83%E4%B8%8E%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-09_飞轮科技_构建AI-Ready的数据分析基础设施.pdf" "https://static001.geekbang.org/con/165/pdf/4203610179/file/%E6%9E%84%E5%BB%BA+AI-Ready+%E7%9A%84%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90%E5%9F%BA%E7%A1%80%E8%AE%BE%E6%96%BD.pdf"
dl "2025-04_腾讯_AI驱动的大数据自治_智能应对复杂运维挑战.pdf" "https://static001.geekbang.org/con/156/pdf/3466699390/file/%E7%86%8A%E8%AE%AD%E5%BE%B7-AI+%E9%A9%B1%E5%8A%A8%E7%9A%84%E5%A4%A7%E6%95%B0%E6%8D%AE%E8%87%AA%E6%B2%BB%EF%BC%9A%E6%99%BA%E8%83%BD%E5%BA%94%E5%AF%B9%E5%A4%8D%E6%9D%82%E8%BF%90%E7%BB%B4%E6%8C%91%E6%88%98.pdf"
# ---- ③ 投研工具箱：智能体平台、MCP、Agent Runtime ----
dl "2026-08_荣耀_从App容器到Agent调度中心_YOYO智能体平台的架构演进与技术实践.pdf" "https://static001.geekbang.org/con/172/pdf/2944119295/file/%E8%81%82%E9%B9%8F%E9%B9%A4-%E4%BB%8EApp%E5%AE%B9%E5%99%A8%E5%88%B0Agent%E8%B0%83%E5%BA%A6%E4%B8%AD%E5%BF%83%EF%BC%9A%E8%8D%A3%E8%80%80YOYO%E6%99%BA%E8%83%BD%E4%BD%93%E5%B9%B3%E5%8F%B0%E7%9A%84%E6%9E%B6%E6%9E%84%E6%BC%94%E8%BF%9B%E4%B8%8E%E6%8A%80%E6%9C%AF%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-04_淘宝闪购_从骑手智能助手业务落地到Agent平台化建设.pdf" "https://static001.geekbang.org/con/170/pdf/2548001428/file/%E6%9D%8E%E5%85%8B%E5%8D%8E-%E6%B7%98%E5%AE%9D%E9%97%AA%E8%B4%AD%EF%BC%9A%E4%BB%8E%E9%AA%91%E6%89%8B%E6%99%BA%E8%83%BD%E5%8A%A9%E6%89%8B%E4%B8%9A%E5%8A%A1%E8%90%BD%E5%9C%B0%E5%88%B0+Agent+%E5%B9%B3%E5%8F%B0%E5%8C%96%E5%BB%BA%E8%AE%BE.pdf"
dl "2024-12_钉钉_AI助理平台核心技术实践.pdf" "https://static001.geekbang.org/con/155/pdf/1425216663/file/1+%E6%9F%AF%E6%9D%B0++%E9%92%89%E9%92%89+AI+%E5%8A%A9%E7%90%86%E5%B9%B3%E5%8F%B0%E6%A0%B8%E5%BF%83%E6%8A%80%E6%9C%AF%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-06_ANP社区_深入对比智能体协议_MCP A2A ANP.pdf" "https://static001.geekbang.org/con/159/pdf/2208216715/file/%E5%B8%B8%E9%AB%98%E4%BC%9F-%E6%B7%B1%E5%85%A5%E5%AF%B9%E6%AF%94%E6%99%BA%E8%83%BD%E4%BD%93%E5%8D%8F%E8%AE%AE%EF%BC%9AMCP%E3%80%81A2A%E3%80%81ANP.pdf"
dl "2025-05_陈仲寅_打造可扩展的生态体系_从MCP到Agent集成的实践与趋势.pdf" "https://static001.geekbang.org/con/158/pdf/3381388996/file/AICon%E4%B8%8A%E6%B5%B7%E7%AB%99-%E9%99%88%E4%BB%B2%E5%AF%85+3.pdf"
# ---- ④ 需求翻译官：需求 → 原型 → 代码 ----
dl "2025-10_小红书_客户端AI Coding实践_从PRD到代码直出.pdf" "https://static001.geekbang.org/con/161/pdf/1185738319/file/%E7%8E%8B%E5%85%89%E6%99%AF-%E5%AE%A2%E6%88%B7%E7%AB%AF+AI+Coding+%E5%AE%9E%E8%B7%B5%EF%BC%9A%E4%BB%8E+PRD+%E5%88%B0%E4%BB%A3%E7%A0%81%E7%9B%B4%E5%87%BA.pdf"
dl "2024-08_路宁_大模型辅助需求代码开发.pdf" "https://static001.geekbang.org/con/153/pdf/1572877265/file/%E5%A4%A7%E6%A8%A1%E5%9E%8B%E8%BE%85%E5%8A%A9%E9%9C%80%E6%B1%82%E4%BB%A3%E7%A0%81%E5%BC%80%E5%8F%91-%E8%B7%AF%E5%AE%81.pdf"
echo; echo "完成：成功 $ok 份，失败 $fail 份。文件在：$DEST"
echo "回到 Claude 说一声「第四批下好了」即可。"
