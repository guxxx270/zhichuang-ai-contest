"""方案报告 / 工具说明：Markdown → Word（封面、目录、页眉、插图）→ PDF。

用法（在 _制作工具 目录下）：
    python scripts/build_docs.py <in.md> <out.docx> "<页眉文字>" [--pdf]

- 「图：S02_clarify」这类行换成 shots/ 下的截图，「图：F01_architecture」换成 deck/figs/ 下的配图，自动编「图 N」。
- 【待团队填写：…】黄底高亮，方便定稿时逐个找。
- Markdown 里「Markdown 版以「图：…」引用…」只对 md 成立，Word 版换成截图来源说明。
- --pdf：再用 LibreOffice 转一份 PDF，放在 .docx 旁边（需要 deck/render.sh 的依赖）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORD_NOTE = "文中截图均来自 Demo v0.6 实际运行（虚构数据），图号见各图下方。"


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--pdf"]
    if len(args) != 3:
        sys.exit(__doc__)
    src, out, header = Path(args[0]).resolve(), Path(args[1]).resolve(), args[2]
    md = re.sub(r"Markdown 版以[^。]*。", WORD_NOTE, src.read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_md = Path(tmp) / src.name
        tmp_md.write_text(md, encoding="utf-8")
        subprocess.run(["node", str(ROOT / "deck/md2docx.js"), str(tmp_md), str(out), str(ROOT / "shots"), header,
                        str(ROOT / "deck/figs")], check=True, cwd=ROOT / "deck")
        if "--pdf" in sys.argv:
            subprocess.run(["bash", str(ROOT / "deck/render.sh"), str(out), tmp, "40"], check=True, stdout=subprocess.DEVNULL)
            shutil.move(str(Path(tmp) / (out.stem + ".pdf")), out.with_suffix(".pdf"))
    print("wrote", out, "(+ pdf)" if "--pdf" in sys.argv else "")


if __name__ == "__main__":
    main()
