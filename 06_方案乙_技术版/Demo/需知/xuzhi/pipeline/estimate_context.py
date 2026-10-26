"""估时专用源码检索：只在已提供的 RepoBundle 文本内选择需求相关片段。"""
from __future__ import annotations

import json
import math
import re
from pathlib import PurePosixPath
from typing import Iterable

from ..drafts import RepoBundle, is_secret_path, redact_material


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_STOP_WORDS = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "is", "with", "this", "that", "ai", "http", "https"}
_GAP = "\n/* …未选取的代码… */\n"
_CHINESE = re.compile(r"[\u4e00-\u9fff]+")
# 只拆语法和通用需求动作，不内置某个项目的指标或文件名。
_CLAUSE_BREAK = re.compile(r"(?:需要|修改|调整|增加|改为|目前|之后|如果|例如|所有|对应|计算|方法|逻辑|指标|字段|是否|以及|或者|并且|这个|一个|该|的|中|给|将|在|为|是|下|和|与|对|其)")


def _business_terms(demand: str, indicators: Iterable[str]) -> dict[str, int]:
    terms: dict[str, int] = {}
    for span in _CHINESE.findall(demand):
        for phrase in _CLAUSE_BREAK.split(span):
            if 4 <= len(phrase) <= 20:
                terms[phrase] = 28
    for indicator in indicators:
        indicator = str(indicator).strip()
        if _CHINESE.search(indicator) and 3 <= len(indicator) <= 40:
            terms[indicator] = 36
    return terms


def _terms(text: str, answers: Iterable[str], keywords: Iterable[str], indicators: Iterable[str]) -> list[tuple[re.Pattern, int]]:
    weights: dict[str, int] = {}
    indicators = list(indicators)
    demand = "\n".join([text, *answers])

    def add(term: str, weight: int) -> None:
        term = term.strip().lower()
        if 2 <= len(term) <= 100 and term not in _STOP_WORDS:
            weights[term] = max(weights.get(term, 0), weight)

    # 不使用 Unicode 的 \b：中文紧邻的 52W_beta 等标识也必须能检出。
    for token in _IDENTIFIER.findall(demand):
        if not re.search(r"[A-Za-z]", token):
            continue
        add(token, 20 if re.search(r"[_\d.-]", token) else 12)
        # 周期/单位标识的常见写法差异，如 52W_value 与 w52_value；不绑定业务字段。
        swapped = re.sub(r"(^|_)(\d+)([A-Za-z]+)(?=_|$)", r"\1\3\2", token)
        if swapped != token:
            add(swapped, 18)
        parts = re.sub(r"([a-z])([A-Z])", r"\1_\2", token)
        for part in re.split(r"[_.-]+", parts):
            if re.search(r"[A-Za-z]", part) and len(part) >= 3:
                add(part, 8)
    for term in keywords:
        add(str(term), 3)
    for term in indicators:
        add(str(term), 10)
    for term, weight in _business_terms(demand, indicators).items():
        add(term, weight)
    patterns = []
    for term, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:64]:
        pattern = re.escape(term)
        if term.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        patterns.append((re.compile(pattern, re.I), weight))
    return patterns


def _source_weight(path: str, kind: str, demand: str) -> float:
    parts = PurePosixPath(path.replace("\\", "/").lower()).parts
    name = parts[-1] if parts else ""
    if len(name) > 4 and name in demand.lower():
        return 1.2  # 明确点名配置文件时仍应优先读该文件。
    suffix = PurePosixPath(name).suffix
    if any(part in {"outputs", "artifacts", "tmp", "archive", "archives"} for part in parts[:-1]):
        return 0.15
    if kind == "config" or suffix in {".json", ".yaml", ".yml", ".toml", ".xml", ".properties", ".md"}:
        return 0.2
    if suffix in {".css", ".scss", ".less"}:
        return 0.25
    if any(part in {"test", "tests", "fixtures", "__tests__"} for part in parts[:-1]) or name.startswith("test_") or ".test." in name or ".spec." in name:
        return 0.45
    if suffix == ".sql":
        return 0.55
    return 1.0


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _render(text: str, windows: list[tuple[int, int]]) -> str:
    return _GAP.join(text[start:end] for start, end in _merge(windows))


def _excerpt(text: str, patterns: list[tuple[re.Pattern, float]], limit: int) -> str:
    if len(text) <= limit:
        return text
    windows: dict[tuple[int, int], tuple[float, int]] = {}
    for pattern, weight in patterns:
        hits = [match.start() for match in pattern.finditer(text)]
        # 大文件重复命中时取分散位置，避免只看头部，也避免制造无界候选窗口。
        if len(hits) > 32:
            hits = [hits[index * (len(hits) - 1) // 31] for index in range(32)]
        for position in hits:
            start, end = max(0, position - 600), min(len(text), position + 1400)
            newline = text.rfind("\n", max(0, start - 120), start)
            if newline >= 0:
                start = newline + 1
            newline = text.find("\n", end, min(len(text), end + 120))
            if newline >= 0:
                end = newline + 1
            score = weight + sum(w for p, w in patterns if p.search(text[start:end]))
            previous = windows.get((start, end))
            if previous is None or score > previous[0]:
                windows[(start, end)] = (score, position)
    if not windows:
        return text[:limit]
    head = min(400, limit // 6) if limit >= 800 else 0
    selected = [(0, head)] if head else []
    focused = False
    for (start, end), (_score, position) in sorted(windows.items(), key=lambda item: (-item[1][0], item[0][0])):
        candidate = selected + [(start, end)]
        if len(_render(text, candidate)) <= limit:
            selected = _merge(candidate)
            focused = True
        elif not focused:
            available = max(0, limit - head - (len(_GAP) if head else 0))
            start = max(start, min(position - available // 3, end - available))
            end = min(len(text), start + available)
            selected = _merge(selected + [(start, end)])
            focused = True
    return _render(text, selected)[:limit]


def estimate_code_files(
    repos: list[RepoBundle], text: str, answers: Iterable[str] = (), *,
    keywords: Iterable[str] = (), indicators: Iterable[str] = (), code_limit: int = 28000,
) -> list[dict[str, str]]:
    """按完整已收录文本排名；输出 code_files 的 JSON 字符数不超过预算（空列表除外）。

    不打开本地文件、不访问仓库地址。凭据文件先排除；选中文本先整体脱敏，
    再取窗口，避免截断密钥后绕过脱敏。ID 使用原仓库/文件序号，不依赖脱敏路径唯一性。
    """
    if code_limit <= 2:
        return []
    answers = list(answers)
    indicators = list(indicators)
    patterns = _terms(text, answers, keywords, indicators)
    demand = "\n".join([text, *answers])
    candidates = []
    for repo_index, repo in enumerate(repos, 1):
        for file_index, source in enumerate(repo.files, 1):
            if is_secret_path(source.path) or not source.text.strip():
                continue
            # 每个词只加一次分：重复配置项和压缩文件不能靠出现次数占满预算。
            hits = {i for i, (pattern, _) in enumerate(patterns) if pattern.search(source.text)}
            path_hits = {i for i, (pattern, _) in enumerate(patterns) if pattern.search(source.path)}
            quality = _source_weight(source.path, source.kind, demand)
            candidates.append((quality, repo_index, file_index, source, hits, path_hits))
    # 在本次提供的文件集合里计算文档频率；普遍出现的技术词不应压过业务入口。
    frequencies = [sum(i in row[4] or i in row[5] for row in candidates) for i in range(len(patterns))]
    patterns = [(pattern, weight * min(3.0, 1 + math.log((1 + len(candidates)) / (1 + frequencies[i]))))
                for i, (pattern, weight) in enumerate(patterns)]
    business = {i for i, (pattern, _) in enumerate(patterns) if _CHINESE.search(pattern.pattern)}
    technical = set(range(len(patterns))) - business

    def score(row, terms, covered=frozenset()):
        quality, _, _, _, hits, path_hits = row
        return quality * sum(patterns[i][1] * (0.25 if i in covered else 1.0)
                             * (1.0 if i in hits else 0.5) for i in (hits | path_hits) & terms)

    all_terms = set(range(len(patterns)))
    ordered = sorted(candidates, key=lambda row: (-score(row, all_terms), -row[0], row[1], row[3].path, row[2]))
    ranked = []
    # 需求多次提及的具体指标，优先覆盖其两个实现位置（例如入口和计算函数）。
    # 不能仅因某个上游文件同时提及许多相关术语，就挤掉只写核心业务指标的入口。
    anchors = _business_terms(demand, indicators)
    focus = max(anchors, key=lambda term: (demand.count(term), anchors[term], len(term)), default="")
    focus_terms = {i for i, (pattern, _) in enumerate(patterns) if pattern.pattern == re.escape(focus.lower())} if focus else set()
    focus_rows = sorted((row for row in ordered if score(row, focus_terms) > 0),
                        key=lambda row: (-score(row, focus_terms), -score(row, all_terms)))
    ranked.extend(focus_rows[:2])
    # 再确保业务词与技术标识各有覆盖；它们可以落在同一文件。
    for terms in (business, technical):
        if ordered:
            best = max(ordered, key=lambda row: (score(row, terms), score(row, all_terms)))
            if score(best, terms) > 0 and best not in ranked and (terms != business or not ranked):
                ranked.append(best)
    remaining = [row for row in ordered if row not in ranked]
    covered = set().union(*(row[4] | row[5] for row in ranked)) if ranked else set()
    while remaining:
        best = max(remaining, key=lambda row: score(row, all_terms, covered))
        ranked.append(best)
        remaining.remove(best)
        covered.update(best[4] | best[5])
    selected: list[dict[str, str]] = []
    used = 2  # 与估时发送端相同的默认 JSON 分隔符，包含括号、字段和字符串转义。
    for _quality, repo_index, file_index, source, _hits, _path_hits in ranked:
        path = redact_material(source.path)
        record = {"id": f"R{repo_index}/F{file_index}/{path}", "path": path, "excerpt": ""}
        overhead = len(json.dumps(record, ensure_ascii=False)) + (2 if selected else 0)
        room = code_limit - used - overhead
        if room <= 0:
            continue
        safe_text = redact_material(source.text)
        limit = min(6000, room)
        if limit < min(100, len(safe_text)):
            continue  # 不把预算尾部的一两个字符伪装成另一个已提供的源码文件。
        for _ in range(12):
            record["excerpt"] = _excerpt(safe_text, patterns, limit)
            cost = len(json.dumps(record, ensure_ascii=False)) + (2 if selected else 0)
            if used + cost <= code_limit:
                break
            limit -= used + cost - code_limit
            if limit <= 0:
                record["excerpt"] = ""
                break
        if record["excerpt"].strip() and len(record["excerpt"]) >= min(100, len(safe_text)) and used + cost <= code_limit:
            selected.append(record)
            used += cost
    return selected
