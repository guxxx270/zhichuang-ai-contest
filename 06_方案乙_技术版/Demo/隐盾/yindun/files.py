"""读文件成纯文本：txt / md / csv / log / json 等文本文件，以及 Word（.docx）。只用标准库，不装额外依赖。"""
from __future__ import annotations

import html
import io
import re
import zipfile
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".ini", ".conf", ".sql", ".xml", ".html", ".eml"}
SUFFIXES = TEXT_SUFFIXES | {".docx"}
MAX_BYTES = 5 * 1024 * 1024   # 5 MB：贴给大模型的材料远小于这个量


def decode(data: bytes) -> str:
    """UTF-8（含 BOM）优先，不行再按 GB18030（Windows 记事本另存的中文文件）。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def docx_text(data: bytes) -> str:
    """Word 正文按段落取文字（含表格单元格），一段一行；页眉页脚、批注不取。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    paras = []
    for p in re.findall(r"<w:p[ >][\s\S]*?</w:p>", xml):
        p = p.replace("<w:tab/>", "<w:t>\t</w:t>").replace("<w:br/>", "<w:t>\n</w:t>")
        paras.append(html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>([\s\S]*?)</w:t>", p))))
    return "\n".join(paras).strip("\n")


def read_bytes(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if len(data) > MAX_BYTES:
        raise ValueError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB，请拆开再处理")
    if suffix == ".docx":
        return docx_text(data)
    if suffix and suffix not in TEXT_SUFFIXES:
        raise ValueError(f"暂不支持 {suffix} 文件；支持 {'、'.join(sorted(SUFFIXES))}")
    return decode(data)


def read_path(path: str | Path) -> str:
    p = Path(path)
    return read_bytes(p.name, p.read_bytes())
