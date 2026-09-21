"""评测集跑分：规则引擎在 20 条虚构需求上的成绩不低于阈值（防止后续改规则退化）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from eval_req import DEFAULT_SET, THRESHOLDS, score_case, summarize  # noqa: E402
from xuzhi.llm import LLM  # noqa: E402


def test_eval_set_meets_thresholds():
    cases = json.loads(DEFAULT_SET.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 20
    rows = [score_case(c, LLM(mode="mock")) for c in cases]
    s = summarize(rows)
    assert s["pass"], {k: s[k] for k in THRESHOLDS}
    assert s["type_acc"] >= THRESHOLDS["type_acc"] and s["probe_false"] <= THRESHOLDS["probe_false"]
    assert s["redact"] == 1.0 and s["realtime_acc"] == 1.0
