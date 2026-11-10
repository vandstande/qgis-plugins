#!/usr/bin/env python3
"""
Byg og vedligehold QGIS-plugin-repository'et.

Kommandoer:
  python tools/build.py sync                 Skriv fælles felter (plugins.toml) ind i alle metadata.txt
  python tools/build.py sync --check         Fejl hvis en metadata.txt ikke er synkroniseret
  python tools/build.py check                Valider alle plugins (uden at bygge)
  python tools/build.py bump <mappe> [patch|minor|major] -m "Hvad er ændret"
                                             Hæv version og tilføj linje i changelog
  python tools/build.py import <fil.zip> <gruppe>
                                             Tilføj et plugin fra en zip-fil
  python tools/build.py build [--out _site] [--previous URL|auto|none]
                                             Byg zip-filer (<mappe>.<version>.zip), plugins.xml, manifest.json og index.html

Kun Python-standardbiblioteket bruges (Python 3.11+).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "plugins"
CONFIG_FILE = ROOT / "plugins.toml"
WEB_DIR = ROOT / "tools" / "web"

REQUIRED = ["name", "qgisMinimumVersion", "description", "about", "version", "author", "email"]
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".pytest_cache", "test_data_local"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".bak", ".orig", ".tmp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", ".gitignore"}
BINARY_SUFFIXES = {".exe", ".dll", ".so", ".pyd", ".dylib"}
MAX_ZIP_BYTES = 20 * 1024 * 1024
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
#  metadata.txt – linjebaseret parser der bevarer kommentarer og rækkefølge
# ---------------------------------------------------------------------------
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s?(.*)$")


class Metadata:
    def __init__(self, text: str):
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()

    # Find nøgler i [general]: {key: (start, slut_eksklusiv, værdi)}
    def _spans(self) -> dict[str, tuple[int, int, str]]:
        spans: dict[str, tuple[int, int, str]] = {}
        section = None
        i = 0
        n = len(self.lines)
        while i < n:
            line = self.lines[i]
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1].strip().lower()
                i += 1
                continue
            m = KEY_RE.match(line) if section == "general" else None
            if m:
                key, first = m.group(1), m.group(2).rstrip()
                parts = [first]
                j = i + 1
                while j < n and self.lines[j][:1] in (" ", "\t") and self.lines[j].strip():
                    parts.append(self.lines[j].strip())
                    j += 1
                spans[key] = (i, j, "\n".join(parts).strip())
                i = j
                continue
            i += 1
        return spans

    def get(self, key: str, default: str = "") -> str:
        sp = self._spans().get(key)
        return sp[2] if sp else default

    def as_dict(self) -> dict[str, str]:
        return {k: v[2] for k, v in self._spans().items()}

    @staticmethod
    def _format(key: str, value: str) -> list[str]:
        rows = str(value).split("\n")
        return [f"{key}={rows[0]}"] + [f"    {r}" for r in rows[1:]]

    def set(self, key: str, value: str) -> None:
        spans = self._spans()
        new = self._format(key, value)
        if key in spans:
            a, b, _ = spans[key]
            self.lines[a:b] = new
            return
        if spans:
            insert_at = max(b for _, b, _ in spans.values())
        else:
            if not any(l.strip().lower() == "[general]" for l in self.lines):
                self.lines.insert(0, "[general]")
            insert_at = next(i for i, l in enumerate(self.lines) if l.strip().lower() == "[general]") + 1
        self.lines[insert_at:insert_at] = new

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def text_without(self, keys: set[str]) -> str:
        spans = self._spans()
        drop = set()
        for k in keys:
            if k in spans:
                a, b, _ = spans[k]
                drop.update(range(a, b))
        return "\n".join(l for i, l in enumerate(self.lines) if i not in drop) + "\n"


# ---------------------------------------------------------------------------
#  Konfiguration
# ---------------------------------------------------------------------------
@dataclass
class Config:
    raw: dict
    github_user: str
    repo_name: str
    base_url: str
    title: str
    shared: dict[str, str]
    groups: dict[str, dict]
    plugins: dict[str, str]

    @property
    def repo_web(self) -> str:
        return f"https://github.com/{self.github_user}/{self.repo_name}"

    def managed_fields(self, folder: str) -> dict[str, str]:
        group = self.groups[self.plugins[folder]]
        fields = dict(self.shared)
        fields["homepage"] = group["homepage"]
        fields["repository"] = f"{self.repo_web}/tree/main/plugins/{folder}"
        fields["tracker"] = f"{self.repo_web}/issues"
        return fields

    @property
    def managed_keys(self) -> set[str]:
        return set(self.shared) | {"homepage", "repository", "tracker"}


def load_config() -> Config:
    raw = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    repo = raw.get("repo", {})
    user = repo.get("github_bruger", "").strip()
    name = repo.get("repo_navn", "qgis-plugins").strip()
    domain = repo.get("eget_domaene", "").strip().rstrip("/")
    if domain:
        base = domain if domain.startswith("http") else f"https://{domain}"
    else:
        base = f"https://{user}.github.io/{name}"
    groups = raw.get("grupper", {})
    plugins = {k: v for k, v in raw.get("plugins", {}).items()}
    for folder, g in plugins.items():
        if g not in groups:
            raise BuildError(f"plugins.toml: '{folder}' peger på ukendt gruppe '{g}'")
    return Config(
        raw=raw, github_user=user, repo_name=name, base_url=base + "/",
        title=repo.get("titel", "QGIS plugins"),
        shared={k: str(v) for k, v in raw.get("faelles", {}).items()},
        groups=groups, plugins=plugins,
    )


# ---------------------------------------------------------------------------
#  Plugins
# ---------------------------------------------------------------------------
@dataclass
class Plugin:
    folder: str
    path: Path
    meta: Metadata
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def md(self) -> dict[str, str]:
        return self.meta.as_dict()


def plugin_files(path: Path) -> list[Path]:
    files = []
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.is_dir() or p.suffix.lower() in EXCLUDE_SUFFIXES or p.name in EXCLUDE_NAMES:
            continue
        files.append(p)
    return files


def discover(cfg: Config) -> list[Plugin]:
    found = sorted(d.name for d in PLUGINS_DIR.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))
    unknown = [f for f in found if f not in cfg.plugins]
    missing = [f for f in cfg.plugins if f not in found]
    if unknown:
        raise BuildError("Mapper i plugins/ der ikke står i plugins.toml [plugins]: " + ", ".join(unknown))
    if missing:
        raise BuildError("plugins.toml nævner mapper der ikke findes i plugins/: " + ", ".join(missing))
    result = []
    # Rækkefølgen følger [plugins] i plugins.toml (bruges på oversigtssiden og i plugins.xml)
    for folder in cfg.plugins:
        mfile = PLUGINS_DIR / folder / "metadata.txt"
        if not mfile.exists():
            raise BuildError(f"{folder}: mangler metadata.txt")
        result.append(Plugin(folder, PLUGINS_DIR / folder, Metadata(mfile.read_text(encoding="utf-8-sig"))))
    return result


def apply_shared(cfg: Config, p: Plugin) -> bool:
    before = p.meta.text()
    for k, v in cfg.managed_fields(p.folder).items():
        p.meta.set(k, v)
    return p.meta.text() != before


def version_tuple(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v)
    return tuple(int(x) for x in nums) if nums else (0,)


def validate(cfg: Config, plugins: list[Plugin]) -> None:
    names: dict[str, str] = {}
    for p in plugins:
        md = p.md
        if not MODULE_RE.match(p.folder):
            p.errors.append(f"mappenavnet '{p.folder}' er ikke et gyldigt Python-modulnavn (brug a-z, 0-9, _ og ingen æøå)")
        for k in REQUIRED:
            if not md.get(k):
                p.errors.append(f"metadata.txt mangler '{k}'")
        if not (p.path / "__init__.py").exists():
            p.errors.append("mangler __init__.py")
        elif "classFactory" not in (p.path / "__init__.py").read_text(encoding="utf-8", errors="ignore"):
            p.errors.append("__init__.py indeholder ikke classFactory()")
        if md.get("version") and not re.match(r"^\d+(\.\d+){0,3}$", md["version"]):
            p.warnings.append(f"version '{md['version']}' bør være på formen 1.2.3")
        icon = md.get("icon")
        if icon and not (p.path / icon).exists():
            p.errors.append(f"icon '{icon}' findes ikke")
        if not icon:
            p.warnings.append("intet icon angivet i metadata.txt")
        if not md.get("changelog"):
            p.warnings.append("ingen changelog i metadata.txt")
        for f in plugin_files(p.path):
            if f.suffix.lower() in BINARY_SUFFIXES:
                p.warnings.append(f"binær fil: {f.relative_to(p.path)}")
        n = md.get("name", "")
        if n in names:
            p.errors.append(f"name '{n}' bruges også af {names[n]}")
        names[n] = p.folder
        for dep in [d.strip() for d in md.get("plugin_dependencies", "").split(",") if d.strip()]:
            depname = re.split(r"[=<>!]", dep)[0].strip()
            if depname not in {q.md.get("name") for q in plugins}:
                p.warnings.append(f"plugin_dependencies '{depname}' findes ikke i dette repository")


def content_hash(cfg: Config, p: Plugin) -> str:
    """Hash af pluginets indhold, uden de centralt styrede felter.
    Bruges til at opdage ændringer der ikke har fået ny version."""
    h = hashlib.sha256()
    for f in plugin_files(p.path):
        rel = f.relative_to(p.path).as_posix()
        h.update(rel.encode() + b"\0")
        if rel == "metadata.txt":
            h.update(p.meta.text_without(cfg.managed_keys | {"version", "changelog"}).encode())
        else:
            h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]


def git_date(path: Path, first: bool = False) -> str | None:
    args = ["git", "log", "--format=%cI"]
    args += ["--reverse"] if first else ["-1"]
    try:
        out = subprocess.run(args + ["--", str(path)], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        lines = [l for l in out.splitlines() if l.strip()]
        return (lines[0] if first else lines[-1]) if lines else None
    except Exception:
        return None


def report(plugins: list[Plugin]) -> bool:
    ok = True
    for p in plugins:
        for w in p.warnings:
            print(f"  ADVARSEL {p.folder}: {w}")
        for e in p.errors:
            print(f"  FEJL     {p.folder}: {e}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
#  Kommandoer
# ---------------------------------------------------------------------------
def cmd_sync(args) -> int:
    cfg = load_config()
    changed = []
    for p in discover(cfg):
        if apply_shared(cfg, p):
            changed.append(p.folder)
            if not args.check:
                (p.path / "metadata.txt").write_text(p.meta.text(), encoding="utf-8")
    if args.check and changed:
        print("Ikke synkroniseret med plugins.toml: " + ", ".join(changed))
        print("Kør: python tools/build.py sync")
        return 1
    print(("Opdateret: " + ", ".join(changed)) if changed else "Alle metadata.txt er synkroniseret.")
    return 0


def cmd_check(args) -> int:
    cfg = load_config()
    plugins = discover(cfg)
    for p in plugins:
        apply_shared(cfg, p)
    validate(cfg, plugins)
    ok = report(plugins)
    print("OK" if ok else "Der er fejl.")
    return 0 if ok else 1


def cmd_bump(args) -> int:
    cfg = load_config()
    plugins = {p.folder: p for p in discover(cfg)}
    p = plugins.get(args.folder)
    if not p:
        raise BuildError(f"Ukendt plugin-mappe '{args.folder}'. Findes: {', '.join(plugins)}")
    old = p.meta.get("version", "0.0.0")
    parts = list(version_tuple(old)) + [0, 0, 0]
    major, minor, patch = parts[:3]
    if args.part == "major":
        major, minor, patch = major + 1, 0, 0
    elif args.part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"
    p.meta.set("version", new)
    if args.message:
        old_cl = p.meta.get("changelog")
        p.meta.set("changelog", f"{new} - {args.message}" + (f"\n{old_cl}" if old_cl else ""))
    (p.path / "metadata.txt").write_text(p.meta.text(), encoding="utf-8")
    print(f"{p.folder}: {old} -> {new}")
    return 0


def cmd_import(args) -> int:
    """Pak en plugin-zip ud i plugins/ og tilføj den til plugins.toml."""
    cfg = load_config()
    if args.group not in cfg.groups:
        raise BuildError(f"Ukendt gruppe '{args.group}'. Findes: {', '.join(cfg.groups)}")
    zpath = Path(args.zip)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if not n.startswith("__MACOSX/")]
        tops = {n.split("/")[0] for n in names if "/" in n}
        if len(tops) != 1:
            raise BuildError(f"{zpath.name}: zip skal indeholde præcis én topmappe (fandt: {', '.join(sorted(tops)) or 'ingen'})")
        folder = tops.pop()
        dest = PLUGINS_DIR / folder
        if dest.exists() and not args.force:
            raise BuildError(f"plugins/{folder} findes allerede (brug --force for at overskrive)")
        if dest.exists():
            shutil.rmtree(dest)
        for n in names:
            rel = Path(n)
            if any(part in EXCLUDE_DIRS for part in rel.parts) or rel.suffix in EXCLUDE_SUFFIXES or n.endswith("/"):
                continue
            target = (PLUGINS_DIR / rel).resolve()
            if PLUGINS_DIR.resolve() not in target.parents:
                raise BuildError(f"usikker sti i zip: {n}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
    if folder not in cfg.plugins:
        text = CONFIG_FILE.read_text(encoding="utf-8").rstrip("\n")
        CONFIG_FILE.write_text(text + f'\n{folder} = "{args.group}"\n', encoding="utf-8")
    print(f"Importeret {zpath.name} -> plugins/{folder} (gruppe: {args.group})")
    return 0


def fetch_previous(cfg: Config, src: str) -> dict | None:
    if src == "none":
        return None
    url = cfg.base_url + "manifest.json" if src == "auto" else src
    try:
        if url.startswith("http"):
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        return json.loads(Path(url).read_text(encoding="utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  (ingen tidligere manifest.json fundet på {url}: {e} – springer versionstjek over)")
        return None


def cmd_build(args) -> int:
    cfg = load_config()
    if cfg.github_user in ("", "DIT-GITHUB-BRUGERNAVN"):
        print("  ADVARSEL: github_bruger er ikke udfyldt i plugins.toml")
    plugins = discover(cfg)
    for p in plugins:
        apply_shared(cfg, p)
    validate(cfg, plugins)

    previous = fetch_previous(cfg, args.previous)
    prev_by_folder = {x["mappe"]: x for x in (previous or {}).get("plugins", [])}

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    hashes = {}
    for p in plugins:
        hashes[p.folder] = content_hash(cfg, p)
        prev = prev_by_folder.get(p.folder)
        if prev and prev.get("hash") != hashes[p.folder]:
            if version_tuple(p.md["version"]) <= version_tuple(prev["version"]):
                p.errors.append(
                    f"koden er ændret, men version er stadig {p.md['version']} "
                    f"(publiceret: {prev['version']}). Kør: python tools/build.py bump {p.folder} -m \"...\""
                )
    if not report(plugins):
        print("Build stoppet pga. fejl.")
        return 1

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "zips").mkdir(parents=True)
    (out / "icons").mkdir()

    entries = []
    for p in plugins:
        md = p.md
        ver = md["version"]
        zname = f"{p.folder}.{ver}.zip"  # QGIS bruger teksten før første punktum som plugin-id
        zpath = out / "zips" / zname
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in plugin_files(p.path):
                rel = f.relative_to(p.path).as_posix()
                arc = f"{p.folder}/{rel}"
                if rel == "metadata.txt":
                    z.writestr(arc, p.meta.text())
                else:
                    z.write(f, arc)
        if zpath.stat().st_size > MAX_ZIP_BYTES:
            print(f"  FEJL {p.folder}: zip er større end 20 MB")
            return 1
        shutil.copy(zpath, out / "zips" / f"{p.folder}.zip")  # fast link til hjemmesiden

        icon_url = ""
        if md.get("icon") and (p.path / md["icon"]).exists():
            ext = Path(md["icon"]).suffix
            shutil.copy(p.path / md["icon"], out / "icons" / f"{p.folder}{ext}")
            icon_url = f"{cfg.base_url}icons/{p.folder}{ext}"

        group_key = cfg.plugins[p.folder]
        entries.append({
            "mappe": p.folder,
            "gruppe": group_key,
            "name": md["name"],
            "version": ver,
            "description": md.get("description", ""),
            "about": md.get("about", ""),
            "changelog": md.get("changelog", ""),
            "qgis_minimum_version": md.get("qgisMinimumVersion", ""),
            "qgis_maximum_version": md.get("qgisMaximumVersion", ""),
            "homepage": md.get("homepage", ""),
            "repository": md.get("repository", ""),
            "tracker": md.get("tracker", ""),
            "author": md.get("author", ""),
            "tags": md.get("tags", ""),
            "experimental": md.get("experimental", "False").strip().lower() in ("true", "1", "yes"),
            "deprecated": md.get("deprecated", "False").strip().lower() in ("true", "1", "yes"),
            "plugin_dependencies": md.get("plugin_dependencies", ""),
            "server": md.get("server", "False"),
            "file_name": zname,
            "download_url": f"{cfg.base_url}zips/{zname}",
            "download_url_latest": f"{cfg.base_url}zips/{p.folder}.zip",
            "icon": icon_url,
            "create_date": git_date(p.path, first=True) or now,
            "update_date": git_date(p.path) or now,
            "hash": hashes[p.folder],
        })

    (out / "plugins.xml").write_text(render_xml(entries), encoding="utf-8")
    manifest = {
        "titel": cfg.title,
        "repository_url": cfg.base_url + "plugins.xml",
        "bygget": now,
        "grupper": {k: {"navn": v.get("navn", k), "homepage": v.get("homepage", "")} for k, v in cfg.groups.items()},
        "plugins": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "index.html").write_text(render_index(cfg, manifest), encoding="utf-8")
    for f in WEB_DIR.glob("*"):
        if f.is_file():
            shutil.copy(f, out / f.name)
    (out / ".nojekyll").write_text("")
    if cfg.raw.get("repo", {}).get("eget_domaene"):
        (out / "CNAME").write_text(cfg.raw["repo"]["eget_domaene"].replace("https://", "").strip("/") + "\n")

    print(f"Bygget {len(entries)} plugins til {out}")
    for e in entries:
        print(f"  {e['name']:<28} {e['version']:<10} {e['file_name']}")
    return 0


def render_xml(entries: list[dict]) -> str:
    def cdata(s: str) -> str:
        return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"

    def b(v) -> str:
        return "True" if v in (True, "True", "true") else "False"

    rows = ['<?xml version="1.0" encoding="UTF-8"?>', "<plugins>"]
    for e in entries:
        rows.append(f'  <pyqgis_plugin name="{xml_escape(e["name"], {chr(34): "&quot;"})}" version="{xml_escape(e["version"])}">')
        rows.append(f"    <description>{cdata(e['description'])}</description>")
        rows.append(f"    <about>{cdata(e['about'])}</about>")
        rows.append(f"    <version>{xml_escape(e['version'])}</version>")
        rows.append("    <trusted>False</trusted>")
        rows.append(f"    <qgis_minimum_version>{xml_escape(e['qgis_minimum_version'])}</qgis_minimum_version>")
        rows.append(f"    <qgis_maximum_version>{xml_escape(e['qgis_maximum_version'])}</qgis_maximum_version>")
        rows.append(f"    <homepage>{cdata(e['homepage'])}</homepage>")
        rows.append(f"    <file_name>{xml_escape(e['file_name'])}</file_name>")
        rows.append(f"    <icon>{cdata(e['icon'])}</icon>")
        rows.append(f"    <author_name>{cdata(e['author'])}</author_name>")
        rows.append(f"    <download_url>{cdata(e['download_url'])}</download_url>")
        rows.append(f"    <uploaded_by>{cdata(e['author'])}</uploaded_by>")
        rows.append(f"    <create_date>{e['create_date']}</create_date>")
        rows.append(f"    <update_date>{e['update_date']}</update_date>")
        rows.append(f"    <experimental>{b(e['experimental'])}</experimental>")
        rows.append(f"    <deprecated>{b(e['deprecated'])}</deprecated>")
        rows.append(f"    <tracker>{cdata(e['tracker'])}</tracker>")
        rows.append(f"    <repository>{cdata(e['repository'])}</repository>")
        rows.append(f"    <tags>{cdata(e['tags'])}</tags>")
        rows.append("    <downloads>0</downloads>")
        rows.append("    <average_vote>0</average_vote>")
        rows.append("    <rating_votes>0</rating_votes>")
        rows.append(f"    <plugin_dependencies>{cdata(e['plugin_dependencies'])}</plugin_dependencies>")
        rows.append("    <external_dependencies></external_dependencies>")
        rows.append(f"    <server>{b(e['server'])}</server>")
        rows.append("  </pyqgis_plugin>")
    rows.append("</plugins>")
    return "\n".join(rows) + "\n"


def render_index(cfg: Config, manifest: dict) -> str:
    e = html.escape
    sections = []
    for gkey, g in manifest["grupper"].items():
        items = [p for p in manifest["plugins"] if p["gruppe"] == gkey]
        if not items:
            continue
        cards = []
        for p in items:
            icon = f'<img src="{e(p["icon"])}" alt="" width="40" height="40">' if p["icon"] else '<div class="noicon"></div>'
            exp = ' <span class="tag">eksperimentel</span>' if p["experimental"] else ""
            cards.append(f"""
      <article class="card">
        {icon}
        <div>
          <h3>{e(p['name'])} <span class="ver">v{e(p['version'])}</span>{exp}</h3>
          <p>{e(p['description'])}</p>
          <p class="links"><a href="{e(p['download_url_latest'])}">Download zip</a> ·
             <a href="{e(p['homepage'])}">Hjemmeside</a> ·
             <a href="{e(p['repository'])}">Kildekode</a></p>
        </div>
      </article>""")
        sections.append(f"""
    <section>
      <h2><a href="{e(g['homepage'])}">{e(g['navn'])}</a></h2>{''.join(cards)}
    </section>""")
    return f"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(manifest['titel'])}</title>
<style>
  :root {{ --ink:#1d2b33; --muted:#5c6b73; --line:#d9e2e6; --accent:#1f6f8b; --bg:#f6f9fa; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
  main {{ max-width:860px; margin:0 auto; padding:40px 20px 80px; }}
  h1 {{ font-size:28px; margin:0 0 8px; }}
  h2 {{ font-size:20px; margin:40px 0 12px; }}
  h2 a {{ color:inherit; text-decoration:none; border-bottom:2px solid var(--accent); }}
  h3 {{ font-size:17px; margin:0 0 4px; }}
  .ver {{ color:var(--muted); font-weight:400; font-size:14px; }}
  .tag {{ font-size:12px; background:#fff3cd; padding:1px 6px; border-radius:4px; font-weight:400; }}
  .install {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:20px; margin-top:24px; }}
  .url {{ display:flex; gap:8px; margin:12px 0; }}
  .url code {{ flex:1; background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:8px 10px; overflow-x:auto; white-space:nowrap; }}
  button {{ border:0; background:var(--accent); color:#fff; border-radius:6px; padding:8px 14px; cursor:pointer; font:inherit; }}
  ol {{ padding-left:20px; margin:8px 0 0; }}
  .card {{ display:flex; gap:16px; background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:10px; }}
  .card p {{ margin:0 0 6px; color:var(--muted); }}
  .card .links {{ margin:0; font-size:14px; }}
  .noicon {{ width:40px; height:40px; border-radius:8px; background:var(--line); flex:none; }}
  a {{ color:var(--accent); }}
  footer {{ margin-top:48px; color:var(--muted); font-size:13px; }}
</style>
</head>
<body>
<main>
  <h1>{e(manifest['titel'])}</h1>
  <div class="install">
    <strong>Installér og få automatiske opdateringer i QGIS</strong>
    <div class="url"><code id="repo">{e(manifest['repository_url'])}</code><button onclick="navigator.clipboard.writeText(document.getElementById('repo').textContent);this.textContent='Kopieret'">Kopiér</button></div>
    <ol>
      <li>QGIS: <em>Plugins → Administrer og installer plugins → Indstillinger</em></li>
      <li>Tryk <em>Tilføj…</em>, giv repository'et et navn (fx „Vandstande“) og indsæt URL'en ovenfor</li>
      <li>Tryk <em>Genindlæs alle repositories</em> – pluginsene ligger nu under <em>Alle</em></li>
    </ol>
  </div>
  {''.join(sections)}
  <footer>Opdateret {e(manifest['bygget'][:10])}</footer>
</main>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--check", action="store_true"); s.set_defaults(func=cmd_sync)
    c = sub.add_parser("check"); c.set_defaults(func=cmd_check)
    bp = sub.add_parser("bump")
    bp.add_argument("folder"); bp.add_argument("part", nargs="?", default="patch", choices=["patch", "minor", "major"])
    bp.add_argument("-m", "--message", default="")
    bp.set_defaults(func=cmd_bump)
    im = sub.add_parser("import")
    im.add_argument("zip"); im.add_argument("group"); im.add_argument("--force", action="store_true")
    im.set_defaults(func=cmd_import)
    b = sub.add_parser("build")
    b.add_argument("--out", default="_site")
    b.add_argument("--previous", default="none", help="URL/sti til tidligere manifest.json, 'auto' eller 'none'")
    b.set_defaults(func=cmd_build)
    args = ap.parse_args()
    try:
        return args.func(args)
    except BuildError as e:
        print(f"FEJL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
