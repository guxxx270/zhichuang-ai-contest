"""定架 · 数据地图：需求里的数据项定位到 系统 · 数据域 · 接口 · 时效。"""
from __future__ import annotations

import pytest

from xuzhi import config, knowledge
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze

SAMPLES = {p.stem[:2]: p for p in config.SAMPLES_DIR.glob("*.md")}


@pytest.fixture(scope="module")
def maps():
    llm = LLM(mode="mock")
    return {k: analyze(p.read_text(encoding="utf-8"), llm=llm).architecture for k, p in SAMPLES.items()}


def test_catalog_has_domains_and_interfaces():
    assert knowledge.data_domains() >= 10 and knowledge.interfaces() >= 15
    assert all(s.domains == () or all(d.keywords for d in s.domains) for s in knowledge.systems())


def test_report_sample_locates_sources(maps):
    rows = {r.need: r for r in maps["01"].data_map}
    nav = next(r for n, r in rows.items() if n.startswith("产品净值"))
    assert nav.system == "估值核算系统" and nav.freshness == "T+1" and nav.status == "可复用"
    risk = next(r for n, r in rows.items() if n.startswith("风险指标"))
    assert risk.system == "风控系统"
    assert rows["较上一交易日变动"].status == "需计算"
    assert "涉及" in maps["01"].data_map_line and "## 数据地图" in maps["01"].adr_md


def test_external_source_marked_new(maps):
    spot = next(r for r in maps["02"].data_map if r.need.startswith("现货价格"))
    assert spot.system == "—" and spot.status == "需新建" and "外部" in spot.note
    assert any(x.startswith("待接入") for x in maps["02"].layers["数据"])


def test_realtime_demand_prefers_realtime_domain(maps):
    pos = next(r for r in maps["03"].data_map if r.need.startswith("持仓"))
    assert pos.system == "交易系统" and pos.freshness == "实时" and pos.status == "可复用"
    assert not any(r.status == "时效不符" for r in maps["04"].data_map)   # 分钟级 + 事件源不算时效不符
    d2 = next(d for d in maps["03"].decisions if d.id == "D2")
    assert "数据地图" in d2.reason
