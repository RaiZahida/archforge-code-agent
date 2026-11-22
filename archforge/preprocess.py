"""Preprocessor: turn architecture markdown + PlantUML views into structured JSON.

The agent works far better (and cheaper) on a compact, structured spec than on
raw markdown, so this module runs *before* any LLM call and costs no tokens.

Usage:
    python -m archforge.preprocess DOC.md VIEW.md -o spec.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FENCE_RE = re.compile(r"```([\w+-]*)\n(.*?)```", re.S)


# --------------------------------------------------------------------------- #
# Generic markdown helpers
# --------------------------------------------------------------------------- #
def _parse_table(lines: list[str]) -> list[dict]:
    """Parse a GitHub-style markdown table into a list of row dicts."""
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    if len(rows) < 2:
        return []
    header = rows[0]
    body = [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]
    return [dict(zip(header, r)) for r in body]


def _parse_block(text: str) -> dict:
    """Split one markdown section into prose, bullets, tables and code blocks."""
    code = [{"lang": m.group(1) or "text", "content": m.group(2).strip()}
            for m in FENCE_RE.finditer(text)]
    text = FENCE_RE.sub("", text)

    prose, bullets, tables, buf = [], [], [], []
    for line in text.splitlines() + [""]:
        s = line.strip()
        if s.startswith("|"):
            buf.append(s)
            continue
        if buf:
            tables.append(_parse_table(buf))
            buf = []
        if re.match(r"^([*-]|\d+\.)\s+", s):
            bullets.append(re.sub(r"^([*-]|\d+\.)\s+", "", s))
        elif s and not s.startswith("#"):
            prose.append(s)
    out = {}
    if prose:
        out["text"] = " ".join(prose)
    if bullets:
        out["bullets"] = bullets
    if tables:
        out["tables"] = tables
    if code:
        out["code_blocks"] = code
    return out


def _split_headings(md: str, level: int) -> list[tuple[str, str]]:
    """Split markdown on headings of exactly `level` (#, ##, ...)."""
    pat = re.compile(rf"^{'#' * level}\s+(.+)$", re.M)
    # ignore headings that appear inside fenced code
    masked = FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), md)
    marks = [(m.start(), m.end(), m.group(1).strip()) for m in pat.finditer(masked)]
    parts = []
    for i, (s, e, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(md)
        parts.append((title, md[e:end]))
    return parts


# --------------------------------------------------------------------------- #
# Architecture documentation
# --------------------------------------------------------------------------- #
def parse_architecture_doc(md: str) -> dict:
    first = re.search(r"^#\s", md, re.M)
    preamble = md[: first.start()] if first else md
    plan = {}
    for line in preamble.splitlines():
        m = re.match(r"^\s*(\w[\w ]*?):\s*(.+)$", line)
        if m and m.group(1).lower() != "analysis plan":
            plan[m.group(1).strip().lower()] = m.group(2).strip()

    sections = []
    for title, body in _split_headings(md, 1):
        sec = {"title": title, **_parse_block(re.split(r"^##\s", body, maxsplit=1, flags=re.M)[0])}
        subs = [{"title": t, **_parse_block(b)} for t, b in _split_headings(body, 2)]
        if subs:
            sec["subsections"] = subs
        sections.append(sec)

    summary = next((s for s in sections if "summary" in s["title"].lower()), {})
    text = re.split(r"\s*Chosen architectural style", summary.get("text", ""))[0]
    style = re.search(r"architectural style:\s*([^\n.]+)", md, re.I)
    system = re.search(r"The ([A-Z][\w ]+?) system", md)
    return {
        "system_name": system.group(1).strip() if system else None,
        "analysis_plan": plan,
        "summary": text,
        "architectural_style": style.group(1).strip() if style else None,
        "requirements": _extract_requirements(sections),
        "sections": sections,
    }


def _extract_requirements(sections: list[dict]) -> list[dict]:
    """Merge every table row keyed by a requirement ID (FR-/NFR-/ASR-) into one record."""
    merged: dict[str, dict] = {}

    def walk(node):
        for table in node.get("tables", []):
            for row in table:
                key_col = next((k for k in row if "ID" in k), None)
                if not key_col:
                    continue
                m = re.match(r"([A-Z]+-\d+)\s*(?:\((.+)\))?", row[key_col])
                if not m:
                    continue
                rec = merged.setdefault(m.group(1), {"id": m.group(1),
                                                      "type": m.group(1).split("-")[0]})
                if m.group(2):
                    rec.setdefault("short_text", m.group(2))
                for k, v in row.items():
                    if k != key_col and v:
                        rec[re.sub(r"\W+", "_", k.lower().replace("(s)", "s")).strip("_")] = v
        for sub in node.get("subsections", []):
            walk(sub)

    for s in sections:
        walk(s)
    return sorted(merged.values(), key=lambda r: (["FR", "NFR", "ASR"].index(r["type"])
                                                  if r["type"] in ("FR", "NFR", "ASR") else 9, r["id"]))


# --------------------------------------------------------------------------- #
# PlantUML views
# --------------------------------------------------------------------------- #
REL_RE = re.compile(r"^(\S+?)\s*(--\*|\*--|-->|<--|->>|-->>|->|--|\.\.>|<\|--)\s*(\S+?)(?:\s*:\s*(.+))?$")


def _parse_plantuml(src: str) -> dict:
    name = re.search(r"@startuml\s*(\S+)?", src)
    lines = [l.strip() for l in src.splitlines()
             if l.strip() and not l.strip().startswith(("@startuml", "@enduml", "'"))]
    out: dict = {"name": name.group(1) if name and name.group(1) else None}
    classes, actors, usecases, participants, states = [], [], [], [], []
    containers, nodes, relations, messages, steps, notes = [], [], [], [], [], []
    current = None
    for l in lines:
        if current is not None:
            if l == "}":
                current = None
                continue
            m = re.match(r"([+\-#~])\s*(\w+)\s*(\(.*?\))?\s*:\s*(.+)", l)
            if m:
                vis = {"+": "public", "-": "private", "#": "protected", "~": "package"}[m.group(1)]
                item = {"name": m.group(2), "type": m.group(4).strip(), "visibility": vis}
                if m.group(3) is not None:
                    item["params"] = m.group(3)[1:-1]
                    current["methods"].append(item)
                else:
                    current["attributes"].append(item)
            elif l.startswith("class "):
                current["members"].append(l.split()[1])
            continue
        if m := re.match(r"class\s+(\w+)\s*(\{)?", l):
            c = {"name": m.group(1), "attributes": [], "methods": [], "members": []}
            classes.append(c)
            if m.group(2):
                current = c
            continue
        if m := re.match(r"package\s+(\w+)\s*\{", l):
            current = {"name": m.group(1), "attributes": [], "methods": [], "members": []}
            containers.append(current)
            continue
        if m := re.match(r'actor\s+(\w+)(?:\s+as\s+"([^"]+)")?', l):
            actors.append({"id": m.group(1), "label": m.group(2) or m.group(1)})
            continue
        if m := re.match(r'usecase\s+"([^"]+)"\s+as\s+\((\w+)\)', l):
            usecases.append({"id": m.group(2), "label": m.group(1)})
            continue
        if m := re.match(r"(participant|node|artifact)\s+(\w+)", l):
            {"participant": participants, "node": nodes, "artifact": nodes}[m.group(1)].append(m.group(2))
            continue
        if m := re.match(r"state\s+(\w+)", l):
            states.append(m.group(1))
            continue
        if m := re.match(r"note\s+\w+\s+of\s+(\w+)\s*:\s*(.+)", l):
            notes.append({"on": m.group(1), "text": m.group(2)})
            continue
        if l.startswith(":") and l.endswith(";"):
            steps.append({"action": l[1:-1]})
            continue
        if m := re.match(r"if\s*\((.+?)\)\s*then\s*\((\w+)\)", l):
            steps.append({"decision": m.group(1), "branch": m.group(2)})
            continue
        if l in ("start", "stop", "else (no)", "endif") or l.startswith("else"):
            steps.append({"control": l})
            continue
        if m := REL_RE.match(l):
            a, arrow, b, label = m.groups()
            rec = {"from": a.strip("()"), "to": b.strip("()"), "arrow": arrow}
            if label:
                rec["label"] = label.strip()
            (messages if arrow in ("->>", "-->>", "->") else relations).append(rec)
            continue
    for key, val in [("actors", actors), ("use_cases", usecases), ("classes", classes),
                     ("packages", containers), ("participants", participants),
                     ("states", states), ("nodes", nodes), ("relations", relations),
                     ("messages", messages), ("steps", steps), ("notes", notes)]:
        if val:
            if key in ("classes", "packages"):
                val = [{k: v for k, v in c.items() if v} for c in val]
            out[key] = val
    if "transitions" not in out and states:
        out["transitions"] = [r for r in relations]
        out.pop("relations", None)
    return out


def parse_architecture_views(md: str) -> list[dict]:
    diagrams = []
    for view, body in _split_headings(md, 2):
        # each numbered item: "N. Kind — View: Title" followed by one or more fences
        for m in re.finditer(r"^\s*(\d+)\.\s*(\w+)\s*[—-]+\s*(.+?)$((?:(?!^\s*\d+\.\s).)*)", body, re.M | re.S):
            num, kind, title, rest = m.groups()
            for fence in FENCE_RE.finditer(rest):
                parsed = _parse_plantuml(fence.group(2))
                diagrams.append({"view": view, "index": int(num), "kind": kind,
                                 "title": title.strip(), **parsed,
                                 "plantuml": fence.group(2).strip()})
    return diagrams


# --------------------------------------------------------------------------- #
# Combined spec
# --------------------------------------------------------------------------- #
def build_spec(doc_md: str, view_md: str) -> dict:
    doc = parse_architecture_doc(doc_md)
    views = parse_architecture_views(view_md)

    def collect(key):
        out = []
        for d in views:
            for item in d.get(key, []):
                if item not in out:
                    out.append(item)
        return out

    classes = {}
    for d in views:
        for c in d.get("classes", []):
            if c.get("attributes") or c.get("methods"):
                classes[c["name"]] = c
    flows = [{"name": d["name"], "messages": d["messages"]} for d in views if d.get("messages")]
    return {
        "spec_version": 1,
        "system": {
            "name": doc["system_name"],
            "summary": doc["summary"],
            "architectural_style": doc["architectural_style"],
        },
        "domain_model": {
            "actors": collect("actors"),
            "use_cases": collect("use_cases"),
            "classes": list(classes.values()),
            "states": collect("states"),
            "state_transitions": [t for d in views for t in d.get("transitions", [])],
            "activity": [s for d in views if d["kind"].lower() == "activity" for s in d.get("steps", [])],
            "interaction_flows": flows,
            "packages": collect("packages"),
        },
        "requirements": doc["requirements"],
        "views": views,
        "documentation": doc,
    }


def compact_spec(spec: dict) -> dict:
    """Token-lean view of the spec for the LLM: domain model + requirements, no raw markdown.

    Raw PlantUML and the full documentation stay in the full spec (for traceability);
    the agent only needs the facts, which cuts prompt tokens and therefore cost.
    """
    doc = spec["documentation"]
    keep = ("Architecture Overview", "Testing Strategy", "Open Questions")
    notes = {}
    for sec in doc["sections"]:
        if any(k in sec["title"] for k in keep):
            notes[sec["title"]] = sec.get("bullets") or sec.get("text") or [
                b for sub in sec.get("subsections", []) for b in sub.get("bullets", [])]
    views = [{k: v for k, v in d.items() if k not in ("plantuml",)} for d in spec["views"]
             if d["kind"].lower() in ("package", "component", "deployment", "container", "object")]
    return {"system": spec["system"], "domain_model": spec["domain_model"],
            "requirements": spec["requirements"], "structure_views": views,
            "design_notes": notes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert architecture markdown to structured JSON")
    ap.add_argument("doc", type=Path, help="Architecture_Documentation.md")
    ap.add_argument("view", type=Path, help="Architecture_View.md")
    ap.add_argument("-o", "--out", type=Path, default=Path("spec.json"))
    a = ap.parse_args(argv)
    spec = build_spec(a.doc.read_text(encoding="utf-8"), a.view.read_text(encoding="utf-8"))
    a.out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    compact = a.out.with_name(a.out.stem + ".compact.json")
    compact.write_text(json.dumps(compact_spec(spec), indent=1, ensure_ascii=False), encoding="utf-8")
    dm = spec["domain_model"]
    print(f"Wrote {a.out}: {len(spec['views'])} diagrams, {len(dm['classes'])} classes, "
          f"{len(dm['use_cases'])} use cases, {len(spec['requirements'])} requirements "
          f"(+ compact version {compact.name}, {compact.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
