from archforge import gui


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "s.json")
    assert gui.load_settings() == {}
    gui.save_settings({"provider": "gemini", "keys": {"gemini": "k"}})
    assert gui.load_settings()["keys"]["gemini"] == "k"


def test_bundled_examples_found():
    doc, view = gui.bundled_examples()
    assert doc.endswith("Architecture_Documentation.md") and view.endswith("Architecture_View.md")


def test_model_choices_match_providers():
    from archforge import config
    assert set(gui.MODEL_CHOICES) == set(config.PROVIDERS)
    assert "deepseek-v4-pro" in gui.MODEL_CHOICES["deepseek"]
