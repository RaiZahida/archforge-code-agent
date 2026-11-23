import os
import json
from app.persistence import load_questions, save_questions
from app.models import Question

def test_persistence(tmp_path):
    # Override the data file path for testing
    import app.persistence
    test_file = tmp_path / "questions.json"
    app.persistence.DATA_FILE = str(test_file)
    
    qs = [Question("1", "Test", ["A", "B"], 0)]
    save_questions(qs)
    
    loaded = load_questions()
    assert len(loaded) == 1
    assert loaded[0].prompt == "Test"
