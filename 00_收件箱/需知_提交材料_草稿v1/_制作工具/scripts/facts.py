"""Collect hard numbers for the materials straight from the code/data (run inside a copy of the app)."""
import json, os, re, sys
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root))
os.environ.setdefault("LLM_MODE", "mock")
from xuzhi import knowledge
probes = knowledge.probes(); systems = knowledge.systems(); hist = knowledge.history()
cat = json.loads((root / "knowledge/system_catalog.json").read_text(encoding="utf-8"))
syslist = cat if isinstance(cat, list) else cat.get("systems", cat)
n_if = sum(len(s.get("interfaces", []) or []) for s in syslist) if isinstance(syslist, list) else None
facts = {
  "probes": len(probes), "probe_categories": sorted({p.get("category", "") for p in probes if isinstance(p, dict)}),
  "systems": len(systems), "data_domains": knowledge.data_domains(), "interfaces": n_if,
  "history": len(hist), "samples": sorted(p.name for p in (root / "data/samples").glob("*.md")),
  "eval_cases": len(json.loads((root / "data/eval/eval_set.json").read_text(encoding="utf-8")).get("cases", [])),
}
import csv
with open(root / "data/jira/示例_迭代导出（虚构）.csv", encoding="utf-8-sig") as f:
    facts["jira_sample_rows"] = sum(1 for _ in csv.reader(f)) - 1
api = (root / "api.py").read_text(encoding="utf-8")
facts["rest_routes"] = re.findall(r'@app\.(get|post|put|delete)\("([^"]+)"', api)
mcp = (root / "mcp_server.py").read_text(encoding="utf-8")
facts["mcp_tools"] = re.findall(r"def (xuzhi_\w+)\(", mcp)
facts["py_files"] = sum(1 for p in root.rglob("*.py") if ".venv" not in p.parts and "tests" not in p.parts)
facts["py_lines"] = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in root.rglob("*.py") if ".venv" not in p.parts and "tests" not in p.parts)
facts["test_files"] = len(list((root / "tests").glob("test_*.py")))
print(json.dumps(facts, ensure_ascii=False, indent=1))
