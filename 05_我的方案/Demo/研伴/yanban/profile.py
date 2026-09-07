"""「我的」：个人关注画像。技能公共，伙伴私有——同一批材料，按画像给每个人不同的晨读。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from . import config
from .symbols import SECTORS, name_of


class Profile(BaseModel):
    name: str = "小林"
    role: str = "研究员"                       # 研究员 / 投资经理 / 资管产品经理
    department: str = "研究所"
    watch: list[str] = Field(default_factory=lambda: ["RB", "I", "CU", "SC", "M"])   # 关注品种代码
    horizon: str = "中期"                      # 短期 / 中期 / 中长期
    brief_style: str = "简洁"                  # 简洁 / 详细
    weekly_template: str = "weekly_report.md"
    notes: list[str] = Field(default_factory=list)   # 记住的偏好，如「口径以交易所结算价为准」

    def watch_names(self) -> list[str]:
        return [name_of(c) for c in self.watch]

    def sectors(self) -> list[str]:
        seen: list[str] = []
        for c in self.watch:
            s = SECTORS.get(c, "其他")
            if s not in seen:
                seen.append(s)
        return seen


def load_profile(path: Optional[Path] = None) -> Profile:
    p = Path(path or config.PROFILE_PATH)
    if p.exists():
        return Profile(**json.loads(p.read_text(encoding="utf-8")))
    prof = Profile()
    save_profile(prof, p)
    return prof


def save_profile(profile: Profile, path: Optional[Path] = None) -> None:
    p = Path(path or config.PROFILE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
