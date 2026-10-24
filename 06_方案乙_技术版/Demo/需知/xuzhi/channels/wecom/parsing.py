"""解析同事在企微里发来的消息：是命令、是对问清的答复、还是一条新需求。"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

__all__ = ["strip_mention", "parse_answers", "looks_like_answers", "ask_prefix"]

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


# 编号里夹着一条完整的新诉求（动词后 12 字内出现交付物）。「想要一个…页面」几乎只在提需求时说；
# 「加一个 / 做个 / 需要一个…功能」答复里也常见（「1 加一个导出功能，导出时带水印」），要更像一整条需求才算
_DELIVERABLE = r"[^\n]{0,12}?(?:页面|报表|看板|提醒|接口|功能|系统)"
_NEW_DEMAND_STRONG = re.compile(r"想要" + _DELIVERABLE)
_NEW_DEMAND_WEAK = re.compile(r"(?:需要一个|新增一个|做一个|做个|加一个)" + _DELIVERABLE)


def _new_demand(text: str, answers: Dict[int, str]) -> bool:
    """带编号列表发来的新需求：有新诉求句式，且篇幅像需求。单看字数或关键词都会误伤真实答复（答复也可能长、
    也会提到页面），所以两条同时满足才算：「想要…」整段超过 40 字或某条超过 20 字；「加一个 / 做个…」要整段超过 60 字或某条超过 30 字。"""
    total = len((text or "").strip())
    longest = max((len(a) for a in answers.values()), default=0)
    if any(_NEW_DEMAND_STRONG.search(a) for a in answers.values()) and (total > 40 or longest > 20):
        return True
    return any(_NEW_DEMAND_WEAK.search(a) for a in answers.values()) and (total > 60 or longest > 30)


def looks_like_answers(text: str, numbered_count: int) -> bool:
    """像"在回答问题"而不是"一条新需求"：上轮问过题，且正文以编号行为主、不像一条带编号的新需求。"""
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
    if not all(n <= numbered_count for n in answers):
        return False
    return not _new_demand(text, answers)


# /问 /问码 /ask /代码 + 问题；返回问题正文，不是这个前缀则返回 None
_ASK_PREFIX = re.compile(r"^/(?:问码|问|ask|code|代码)(?:\s+|$)", re.I)


def ask_prefix(text: str) -> "str | None":
    m = _ASK_PREFIX.match(text or "")
    if not m:
        return None
    return (text[m.end():] or "").strip()
