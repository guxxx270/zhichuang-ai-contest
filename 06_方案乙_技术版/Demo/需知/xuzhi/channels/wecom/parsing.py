"""解析同事在企微里发来的消息：是命令、是对问清的答复、还是一条新需求。"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

__all__ = ["strip_mention", "parse_answers", "looks_like_answers"]

# "1 需要"/"1. 需要"/"1、需要"/"答1：需要"/"#1 需要"
_ANSWER_LINE = re.compile(r"^\s*(?:答|#)?\s*(\d{1,2})\s*[\.．、：:）)\]】]?\s*(.+?)\s*$")


def strip_mention(text: str, bot_name: str = "") -> str:
    """剥掉群聊 content 开头的 "@机器人名 "（企微会把 @ 一起带过来）。"""
    text = (text or "").lstrip()
    if bot_name:
        pat = re.compile(r"^(?:@" + re.escape(bot_name) + r"\s*|@\S+\s+)+")
    else:
        pat = re.compile(r"^(?:@\S+\s+)+")
    return pat.sub("", text, count=1).strip()


def parse_answers(text: str) -> Dict[int, str]:
    """把多行"编号 + 答复"解析成 {序号: 答复}。答复本身可以带标点和空格。"""
    out: Dict[int, str] = {}
    for line in (text or "").splitlines():
        m = _ANSWER_LINE.match(line)
        if not m:
            continue
        num, ans = int(m.group(1)), m.group(2).strip()
        if 1 <= num <= 30 and ans:
            out[num] = ans
    return out


def looks_like_answers(text: str, numbered_count: int) -> bool:
    """像"在回答问题"而不是"一条新需求"：上轮问过题，且正文以编号行为主、整体不长。"""
    if numbered_count <= 0:
        return False
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    answers = parse_answers(text)
    if not answers:
        return False
    # 编号行占多数，且没有超出上轮的题号
    if len(answers) < max(1, len(lines) // 2):
        return False
    return all(n <= numbered_count for n in answers)
