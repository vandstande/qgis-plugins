#!/usr/bin/env python3
"""Tjek at alle navne fra compat.py er importeret på modulniveau, og at
'from .compat import' aldrig står inde i en tekststreng (fx en skabelon)."""
import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "plugins"
fejl = 0
for f in sorted(ROOT.glob("*/*.py")):
    cp = f.parent / "compat.py"
    if f.name == "compat.py" or not cp.exists():
        continue
    ct = ast.parse(cp.read_text(encoding="utf-8"))
    exported = {"QAction"}
    for n in ct.body:
        if isinstance(n, ast.Assign):
            exported |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
            exported.add(n.name)
    t = ast.parse(f.read_text(encoding="utf-8"))
    top = set()
    for node in t.body:
        for sub in (ast.walk(node) if isinstance(node, (ast.Try, ast.If)) else [node]):
            if isinstance(sub, ast.ImportFrom) and sub.module == "compat" and sub.level == 1:
                top |= {a.asname or a.name for a in sub.names}
    andre = {a.asname or a.name for n in ast.walk(t) if isinstance(n, (ast.Import, ast.ImportFrom))
             and not (isinstance(n, ast.ImportFrom) and n.module == "compat") for a in n.names}
    lokale = {n.id for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    brugt = {n.id for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} & exported
    mangler = brugt - top - andre - lokale
    i_streng = [n.lineno for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "from .compat import" in n.value]
    if mangler or i_streng:
        fejl += 1
        print(f"FEJL {f.relative_to(ROOT)}: mangler import af {sorted(mangler)}"
              + (f", compat-import i tekststreng (linje {i_streng})" if i_streng else ""))
print("OK – alle compat-importer er på plads" if not fejl else f"{fejl} fil(er) med fejl")
sys.exit(1 if fejl else 0)
