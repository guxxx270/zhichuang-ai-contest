"""隐盾 · 命令行。纯本地规则，不联网。

  python cli.py redact 文件.txt                 脱敏后输出到屏幕，映射表写到 文件.map.json
  python cli.py redact 目录/ --out 目录_脱敏/     批量：目录下的文本与 Word 逐个脱敏，共用一张映射表（同一客户同一标签）
  python cli.py restore 回答.txt 文件.map.json   把大模型的回答还原（标签被改写成〈客户_1〉、客户1 等也认得）
  python cli.py check 文件或目录                  只检查不改写：发现明文敏感信息时退出码为 1，可挂在提交 / 外发之前
  python cli.py clip                            剪贴板一键：复制原文后运行 → 剪贴板里换成脱敏文本；
                                                复制大模型回答后再运行 → 剪贴板里换成还原文本
  也可用管道：cat 文件 | python cli.py redact -

常用选项：--names / --orgs / --keep 名单文件（一行一个）；--map 已有映射表（多份材料、多轮对话接着编号）；
         --off 金额,机构（关掉某些类型）；--hint（脱敏文本最前面加一句「请原样保留标签」）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from yindun.files import SUFFIXES, read_path
from yindun.privacy import KINDS, PROMPT_HINT, Redactor, Session, restore_detail, suspects

CLIP_MAP = Path.home() / ".yindun" / "clip.map.json"
_LABEL = re.compile(r"<[^\s<>_]{1,8}_\d+>")


def _lines(path: str | None) -> list[str]:
    if not path:
        return []
    return [x.strip() for x in Path(path).read_text(encoding="utf-8-sig").splitlines() if x.strip() and not x.startswith("#")]


def _redactor(a: argparse.Namespace) -> Redactor:
    off = {k.strip() for k in (a.off or "").replace("，", ",").split(",") if k.strip()}
    bad = off - set(KINDS)
    if bad:
        raise SystemExit(f"--off 里有不认识的类型：{'、'.join(bad)}；可选 {'、'.join(KINDS)}")
    return Redactor(names=_lines(a.names), orgs=_lines(a.orgs), keep=_lines(a.keep), kinds=[k for k in KINDS if k not in off])


def _load_map(path: str | Path | None) -> dict[str, str]:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def _save_map(path: Path, mapping: dict[str, str]) -> None:
    """映射表里是原文：只留本机，权限收紧到仅本人可读写（Windows 上 chmod 只影响只读位，忽略即可）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES and ".map" not in p.suffixes)
    return [target]


def _summary(counts: dict[str, int]) -> str:
    return "、".join(f"{k} {v}" for k, v in counts.items()) or "无"


def cmd_redact(a: argparse.Namespace) -> int:
    session = Session(_redactor(a), _load_map(a.map))
    src = Path(a.src) if a.src != "-" else None
    t0 = time.perf_counter()
    if src is None:
        r = session.redact(sys.stdin.read())
        print((PROMPT_HINT + "\n\n" if a.hint else "") + r.text)
        map_path = Path(a.map) if a.map else None
        total = r.counts
    elif src.is_dir():
        out_dir = Path(a.out) if a.out else src.with_name(src.name + "_脱敏")
        total: dict[str, int] = {}
        files, hits = _files(src), 0
        for f in files:
            r = session.redact(read_path(f))
            dest = (out_dir / f.relative_to(src)).with_suffix(".txt" if f.suffix.lower() == ".docx" else f.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((PROMPT_HINT + "\n\n" if a.hint else "") + r.text, encoding="utf-8")
            for k, v in r.counts.items():
                total[k] = total.get(k, 0) + v
            n = len(_LABEL.findall(r.text))
            hits += n
            print(f"[隐盾] {f.relative_to(src)} → {dest}：{n} 处", file=sys.stderr)
        map_path = Path(a.map) if a.map else out_dir / "隐盾映射表.map.json"
        print(f"[隐盾] 共 {len(files)} 个文件，换掉 {hits} 处", file=sys.stderr)
    else:
        r = session.redact(read_path(src))
        text = (PROMPT_HINT + "\n\n" if a.hint else "") + r.text
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        else:
            print(text)
        map_path = Path(a.map) if a.map else src.with_suffix(".map.json")
        total = r.counts
    ms = (time.perf_counter() - t0) * 1000
    if map_path:
        _save_map(map_path, session.mapping)
    print(f"\n[隐盾] 敏感项 {sum(total.values())} 个（{_summary(total)}），用时 {ms:.1f} ms"
          + (f"；映射表 → {map_path}（含原文，只留本机）" if map_path else "；管道模式未存映射表，需要还原请加 --map"), file=sys.stderr)
    return 0


def cmd_restore(a: argparse.Namespace) -> int:
    text = sys.stdin.read() if a.src == "-" else read_path(a.src)
    mapping = _load_map(a.map)
    if not mapping:
        raise SystemExit(f"映射表 {a.map} 不存在或为空")
    res = restore_detail(text, mapping)
    if a.out:
        Path(a.out).write_text(res.text, encoding="utf-8")
    else:
        print(res.text)
    msg = f"\n[隐盾] 还原 {res.restored} 处" + (f"（其中 {res.fixed} 处模型改了标签写法，已按原标签还原）" if res.fixed else "")
    if res.unknown:
        msg += f"；映射表里没有：{'、'.join(res.unknown)}（多半是模型自己编的，请人工核对）"
    print(msg, file=sys.stderr)
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    red = _redactor(a)
    found = 0
    for f in _files(Path(a.src)):
        r = red.redact(read_path(f))
        sus = suspects(r.text)
        if r.total or sus:
            found += r.total
            print(f"{f}：明文敏感 {r.total} 处（{_summary(r.counts)}）" + (f"；疑似账号 {len(sus)} 处" if sus else ""))
    print(f"[隐盾] 检查完成：{'发现 ' + str(found) + ' 处明文敏感信息' if found else '未发现明文敏感信息'}", file=sys.stderr)
    return 1 if found else 0


# ---------- 剪贴板 ----------
def _clip_get() -> str:
    system = platform.system()
    if system == "Darwin":
        return subprocess.run(["pbpaste"], capture_output=True, env={**os.environ, "LANG": "en_US.UTF-8"}).stdout.decode("utf-8")
    if system == "Windows":
        cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"
        return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True).stdout.decode("utf-8")
    import tkinter
    root = tkinter.Tk(); root.withdraw()
    try:
        return root.clipboard_get()
    finally:
        root.destroy()


def _clip_set(text: str) -> None:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), env={**os.environ, "LANG": "en_US.UTF-8"}, check=True)
    elif system == "Windows":
        cmd = "[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], input=text.encode("utf-8"), check=True)
    else:
        import tkinter
        root = tkinter.Tk(); root.withdraw()
        root.clipboard_clear(); root.clipboard_append(text); root.update()
        root.destroy()


def cmd_clip(a: argparse.Namespace) -> int:
    """剪贴板里有映射表认得的标签 → 还原；否则 → 脱敏（接着上一轮的映射表编号，多轮对话同一客户同一标签）。"""
    if a.new and CLIP_MAP.exists():
        CLIP_MAP.unlink()
    mapping = _load_map(CLIP_MAP)
    text = _clip_get()
    if not text.strip():
        print("[隐盾] 剪贴板是空的：先复制要发给大模型的原文，或大模型的回答。", file=sys.stderr)
        return 1
    res = restore_detail(text, mapping)
    if res.restored:
        _clip_set(res.text)
        print(f"[隐盾] 已还原 {res.restored} 处，剪贴板里是还原后的文字。"
              + (f"映射表里没有：{'、'.join(res.unknown)}" if res.unknown else ""), file=sys.stderr)
        return 0
    session = Session(_redactor(a), mapping)
    r = session.redact(text)
    _clip_set((PROMPT_HINT + "\n\n" if not a.no_hint else "") + r.text)
    _save_map(CLIP_MAP, session.mapping)
    print(f"[隐盾] 已脱敏 {r.total} 处（{_summary(r.counts)}），剪贴板里是脱敏后的文字，直接粘贴给大模型。"
          f"会话映射表 {len(session.mapping)} 条，存在 {CLIP_MAP}；开新话题加 --new。", file=sys.stderr)
    sus = suspects(r.text)
    if sus:
        print(f"[隐盾] 提醒：{'、'.join(sus)} 像账号但没有上下文，未自动脱敏；确认是账号请加到 --names 名单。", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="隐盾：贴给大模型前先脱敏，用完一键还原（本地规则，不联网）",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def lists(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--names", help="客户 / 员工姓名名单文件，一行一个")
        sp.add_argument("--orgs", help="机构名单文件，一行一个")
        sp.add_argument("--keep", help="不脱敏白名单文件（公司热线、自家公司名），一行一个")
        sp.add_argument("--off", help=f"关掉的类型，逗号分隔；可选 {'、'.join(KINDS)}")

    r = sub.add_parser("redact", help="脱敏文件、目录或标准输入")
    r.add_argument("src", help="文件、目录，或 - 表示标准输入")
    r.add_argument("--out", help="输出文件（单文件）或输出目录（目录模式）")
    r.add_argument("--map", help="映射表路径；已存在则接着编号（同一实体沿用同一标签）")
    r.add_argument("--hint", action="store_true", help="最前面加一句提示，请大模型原样保留标签")
    lists(r)
    s = sub.add_parser("restore", help="按映射表还原大模型的回答")
    s.add_argument("src", help="回答文件，或 - 表示标准输入")
    s.add_argument("map", help="脱敏时生成的 .map.json")
    s.add_argument("--out", help="输出文件")
    c = sub.add_parser("check", help="只检查是否含明文敏感信息（发现即退出码 1）")
    c.add_argument("src", help="文件或目录")
    lists(c)
    k = sub.add_parser("clip", help="剪贴板一键脱敏 / 还原")
    k.add_argument("--new", action="store_true", help="开新会话：清空上一轮的映射表")
    k.add_argument("--no-hint", action="store_true", help="不加「请原样保留标签」提示")
    lists(k)

    a = p.parse_args(argv)
    return {"redact": cmd_redact, "restore": cmd_restore, "check": cmd_check, "clip": cmd_clip}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
