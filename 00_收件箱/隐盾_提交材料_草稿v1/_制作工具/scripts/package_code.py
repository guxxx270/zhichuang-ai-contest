"""打包提交用源码：Demo/隐盾 → zip。只放 git 跟踪的文件（虚拟环境、缓存、*.map.json 映射表都不在里面）。

用法：python package_code.py <repo_root> <out.zip>
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(sys.argv[1])
OUT = Path(sys.argv[2])
APP = "06_方案乙_技术版/Demo/隐盾"
# 只取 git 跟踪的文件：.venv、__pycache__、*.map.json 都在 .gitignore 里，天然排除
files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", APP], capture_output=True, check=True).stdout.decode().split("\0")
files = [f for f in files if f]
BLOCK = (".env",)
OUT.parent.mkdir(parents=True, exist_ok=True)
n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in files:
        p = REPO / f
        name = Path(f).name
        if not p.is_file() or name in BLOCK or name.endswith(".map.json"):
            continue
        z.write(p, arcname="隐盾/" + f[len(APP) + 1:])
        n += 1
print(f"{OUT} · {n} files · {OUT.stat().st_size / 1024 / 1024:.1f} MB")
