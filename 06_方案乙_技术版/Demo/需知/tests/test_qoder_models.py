from xuzhi.qoder_cloud import (
    filter_model_catalog,
    merge_model_catalogs,
    parse_model_catalog,
)


def test_parse_model_catalog_basic():
    raw = {
        "data": [
            {"id": "ultimate", "display_name": "Ultimate", "is_enabled": True},
            {"id": "sonus", "display_name": "Sonus", "is_enabled": True, "is_new": True},
            {"id": "off", "display_name": "Off", "is_enabled": False},
        ]
    }
    rows = parse_model_catalog(raw)
    ids = [m for m, _ in rows]
    assert ids == ["ultimate", "sonus"]
    assert "Sonus" in rows[1][1]


def test_filter_finds_sonus_and_fuzzy_sonnet():
    catalog = [
        ("ultimate", "Ultimate"),
        ("sonus", "Sonus（sonus）"),
        ("claude-sonnet", "Claude Sonnet"),
    ]
    assert [m for m, _ in filter_model_catalog(catalog, "sonus")] == ["sonus"]
    soft = filter_model_catalog(catalog, "sonnet")
    assert any(m == "claude-sonnet" for m, _ in soft) or any(m == "sonus" for m, _ in soft)


def test_parse_model_catalog_accepts_string_ids():
    rows = parse_model_catalog({"data": ["Qwen/Qwen3-8B", {"id": "deepseek-ai/DeepSeek-V3"}]})
    assert [m for m, _ in rows] == ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"]


def test_live_openai_providers_include_openai_and_fable():
    from xuzhi import config

    assert "openai" in config.LIVE_OPENAI_PROVIDERS
    assert "fable" in config.LIVE_OPENAI_PROVIDERS
    assert "siliconflow" in config.LIVE_OPENAI_PROVIDERS
    ids = {p[2] for p in config.LLM_PRESETS if p[1] == "api"}
    assert {"openai", "fable", "siliconflow"} <= ids
    openai_row = next(p for p in config.LLM_PRESETS if p[2] == "openai")
    assert openai_row[3] == "https://api.openai.com/v1"


def test_merge_prefers_first_label():
    a = [("sonus", "Sonus")]
    b = [("sonus", "dup"), ("ultimate", "Ultimate")]
    merged = merge_model_catalogs(a, b)
    assert merged[0] == ("sonus", "Sonus")
    assert [m for m, _ in merged] == ["sonus", "ultimate"]
