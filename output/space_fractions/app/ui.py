import tkinter as tk
from tkinter import messagebox
from app.models import Game
from app.persistence import load_questions

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Space Fractions")
        self.geometry("900x600")
        self.game = Game("1")
        self.questions = load_questions()
        self.current_question_idx = 0
        
        self.container = tk.Frame(self)
        self.container.pack(fill="both", expand=True)
        
        self.show_main_menu()

    def show_main_menu(self):
        self.clear_container()
        tk.Label(self.container, text="Space Fractions", font=("Arial", 24)).pack(pady=20)
        tk.Button(self.container, text="Play Game", command=self.start_game, width=20).pack(pady=10)
        tk.Button(self.container, text="View Score", command=self.show_score, width=20).pack(pady=10)
        tk.Button(self.container, text="Update Questions", command=self.show_admin, width=20).pack(pady=10)
        tk.Button(self.container, text="View Help", command=self.show_help, width=20).pack(pady=10)
        tk.Button(self.container, text="Quit", command=self.quit, width=20).pack(pady=10)

    def show_admin(self):
        self.clear_container()
        tk.Label(self.container, text="Admin: Update Questions", font=("Arial", 18)).pack(pady=20)
        tk.Label(self.container, text="Feature not implemented in this version.", font=("Arial", 12)).pack(pady=20)
        tk.Button(self.container, text="Back", command=self.show_main_menu).pack(pady=10)

    def show_help(self):
        self.clear_container()
        tk.Label(self.container, text="Help", font=("Arial", 18)).pack(pady=20)
        tk.Label(self.container, text="Solve fraction problems to score points!", font=("Arial", 12)).pack(pady=20)
        tk.Button(self.container, text="Back", command=self.show_main_menu).pack(pady=10)

    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def start_game(self):
        self.game.play()
        self.current_question_idx = 0
        self.game.score = 0
        self.show_question()

    def show_question(self):
        if self.current_question_idx >= len(self.questions):
            self.game.game_over()
            messagebox.showinfo("Game Over", f"Final Score: {self.game.score}")
            self.show_main_menu()
            return

        self.clear_container()
        q = self.questions[self.current_question_idx]
        tk.Label(self.container, text=q.get_prompt(), font=("Arial", 18)).pack(pady=20)
        
        for i, option in enumerate(q.get_options()):
            tk.Button(self.container, text=option, command=lambda idx=i: self.submit_answer(idx)).pack(pady=5)
        
        tk.Button(self.container, text="Pause", command=self.pause_game).pack(pady=20)

    def submit_answer(self, idx):
        if self.questions[self.current_question_idx].check_answer(idx):
            self.game.add_score(10)
        self.current_question_idx += 1
        self.show_question()

    def pause_game(self):
        self.game.pause()
        messagebox.showinfo("Paused", "Game Paused")
        self.game.resume()

    def show_score(self):
        messagebox.showinfo("Score", f"Current Score: {self.game.score}")
