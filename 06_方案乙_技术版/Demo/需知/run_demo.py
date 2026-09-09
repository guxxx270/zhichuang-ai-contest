"""命令行一键跑通需知（不需要界面）：python run_demo.py [样例编号 1-4]"""
from __future__ import annotations

import sys

from xuzhi import config
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze


def main() -> None:
    n = sys.argv[1] if len(sys.argv) > 1 else "1"
    path = next(config.SAMPLES_DIR.glob(f"{int(n):02d}_*.md"))
    llm = LLM()
    print(f"{config.PRODUCT_NAME} · {config.PRODUCT_SLOGAN}")
    print(f"LLM 模式：{llm.mode}（{'已接入 ' + config.LLM_MODEL if llm.mode == 'api' else '未配置 key，规则引擎兜底'}）\n样例：{path.name}\n")
    a = analyze(path.read_text(encoding="utf-8"), llm=llm)
    c = a.card
    print("=" * 70); print(f"【问清】{c.title}｜{c.req_type}｜提出方 {c.requester}｜脱敏 {a.redacted} 处｜{a.seconds}s")
    for i, f in enumerate(c.features, 1):
        print(f"  功能点 {i}. {f}")
    print(f"  待确认 {len(a.questions)} 条：")
    for q in a.questions:
        print(f"   [{q.impact}{'·🌾' if q.tag == '期货' else ''}] {q.question}")
    print("\n" + a.message)
    print("=" * 70); print("【写单】业务版前 30 行："); print("\n".join(a.spec.business_md.splitlines()[:30]))
    print("=" * 70); e = a.estimate
    print(f"【估量】传统 {e.mid} 人天（{e.low}～{e.high}）｜AI 协同 {e.ai_mid} 人天（{e.ai_low}～{e.ai_high}，省 {e.saving_pct}%）｜置信度 {e.confidence}：{e.confidence_reason}")
    print("  分工：" + "，".join(f"{k} {v:.0%}" for k, v in e.who.items()) + f"｜建议：{e.delivery}")
    for t in e.tasks:
        print(f"   {t.id:3} {t.phase} · {t.who:5} {t.trad_days:>5} → {t.ai_days:>5}  {t.name}")
    print("  六维：" + "，".join(f"{k} {v}" for k, v in e.dims.items()))
    for s in e.similar:
        print(f"  相似：{s.title}（{s.score:.0%}，实际 {s.actual_days} 人天）")
    print("=" * 70); print(f"【定架】{a.architecture.coverage_line}")
    for d in a.architecture.decisions:
        print(f"  {d.id} {d.topic} → {d.recommended}（{d.impact}）")
    print("=" * 70); print(f"【出样】原型 HTML {len(a.prototype_html)} 字节（Streamlit 里可点）")


if __name__ == "__main__":
    main()
