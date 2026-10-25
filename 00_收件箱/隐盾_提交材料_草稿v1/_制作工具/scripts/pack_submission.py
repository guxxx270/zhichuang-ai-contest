"""把提交材料打成一个 zip：文件名去掉「_草稿vN」，源码包展开成文件夹，附一页材料说明。

用法：python pack_submission.py <材料目录> <out.zip>
打包时检查 PPT / Word / PDF 里还有没有【待填写】、PDF 是不是比源文件旧；有就列出来，包照样出，方便先看效果。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
TOP = "隐盾_个人赛_工具赛道"
NOTE = Path(__file__).resolve().parent.parent / "content" / "提交包说明.txt"
# PPT（可编辑 + PDF）、视频、技术文档（Word + PDF）；字幕、旁白稿、提交清单、制作工具不进包
PICK = ["01_隐盾_方案PPT_草稿v*.pptx", "01_隐盾_方案PPT_草稿v*.pdf", "02_隐盾_Demo视频_草稿v*.mp4",
        "03_隐盾_工具说明与技术方案_草稿v*.docx", "03_隐盾_工具说明与技术方案_草稿v*.pdf"]
CODE = "04_隐盾_源码_v*_草稿v*.zip"
DRAFT = re.compile(r"_草稿v(\d+)")
TODO = re.compile(r"【待填写[^】]*】")


def latest(pattern: str) -> Path:
    found = sorted(SRC.glob(pattern), key=lambda p: int(DRAFT.search(p.name).group(1)))
    if not found:
        sys.exit(f"缺文件：{SRC / pattern}")
    return found[-1]


def clean(name: str) -> str:
    return DRAFT.sub("", name)


def todos(p: Path) -> list[str]:
    """返回 p 里没补的【待填写】，PPT 带页码。"""
    if p.suffix in (".pptx", ".docx"):
        out = []
        with zipfile.ZipFile(p) as z:
            parts = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml|word/document\.xml", n)]
            for n in sorted(parts, key=lambda n: int(re.sub(r"\D", "", n) or 0)):
                text = re.sub(r"<[^>]+>", "", z.read(n).decode("utf-8", "ignore"))
                num = re.sub(r"\D", "", n)
                page = f"第 {num} 页 " if n.startswith("ppt/") else ""
                out += [page + m for m in TODO.findall(text)]
        return out
    if p.suffix == ".pdf" and shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True).stdout
        return ["待填写"] * text.replace("\n", "").count("待填写")
    return []


files = [latest(pat) for pat in PICK]
code = latest(CODE)
code_dir = clean(code.stem)

warn = []
for p in files:
    left = todos(p)
    if left:
        warn.append(f"  {p.name}：{len(left)} 处" + ("".join(f"\n    {t}" for t in left) if p.suffix != ".pdf" else ""))
    src = p.with_suffix(".pptx" if p.name.startswith("01_") else ".docx")
    if p.suffix == ".pdf" and src.exists() and src.stat().st_mtime > p.stat().st_mtime + 60:
        warn.append(f"  {p.name}：比 {src.name} 旧，改过源文件后记得重新另存 PDF")

OUT.parent.mkdir(parents=True, exist_ok=True)
tmp = OUT.with_name(OUT.name + ".part")
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    note = NOTE.read_text(encoding="utf-8").replace("{源码目录}", code_dir)
    # 记事本友好：带 BOM、CRLF
    z.writestr(f"{TOP}/00_材料说明.txt", ("﻿" + note.replace("\r\n", "\n").replace("\n", "\r\n")).encode("utf-8"))
    for p in files:
        # 视频本身已压缩，存储即可
        z.write(p, f"{TOP}/{clean(p.name)}", zipfile.ZIP_STORED if p.suffix == ".mp4" else zipfile.ZIP_DEFLATED)
    # 源码展开成文件夹，保留 .command 的可执行位
    with zipfile.ZipFile(code) as zc:
        for info in zc.infolist():
            rel = info.filename.split("/", 1)[1]
            if not rel or info.is_dir():
                continue
            item = zipfile.ZipInfo(f"{TOP}/{code_dir}/{rel}", info.date_time)
            item.external_attr = info.external_attr
            item.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(item, zc.read(info))
os.replace(tmp, OUT)

with zipfile.ZipFile(OUT) as z:
    names = z.namelist()
    assert z.testzip() is None
print(f"{OUT} · {len(names)} 个文件 · {OUT.stat().st_size / 1024 / 1024:.1f} MB")
for n in names:
    if f"/{code_dir}/" not in n:
        print("  " + n)
print(f"  {TOP}/{code_dir}/ · {sum(f'/{code_dir}/' in n for n in names)} 个文件")
if warn:
    print("\n还没补完：\n" + "\n".join(warn))
