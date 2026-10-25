"""方案报告 / 工具说明：Markdown → Word（封面、目录、页眉、插图）→ PDF。

用法（在 _制作工具 目录下）：
    python scripts/build_docs.py <in.md> <out.docx> "<页眉文字>" [--pdf]

- 「图：S02_clarify」这类行换成 shots/ 下的截图，「图：F01_architecture」换成 deck/figs/ 下的配图，自动编「图 N」。
- 【待填写：…】黄底高亮，方便定稿时逐个找。
- Markdown 里「Markdown 版以「图：…」引用…」只对 md 成立，Word 版换成截图来源说明。
- --pdf：再用 LibreOffice 转一份 PDF，放在 .docx 旁边（需要 deck/render.sh 的依赖）。LibreOffice 转换时不刷新目录，
  所以先转一次，按 PDF 页脚把各标题所在页写进 Word 目录，再转一次；Word 打开时还会按自己的排版刷新。
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORD_NOTE = "文中截图均来自隐盾 v0.2 实际运行（虚构数据），图号见各图下方。"


def render(docx: Path, tmp: str) -> Path:
    subprocess.run(["bash", str(ROOT / "deck/render.sh"), str(docx), tmp, "40"], check=True, stdout=subprocess.DEVNULL)
    return Path(tmp) / (docx.stem + ".pdf")


def fill_toc(docx: Path, pdf: Path) -> tuple[int, list[str]]:
    """目录里每条 PAGEREF 写上该标题在 PDF 里所在页的页脚页码；返回 (写入条数, 没找到的标题)。"""
    pages = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    lines = [[re.sub(r"\s+", "", s) for s in p.splitlines() if s.strip()] for p in pages]
    # 页脚「第 N 页」是每页最后一行；封面和目录页没有，正文从 1 起
    labels = []
    for ls in lines:
        m = re.fullmatch(r"第(\d+)页", ls[-1]) if ls else None
        labels.append(m.group(1) if m else None)
    with zipfile.ZipFile(docx) as z:
        infos = z.infolist()
        data = {i.filename: z.read(i.filename) for i in infos}
    xml = data["word/document.xml"].decode("utf-8")
    page, found, missing = next((k for k, lb in enumerate(labels) if lb), 0), {}, []
    for anchor, inner in re.findall(r'<w:hyperlink [^>]*w:anchor="(_Toc\d+)"[^>]*>(.*?)</w:hyperlink>', xml):
        title = re.sub(r"\s+", "", html.unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", inner))))
        # 从上一条所在页往后找：标题独占一行，长标题可能折成两行
        for k in range(page, len(lines)):
            ls = lines[k]
            if labels[k] and any(a == title or a + b == title for a, b in zip(ls, ls[1:] + [""])):
                found[anchor], page = labels[k], k
                break
        else:
            missing.append(title)
    xml = re.sub(r'(PAGEREF (_Toc\d+) \\h</w:instrText><w:fldChar w:fldCharType="separate"/>)(?=<w:fldChar w:fldCharType="end"/>)',
                 lambda m: m.group(1) + (f"<w:t>{found[m.group(2)]}</w:t>" if m.group(2) in found else ""), xml)
    data["word/document.xml"] = xml.encode("utf-8")
    part = docx.with_name(docx.name + ".part")
    with zipfile.ZipFile(part, "w") as z:
        for i in infos:
            z.writestr(i, data[i.filename])
    os.replace(part, docx)
    return len(found), missing


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
            n, missing = fill_toc(out, render(out, tmp))
            shutil.move(str(render(out, tmp)), out.with_suffix(".pdf"))
            print(f"目录页码：写入 {n} 条" + (f"，没找到：{'、'.join(missing)}" if missing else ""))
    print("wrote", out, "(+ pdf)" if "--pdf" in sys.argv else "")


if __name__ == "__main__":
    main()
