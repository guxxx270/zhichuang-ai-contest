"""命令行一键跑通需知（不需要界面）：python run_demo.py [样例编号 1-6]"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from xuzhi import config
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.split import split_requirements


def main() -> None:
    n = sys.argv[1] if len(sys.argv) > 1 else "1"
    path = next(config.SAMPLES_DIR.glob(f"{int(n):02d}_*.md"))
    llm = LLM()
    print(f"{config.PRODUCT_NAME} · {config.PRODUCT_SLOGAN}")
    print(f"LLM 模式：{llm.mode}（{'已接入 ' + config.LLM_MODEL if llm.mode == 'api' else (llm.note or '未配置 key，规则引擎兜底')}）\n样例：{path.name}\n")
    text = path.read_text(encoding="utf-8")
    segments = split_requirements(text)
    if segments:
        print(f"【纪要直通车】这段话里识别到 {len(segments)} 条需求（页面上可一键拆开分别受理；下面先按整段跑一遍）：")
        for s in segments:
            print(f"  {s.index}. {s.requester or '未注明'}｜{s.title_hint}｜依据：{s.reason}")
        print()
    a = analyze(text, llm=llm)
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
    if e.status == "ready":
        print(f"【估量】以前 {e.mid * 8:g} 小时（{e.low * 8:g}～{e.high * 8:g}）｜现在 {e.ai_mid * 8:g} 小时（{e.ai_low * 8:g}～{e.ai_high * 8:g}）｜模型 {e.model}｜置信度 {e.confidence}")
        print(f"  {e.summary}")
        for task in e.tasks:
            print(f"   {task.id:3} {task.phase} · {task.name}：以前 {task.trad_days * 8:g} → 现在 {task.ai_days * 8:g} 小时")
            print(f"     以前：{task.before_reason}\n     现在：{task.after_reason}")
    else:
        print(f"【模型估量】{'估算失败' if e.status == 'failed' else '未估时'}：{e.error or e.summary or e.confidence_reason}")
    rule = e.references.get("rule", {})
    if rule.get("status") == "ready":
        b, now = rule["before"], rule["after"]
        print(f"【规则模板参考（未校准）】以前 {b['mid']:g} 小时（{b['low']:g}～{b['high']:g}）｜现在 {now['mid']:g} 小时（{now['low']:g}～{now['high']:g}）")
        print(f"  {rule['formula']}")
    history = e.references.get("history", {})
    print(f"【历史记录参考】{history.get('summary', '暂无记录')}；不与模型或规则工时加权合并。")
    for item in history.get("items", []):
        actual = f"{item['actual_hours']:g} 小时" if item.get("actual_hours") is not None else "未记录"
        print(f"  {item['title']}｜{item['source']}｜记录实际投入 {actual}")
    print("=" * 70); print(f"【定架】{a.architecture.coverage_line}")
    for d in a.architecture.decisions:
        print(f"  {d.id} {d.topic} → {d.recommended}（{d.impact}）")
    print("=" * 70); print(f"【出样】原型 HTML {len(a.prototype_html)} 字节（Streamlit 里可点）")


if __name__ == "__main__":
    main()
