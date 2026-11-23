import json
import os

class Question:
    def __init__(self, id, prompt, options, correct_index):
        self.id = id
        self.prompt = prompt
        self.options = options
        self.correct_index = correct_index

    def get_prompt(self):
        return self.prompt

    def get_options(self):
        return self.options

    def check_answer(self, index):
        return index == self.correct_index

    def to_dict(self):
        return {
            "id": self.id,
            "prompt": self.prompt,
            "options": self.options,
            "correct_index": self.correct_index
        }

class Game:
    def __init__(self, id):
        self.id = id
        self.score = 0
        self.state = "Playing"

    def play(self):
        self.state = "Playing"

    def view_score(self):
        return self.score

    def add_score(self, points):
        self.score += points

    def pause(self):
        self.state = "Paused"

    def resume(self):
        self.state = "Playing"

    def game_over(self):
        self.state = "GameOver"

class User:
    def __init__(self, id, username):
        self.id = id
        self.username = username

    def play_game(self, game):
        game.play()

    def view_score(self, game):
        return game.view_score()

class Admin:
    def __init__(self, id, username):
        self.id = id
        self.username = username

    def update_questions(self, questions, new_questions):
        questions.clear()
        questions.extend(new_questions)
