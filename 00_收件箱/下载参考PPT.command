#!/bin/bash
# 双击运行：把 InfoQ 上挑好的 11 份参考 PPT（PDF）直接下载到 02_参考PPT/
# 下载地址由 ppt.infoq.cn 的下载接口返回（2026-09-06 获取），是公开的静态文件链接，不需要登录。
# 用完可以删掉这个文件。

DEST="$HOME/SynologyDrive/智创未来AI大赛/02_参考PPT"
mkdir -p "$DEST"
cd "$DEST" || exit 1

ok=0; fail=0
dl() {
  local name="$1" url="$2"
  if [ -s "$name" ]; then echo "已存在，跳过：$name"; ok=$((ok+1)); return; fi
  echo "下载：$name"
  if curl -L --fail --retry 3 --retry-delay 2 -o "$name.part" "$url" && mv "$name.part" "$name"; then
    ok=$((ok+1))
  else
    echo "  ✗ 失败：$name"; rm -f "$name.part"; fail=$((fail+1))
  fi
}

dl "2026-08_中软融鑫_金融监管领域的Harness实践_知识与数据驱动Agent稳定运行.pdf" "https://static001.geekbang.org/con/172/pdf/2517672161/file/%E4%BA%8E%E6%B5%A9%E5%86%9B-%E9%87%91%E8%9E%8D%E7%9B%91%E7%AE%A1%E9%A2%86%E5%9F%9F%E7%9A%84+Harness+%E5%AE%9E%E8%B7%B5%EF%BC%9A%E8%AE%A9%E7%9F%A5%E8%AF%86%E4%B8%8E%E6%95%B0%E6%8D%AE%E9%A9%B1%E5%8A%A8+Agent+%E7%A8%B3%E5%AE%9A%E8%BF%90%E8%A1%8C.pdf"
dl "2026-06_平安人寿_AI神盾平台在金融生态中的智能风控实践.pdf" "https://static001.geekbang.org/con/171/pdf/1746208047/file/AI+%E7%A5%9E%E7%9B%BE%E5%B9%B3%E5%8F%B0%E5%9C%A8%E9%87%91%E8%9E%8D%E7%94%9F%E6%80%81%E4%B8%AD%E7%9A%84%E6%99%BA%E8%83%BD%E9%A3%8E%E6%8E%A7%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-08_去哪儿_企业级Harness Engineering实践_运营数据Coding办公智能体.pdf" "https://static001.geekbang.org/con/172/pdf/2804767142/file/%E6%9D%8E%E4%BD%B3%E5%A5%87-%E4%BC%81%E4%B8%9A%E7%BA%A7+Harness+Engineering+%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-10_容联云_一本通Datainsight Agent在城商行业务分析的实践.pdf" "https://static001.geekbang.org/con/161/pdf/2802523265/file/10.24+%E4%B8%80%E6%9C%AC%E9%80%9A%EF%BC%88Datainsight+Agent%EF%BC%89%E5%9C%A8%E5%9F%8E%E5%95%86%E8%A1%8C%E4%B8%9A%E5%8A%A1%E5%88%86%E6%9E%90%E7%9A%84%E6%8E%A2%E7%B4%A2%E4%B8%8E%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-12_商汤_从需求到投标_智能技术方案生成Agent实战.pdf" "https://static001.geekbang.org/con/162/pdf/180499565/file/AICon%E5%8C%97%E4%BA%AC2025-%E7%8E%8B%E5%BF%97%E5%AE%8F-%E4%BB%8E%E9%9C%80%E6%B1%82%E5%88%B0%E6%8A%95%E6%A0%87%EF%BC%9A%E6%95%B0%E6%8D%AE%E9%A9%B1%E5%8A%A8%E7%9A%84%E6%99%BA%E8%83%BD%E6%8A%80%E6%9C%AF%E6%96%B9%E6%A1%88%E7%94%9F%E6%88%90+Agent+%E5%AE%9E%E6%88%98.pdf"
dl "2025-06_网易有道_QAnything知识库问答体系革新与实践.pdf" "https://static001.geekbang.org/con/159/pdf/1114569861/file/02-QAnything%EF%BC%9A%E5%A4%A7%E6%A8%A1%E5%9E%8B%E9%A9%B1%E5%8A%A8%E4%B8%8B%E7%9A%84%E7%9F%A5%E8%AF%86%E5%BA%93%E9%97%AE%E7%AD%94%E4%BD%93%E7%B3%BB%E9%9D%A9%E6%96%B0%E4%B8%8E%E5%AE%9E%E8%B7%B5.pdf"
dl "2026-06_上海AI实验室_MinerU面向Agent时代的文档解析基础设施.pdf" "https://static001.geekbang.org/con/171/pdf/1002130562/file/MinerU%EF%BC%9A%E9%9D%A2%E5%90%91+Agent+%E6%97%B6%E4%BB%A3%E7%9A%84%E6%96%87%E6%A1%A3%E8%A7%A3%E6%9E%90%E5%9F%BA%E7%A1%80%E8%AE%BE%E6%96%BD%E6%BC%94%E8%BF%9B%E4%B8%8E%E5%AE%9E%E8%B7%B5+%E4%BD%95%E8%81%AA%E8%BE%89+.pdf"
dl "2026-08_汇丰科技_AI Coding在金融科技SDLC中的落地实践.pdf" "https://static001.geekbang.org/con/172/pdf/138250956/file/%E6%9D%8E%E6%B8%AD%E5%AE%81-%E4%BB%8E%E4%BB%A3%E7%A0%81%E7%94%9F%E6%88%90%E5%88%B0%E7%A0%94%E5%8F%91%E9%97%AD%E7%8E%AF%EF%BC%9AAI+Coding+%E5%9C%A8%E9%87%91%E8%9E%8D%E7%A7%91%E6%8A%80+SDLC+%E4%B8%AD%E7%9A%84%E8%90%BD%E5%9C%B0%E5%AE%9E%E8%B7%B5.pdf"
dl "2025-10_腾讯玄武_隐私不上云_结构化语义标签隐私防火墙.pdf" "https://static001.geekbang.org/con/161/pdf/1992890767/file/%E9%99%88%E6%98%B1-%E9%9A%90%E7%A7%81%E4%B8%8D%E4%B8%8A%E4%BA%91%EF%BC%8C%E6%A8%A1%E5%9E%8B%E6%94%BE%E5%BF%83%E7%94%A8%EF%BC%9A%E9%80%9A%E8%BF%87%E7%BB%93%E6%9E%84%E5%8C%96%E8%AF%AD%E4%B9%89%E6%A0%87%E7%AD%BE%E5%AE%9E%E7%8E%B0%E9%9A%90%E7%A7%81%E9%98%B2%E7%81%AB%E5%A2%99%EF%BC%88%E7%BB%88%E7%89%88%EF%BC%89.pdf"
dl "2025-05_数势科技_大模型Data Agent重构金融数据价值.pdf" "https://static001.geekbang.org/con/158/pdf/302531641/file/AICon_%E6%95%B0%E5%8A%BF%E7%A7%91%E6%8A%80_%E5%B2%91%E6%B6%A6%E5%93%B2.pdf"
dl "2025-08_同盾科技_智能体驱动信贷风险的动态感知到策略自迭代.pdf" "https://static001.geekbang.org/con/160/pdf/2417326169/file/%E8%91%A3%E7%BA%AA%E4%BC%9F-%E6%99%BA%E8%83%BD%E4%BD%93%E9%A9%B1%E5%8A%A8%E4%BF%A1%E8%B4%B7%E9%A3%8E%E9%99%A9%E7%9A%84%E5%8A%A8%E6%80%81%E6%84%9F%E7%9F%A5%E5%88%B0%E7%AD%96%E7%95%A5%E8%87%AA%E8%BF%AD%E4%BB%A3.pdf"

echo
echo "完成：成功 $ok 份，失败 $fail 份。文件在：$DEST"
echo "回到 Claude 说一声「下好了」即可。"
