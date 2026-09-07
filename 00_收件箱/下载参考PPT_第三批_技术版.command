#!/bin/bash
# 第三批（2026-09-07，围绕方案乙「变更守门员」·技术类应用）：13 份 InfoQ 大会 PPT 直链下载到 02_参考PPT/
# 直链由 ppt.infoq.cn 的下载接口（/serv/ppt/download）返回，是公开静态文件链接，不需要登录。用完可删。
DEST="$HOME/SynologyDrive/智创未来AI大赛/02_参考PPT"
mkdir -p "$DEST"; cd "$DEST" || exit 1
ok=0; fail=0
dl() {
  local name="$1" url="$2"
  if [ -s "$name" ]; then echo "已存在，跳过：$name"; ok=$((ok+1)); return; fi
  echo "下载：$name"
  if curl -L --fail --retry 3 --retry-delay 2 -o "$name.part" "$url" && mv "$name.part" "$name"; then ok=$((ok+1)); else echo "  ✗ 失败：$name"; rm -f "$name.part"; fail=$((fail+1)); fi
}
dl "2025-10_哔哩哔哩_AI CodeReview实践_代码变更阶段的风险识别与阻断.pdf" "https://static001.geekbang.org/con/161/pdf/2323328539/file/%E4%B8%A5%E5%AE%BD-AI+CodeReview+%E5%AE%9E%E8%B7%B5%EF%BC%9A%E4%BB%A3%E7%A0%81%E5%8F%98%E6%9B%B4%E9%98%B6%E6%AE%B5%E7%9A%84%E9%A3%8E%E9%99%A9%E8%AF%86%E5%88%AB%E4%B8%8E%E9%98%BB%E6%96%AD.pdf"
dl "2026-04_快手_让开关自我消亡_AI赋能的Feature Flag全生命周期治理.pdf" "https://static001.geekbang.org/con/170/pdf/502652185/file/%E9%97%AB%E6%96%87%E4%BA%AE-%E8%AE%A9%E5%BC%80%E5%85%B3%E8%87%AA%E6%88%91%E6%B6%88%E4%BA%A1%EF%BC%9AAI+%E8%B5%8B%E8%83%BD%E7%9A%84+Feature+Flag+%E5%85%A8%E7%94%9F%E5%91%BD%E5%91%A8%E6%9C%9F%E6%B2%BB%E7%90%86.pdf"
dl "2026-04_哔哩哔哩_存量修复加增量拦截_B站代码债务治理实践.pdf" "https://static001.geekbang.org/con/170/pdf/1181906191/file/%E4%BD%95%E4%B9%8B%E7%9C%9F-%E5%AD%98%E9%87%8F%E4%BF%AE%E5%A4%8D+%2B+%E5%A2%9E%E9%87%8F%E6%8B%A6%E6%88%AA%EF%BC%9AB+%E7%AB%99%E4%BB%A3%E7%A0%81%E5%80%BA%E5%8A%A1%E6%B2%BB%E7%90%86%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-08_平凯数据库TiDB_Coding Agent如何真正落地研发流程_任务编排质量保障与知识沉淀.pdf" "https://static001.geekbang.org/con/172/pdf/3858524012/file/%E6%9F%8F%E4%BD%B3%E8%BE%B0-Coding+Agent+%E5%A6%82%E4%BD%95%E7%9C%9F%E6%AD%A3%E8%90%BD%E5%9C%B0%E7%A0%94%E5%8F%91%E6%B5%81%E7%A8%8B%EF%BC%9A%E4%BB%BB%E5%8A%A1%E7%BC%96%E6%8E%92%E3%80%81%E8%B4%A8%E9%87%8F%E4%BF%9D%E9%9A%9C%E4%B8%8E%E7%9F%A5%E8%AF%86%E6%B2%89%E6%B7%80.pdf"
dl "2026-08_蚂蚁集团_AI驱动的生产级软件交付基建和实践.pdf" "https://static001.geekbang.org/con/172/pdf/562316146/file/%E5%88%98%E4%BB%81%E6%9D%83-%E8%9A%82%E8%9A%81+AI+%E9%A9%B1%E5%8A%A8%E7%9A%84%E7%94%9F%E4%BA%A7%E7%BA%A7%E8%BD%AF%E4%BB%B6%E4%BA%A4%E4%BB%98%E5%9F%BA%E5%BB%BA%E5%92%8C%E5%AE%9E%E8%B7%B5.pptx.pdf"
dl "2026-06_蚂蚁数科_全链路AI研发_SDD规范驱动与Harness工程实践.pdf" "https://static001.geekbang.org/con/171/pdf/2329973947/file/%E8%9A%82%E8%9A%81AI%E5%85%A8%E9%93%BE%E8%B7%AF%E7%A0%94%E5%8F%91%EF%BC%9ASDD%E8%A7%84%E8%8C%83%E9%A9%B1%E5%8A%A8%E4%B8%8EHarness%E5%B7%A5%E7%A8%8B%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-04_好未来_让AI运行在工程资产上_从PRD到上线的交付闭环.pdf" "https://static001.geekbang.org/con/170/pdf/1755702703/file/%E6%9D%8E%E6%96%87%E9%B9%8F-%E8%AE%A9+AI+%E8%BF%90%E8%A1%8C%E5%9C%A8%E5%B7%A5%E7%A8%8B%E8%B5%84%E4%BA%A7%E4%B8%8A%EF%BC%9A%E4%BB%8E+PRD+%E5%88%B0%E4%B8%8A%E7%BA%BF%E7%9A%84%E4%BA%A4%E4%BB%98%E9%97%AD%E7%8E%AF%E3%80%81Hybrid+%E6%89%A7%E8%A1%8C%E3%80%81+Doc+%E4%B8%8E+Code+%E8%81%94%E5%8A%A8.pdf"
dl "2025-10_字节跳动_抢回50%的值班时间_SRE Agent从0到1的降噪与排障实践.pdf" "https://static001.geekbang.org/con/161/pdf/3086263295/file/%E8%91%A3%E5%96%84%E4%B8%9C-%E5%AD%97%E8%8A%82%E8%B7%B3%E5%8A%A8+SRE+Agent+%E4%BB%8E+0+%E5%88%B0+1+%E7%9A%84%E9%99%8D%E5%99%AA%E4%B8%8E%E6%8E%92%E9%9A%9C%E5%AE%9E%E8%B7%B5-final.pdf"
dl "2026-04_趣丸科技_基于假设验证闭环的RCA Agent实践.pdf" "https://static001.geekbang.org/con/170/pdf/3671944143/file/%E5%88%98%E8%87%B3%E6%B5%A9-%E5%9F%BA%E4%BA%8E+%26ldquo%3B%E5%81%87%E8%AE%BE-%E9%AA%8C%E8%AF%81%26rdquo%3B+%E9%97%AD%E7%8E%AF%E7%9A%84+RCA+Agent+%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-04_小米_从个人经验到组织资产_AI for SRE的闭环实践与资产沉淀.pdf" "https://static001.geekbang.org/con/170/pdf/2236841513/file/%E8%B5%B5%E6%96%87%E6%88%90-%E4%BB%8E%E4%B8%AA%E4%BA%BA%E7%BB%8F%E9%AA%8C%E5%88%B0%E7%BB%84%E7%BB%87%E8%B5%84%E4%BA%A7%EF%BC%9A%E5%B0%8F%E7%B1%B3%E8%BF%90%E7%BB%B4%E7%9F%A5%E8%AF%86%E6%B2%89%E6%B7%80%E7%9A%84%E9%97%AD%E7%8E%AF%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-10_Kodem_面向未来的DevSecOps_如何用AI重塑应用安全.pdf" "https://static001.geekbang.org/con/161/pdf/1274282733/file/%E9%9D%A2%E5%90%91%E6%9C%AA%E6%9D%A5%E7%9A%84DevSecOps%EF%BC%9AKodem+%E5%A6%82%E4%BD%95%E7%94%A8AI%E9%87%8D%E5%A1%91%E5%BA%94%E7%94%A8%E5%AE%89%E5%85%A8-%E5%88%98%E6%B0%B8%E5%BC%BA.pdf"
dl "2026-06_蚂蚁安全_以模治模_支付宝Agent安全漏洞智能化检测实践.pdf" "https://static001.geekbang.org/con/171/pdf/1462849090/file/%E4%BB%A5%E6%A8%A1%E6%B2%BB%E6%A8%A1%26mdash%3B%26mdash%3B%E6%94%AF%E4%BB%98%E5%AE%9D+Agent+%E5%AE%89%E5%85%A8%E6%BC%8F%E6%B4%9E%E6%99%BA%E8%83%BD%E5%8C%96%E6%A3%80%E6%B5%8B%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-08_TraceLite_从成功率到失败归因_Agent Harness组件级评测实践.pdf" "https://static001.geekbang.org/con/172/pdf/2238971826/file/%E6%9B%B9%E6%99%BA%E5%8B%87-%E4%BB%8E%E6%88%90%E5%8A%9F%E7%8E%87%E5%88%B0%E5%A4%B1%E8%B4%A5%E5%BD%92%E5%9B%A0%EF%BC%9AAgent+Harness%E7%BB%84%E4%BB%B6%E7%BA%A7%E8%AF%84%E6%B5%8B%E5%AE%9E%E8%B7%B5.pdf"
echo; echo "完成：成功 $ok 份，失败 $fail 份。文件在：$DEST"
echo "回到 Claude 说一声「下好了」即可。"
