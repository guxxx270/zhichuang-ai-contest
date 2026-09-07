"""材料读取：研报 / 公告 / 纪要 → Doc。支持 md / txt / pdf，带 front matter（title/publisher/type/date）。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from . import config
from .symbols import detect_symbols

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_DATE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
_FNAME = re.compile(r"^(20\d{2}-\d{2}-\d{2})_(.+?)(?:_(.+?))?$")


class Doc(BaseModel):
    id: str
    title: str
    publisher: str = "未知机构"
    doc_type: str = "研报"            # 研报 / 公告 / 纪要 / 资讯
    published_on: date
    symbols: list[str] = Field(default_factory=list)
    text: str
    path: str = ""

    def sentences(self) -> list[str]:
        return [s.strip() for s in re.split(r"[。！？!?\n]+", self.text) if len(s.strip()) > 3]


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except ImportError as e:
            raise RuntimeError("读取 PDF 需要 pypdf：pip install pypdf") from e
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_doc(raw: str, source_name: str, fallback_date: Optional[date] = None) -> Doc:
    meta: dict[str, str] = {}
    body = raw
    m = _FRONT.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"')
        body = raw[m.end():]
    stem = Path(source_name).stem
    fm = _FNAME.match(stem)
    d = None
    if meta.get("date"):
        dm = _DATE.search(meta["date"])
        d = date(*map(int, dm.groups())) if dm else None
    if d is None and fm:
        d = date.fromisoformat(fm.group(1))
    if d is None:
        dm = _DATE.search(body)
        d = date(*map(int, dm.groups())) if dm else (fallback_date or date.today())
    title = meta.get("title") or (fm.group(2) if fm else stem)
    publisher = meta.get("publisher") or (fm.group(3) if fm and fm.group(3) else "未知机构")
    doc_type = meta.get("type") or ("公告" if "公告" in title or "通知" in title else "研报")
    body = body.strip()
    return Doc(id=stem, title=title, publisher=publisher, doc_type=doc_type, published_on=d,
               symbols=detect_symbols(body), text=body, path=source_name)


def load_docs(folder: Optional[Path] = None, files: Optional[Iterable[Path]] = None) -> list[Doc]:
    paths = list(files) if files else sorted((folder or config.DOCS_DIR).glob("*"))
    docs = []
    for p in paths:
        if p.suffix.lower() not in (".md", ".txt", ".pdf") or p.name.startswith("_"):
            continue
        docs.append(parse_doc(read_text(p), p.name))
    return docs
