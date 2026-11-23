import json
import os
from app.models import Question

DATA_FILE = os.path.join(os.path.dirname(__file__), 'questions.json')

def load_questions():
    if not os.path.exists(DATA_FILE):
        return [
            Question("1", "What is 1/2 + 1/4?", ["3/4", "2/6", "1/6", "2/4"], 0),
            Question("2", "What is 1/3 + 1/3?", ["1/6", "2/3", "2/6", "1/3"], 1),
            Question("3", "What is 3/4 - 1/4?", ["1/4", "2/4", "4/4", "1/2"], 3),
            Question("4", "What is 1/5 + 2/5?", ["3/10", "3/5", "2/5", "1/5"], 1),
            Question("5", "What is 2/3 - 1/3?", ["1/3", "3/3", "1/6", "2/6"], 0),
            Question("6", "What is 1/8 + 3/8?", ["4/16", "4/8", "2/8", "1/8"], 1),
            Question("7", "What is 5/6 - 2/6?", ["3/12", "3/6", "7/6", "1/6"], 1),
            Question("8", "What is 1/4 + 1/4?", ["2/8", "1/2", "2/4", "1/4"], 1),
            Question("9", "What is 7/10 - 3/10?", ["4/10", "10/10", "4/20", "1/10"], 0),
            Question("10", "What is 2/9 + 4/9?", ["6/18", "6/9", "2/9", "8/9"], 1),
        ]
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
        return [Question(q['id'], q['prompt'], q['options'], q['correct_index']) for q in data]

def save_questions(questions):
    with open(DATA_FILE, 'w') as f:
        json.dump([q.to_dict() for q in questions], f, indent=4)
