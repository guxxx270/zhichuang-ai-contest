"""命令行一键跑通研伴四项技能（不需要界面）：python run_demo.py"""
from __future__ import annotations

from datetime import date

from yanban import config
from yanban.docs import load_docs
from yanban.llm import LLM
from yanban.memory import Memory
from yanban.privacy import Redactor
from yanban.profile import load_profile
from yanban.skills.morning import ask, build_brief
from yanban.skills.rulebook import RuleBook
from yanban.skills.writer import compliance_check, weekly_report


def main() -> None:
    llm, mem, profile = LLM(), Memory(), load_profile()
    print(f"{config.PRODUCT_NAME} · {config.PRODUCT_SLOGAN}")
    print(f"LLM 模式：{llm.mode}（{'已接入 ' + config.LLM_MODEL if llm.mode == 'api' else '未配置 key，规则引擎兜底'}）")
    print(f"我的：{profile.name}｜{profile.department} {profile.role}｜关注 {'、'.join(profile.watch_names())}\n")

    # ---- 隐盾 + 晨读 ----
    docs = load_docs()
    red = Redactor()
    total_red = 0
    for d in docs:
        r = red.redact(d.text)
        d.text, total_red = r.text, total_red + sum(r.counts.values())
    brief = build_brief(docs, profile, llm=llm)
    mem.log("晨读", "生成", inp=str(len(docs)), out=brief.markdown, redacted=total_red, engine=brief.engine, minutes=brief.minutes_saved)
    print("=" * 60); print(brief.markdown); print("=" * 60)
    q = "看多螺纹钢的那家理由是什么"
    ans, hits = ask(q, docs, llm)
    print(f"\n追问：{q}\n{ans}\n")

    # ---- 问典 ----
    rb = RuleBook()
    for q in ["沪铜 2609 结算价 78500 今天涨跌停多少", "螺纹钢 按 3200 价格 开 100 手 需要多少保证金",
              "沪铜 交割月前一月 持有 800 手 超没超限仓", "沪铜 2609 最后交易日是哪天", "什么情况下会被强行平仓"]:
        a = rb.answer(q, llm)
        mem.log("问典", a.intent, inp=q, out=a.text, engine=a.engine, minutes=3)
        print(f"问：{q}\n答：{a.text}\n  出处：{'；'.join(a.citations[:2])}\n")

    # ---- 代笔 ----
    draft, eng = weekly_report(profile, brief, notes="下周关注保证金调整后的持仓成本", llm=llm)
    findings = compliance_check(draft + "\n本周螺纹钢必涨。")
    mem.log("代笔", "周报初稿", out=draft, engine=eng, minutes=40)
    print("=" * 60); print(draft[:800] + ("…" if len(draft) > 800 else "")); print("=" * 60)
    print("合规自检：" + "；".join(f"[{f.kind}]{f.term}→{f.suggestion}" for f in findings[:4]))

    # ---- 隐盾单独演示 ----
    demo = "客户：王建国先生（手机 13912345678，资金账号 8812345678901）今日反馈其螺纹钢空单持仓浮亏 1,250,000 元，希望研究员给出后市判断。"
    r = red.redact(demo)
    print("\n隐盾：", r.text, "\n还原一致：", Redactor.restore(r.text, r.mapping) == demo)
    print("\n今日统计：", mem.stats())


if __name__ == "__main__":
    main()
