"""「问清」提示词稳定性测试（在需知目录下运行，用 .env 里的模型）：
  python 稳定性测试.py [次数=3]
对 3 组样例各跑 N 次，统计：JSON 可解析率、标题一致率、问题集合两两重叠度（Jaccard）、高影响问题数，写 稳定性测试_报告.md。"""
from __future__ import annotations

import json
import re
import sys
import time
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
XUZHI = HERE.parent / "Demo" / "需知" if (HERE.parent / "Demo" / "需知").exists() else HERE.parent.parent / "Demo" / "需知"
sys.path.insert(0, str(XUZHI))
from xuzhi.llm import LLM, parse_json_loose  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
prompt = (HERE / "问清_提示词_v1.md").read_text(encoding="utf-8").split("〔原话〕")[0]
samples = sorted((XUZHI / "data" / "samples").glob("0[1-3]_*.md"))
llm = LLM()
if llm.mode != "api":
    raise SystemExit("需要 api 模式：请先在需知目录配置 .env")


def norm(q: str) -> str:
    return re.sub(r"[\s，。？?：:（）()、/]", "", q)[:18]


lines = [f"# 「问清」提示词稳定性测试报告", f"模型：{llm.mode}｜每组 {N} 次｜{time.strftime('%Y-%m-%d %H:%M')}", ""]
for p in samples:
    text = p.read_text(encoding="utf-8")
    outs, secs = [], []
    for _ in range(N):
        t0 = time.time()
        raw = llm.chat(prompt, "〔原话〕\n" + text, temperature=0.2, json_mode=True)
        secs.append(time.time() - t0)
        outs.append(parse_json_loose(raw))
    ok = [o for o in outs if isinstance(o, dict) and o.get("title")]
    titles = [o["title"] for o in ok]
    qsets = [{norm(q.get("question", "")) for q in o.get("questions", [])} for o in ok]
    jac = [len(a & b) / max(len(a | b), 1) for a, b in combinations(qsets, 2)] if len(qsets) > 1 else []
    highs = [sum(1 for q in o.get("questions", []) if q.get("impact") == "高") for o in ok]
    lines += [f"## {p.stem}", f"- JSON 可解析：{len(ok)}/{N}", f"- 标题：{' / '.join(dict.fromkeys(titles)) or '—'}（一致率 {titles.count(max(set(titles), key=titles.count)) / max(len(titles), 1):.0%}）" if titles else "- 标题：—",
              f"- 问题数：{[len(o.get('questions', [])) for o in ok]}；高影响数：{highs}", f"- 问题集合两两重叠度：{[round(j, 2) for j in jac]}（均值 {sum(jac) / len(jac):.2f}）" if jac else "- 问题集合重叠度：样本不足",
              f"- 平均用时：{sum(secs) / len(secs):.1f}s", ""]
    if ok:
        lines += ["<details><summary>第 1 次输出</summary>", "", "```json", json.dumps(ok[0], ensure_ascii=False, indent=2)[:3000], "```", "</details>", ""]
(HERE / "稳定性测试_报告.md").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:40]))
print(f"\n报告已写入 {HERE / '稳定性测试_报告.md'}")
