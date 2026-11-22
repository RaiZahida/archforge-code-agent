import json
from pathlib import Path

from archforge.preprocess import build_spec, compact_spec, main, parse_architecture_views

INPUTS = Path(__file__).resolve().parent.parent / "examples" / "inputs"
DOC = (INPUTS / "Architecture_Documentation.md").read_text(encoding="utf-8")
VIEW = (INPUTS / "Architecture_View.md").read_text(encoding="utf-8")


def spec():
    return build_spec(DOC, VIEW)


def test_system_metadata():
    s = spec()["system"]
    assert s["name"] == "Space Fractions"
    assert s["architectural_style"] == "Microservices"
    assert "Chosen architectural style" not in s["summary"]


def test_all_diagrams_parsed():
    views = parse_architecture_views(VIEW)
    kinds = {v["kind"] for v in views}
    assert len(views) == 13  # 11 numbered items, two of them have 2 diagrams
    assert {"UseCase", "Class", "State", "Activity", "Sequence", "Deployment"} <= kinds


def test_class_model():
    classes = {c["name"]: c for c in spec()["domain_model"]["classes"]}
    assert set(classes) == {"Game", "Question", "User", "Admin"}
    q = classes["Question"]
    assert [a["name"] for a in q["attributes"]] == ["id", "prompt", "options"]
    assert classes["Admin"]["methods"][0]["params"] == "questions: List<Question>"


def test_use_cases_and_actors():
    dm = spec()["domain_model"]
    assert {u["label"] for u in dm["use_cases"]} == {"Play Game", "View Score", "Update Questions", "View Help"}
    assert {a["label"] for a in dm["actors"]} == {"End User", "Admin"}


def test_state_machine():
    dm = spec()["domain_model"]
    assert dm["states"] == ["Playing", "Paused", "GameOver"]
    labels = {(t["from"], t["to"]): t.get("label") for t in dm["state_transitions"]}
    assert labels[("Playing", "Paused")] == "pause()"
    assert labels[("Paused", "Playing")] == "resume()"


def test_sequence_flow():
    flows = {f["name"]: f["messages"] for f in spec()["domain_model"]["interaction_flows"]}
    assert flows["SequenceDiagram1"][0] == {"from": "User", "to": "Game", "arrow": "->>", "label": "play()"}
    assert len(flows["SequenceDiagram1"]) == 8


def test_requirements_merged():
    reqs = {r["id"]: r for r in spec()["requirements"]}
    assert set(reqs) == {"FR-1", "NFR-1", "ASR-1", "ASR-2"}
    assert reqs["NFR-1"]["test_type"].startswith("Load testing")
    assert reqs["NFR-1"]["components"] == "GameComponent"


def test_cli_writes_json(tmp_path):
    out = tmp_path / "spec.json"
    assert main([str(INPUTS / "Architecture_Documentation.md"), str(INPUTS / "Architecture_View.md"), "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["spec_version"] == 1


def test_compact_spec_is_smaller_and_keeps_facts():
    full = spec()
    small = compact_spec(full)
    assert len(json.dumps(small)) < len(json.dumps(full)) / 2
    assert "plantuml" not in json.dumps(small)
    assert {c["name"] for c in small["domain_model"]["classes"]} == {"Game", "Question", "User", "Admin"}
