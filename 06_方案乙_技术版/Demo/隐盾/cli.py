"""隐盾 · 命令行。
  python cli.py redact 输入.txt            → 输出脱敏文本到标准输出，映射表写到 输入.map.json
  python cli.py restore 回答.txt 输入.map.json → 还原后输出
  也可用管道：cat 文件 | python cli.py redact -
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from yindun.privacy import Redactor


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__); raise SystemExit(1)
    cmd, src = sys.argv[1], sys.argv[2]
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    if cmd == "redact":
        r = Redactor().redact(text)
        print(r.text)
        if src != "-":
            Path(src).with_suffix(".map.json").write_text(json.dumps(r.mapping, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n[隐盾] 脱敏 {r.total} 处：{r.counts}；映射表 → {Path(src).with_suffix('.map.json')}", file=sys.stderr)
    elif cmd == "restore":
        mapping = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        print(Redactor.restore(text, mapping))
    else:
        print(__doc__); raise SystemExit(1)


if __name__ == "__main__":
    main()
