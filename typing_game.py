import tkinter as tk
from tkinter import messagebox
import random
import time

WORDS = [
    "apple", "banana", "cherry", "orange", "grape", "melon", "peach", "lemon",
    "python", "java", "code", "script", "html", "css", "react", "node",
    "keyboard", "monitor", "mouse", "screen", "window", "button", "cursor",
    "hello", "world", "game", "play", "start", "stop", "quit", "save",
    "quick", "brown", "fox", "jumps", "over", "lazy", "dog", "typing",
    "practice", "speed", "accuracy", "score", "level", "time", "clock",
    "tiger", "lion", "eagle", "shark", "whale", "panda", "koala", "zebra"
]

class TypingGame:
    def __init__(self, root):
        self.root = root
        self.root.title("타자 연습 게임")
        self.root.geometry("500x400")
        self.root.resizable(False, False)

        self.current_word = ""
        self.score = 0
        self.time_left = 30
        self.timer_id = None
        self.running = False

        # UI
        self.title_label = tk.Label(root, text="타자 연습 게임", font=("맑은 고딕", 20, "bold"))
        self.title_label.pack(pady=10)

        self.timer_label = tk.Label(root, text="남은 시간: 30초", font=("맑은 고딕", 14))
        self.timer_label.pack()

        self.word_label = tk.Label(root, text="", font=("Consolas", 28, "bold"), fg="blue")
        self.word_label.pack(pady=40)

        self.entry = tk.Entry(root, font=("Consolas", 16), justify="center", state="disabled")
        self.entry.pack(pady=10)
        self.entry.bind("<Return>", self.check_input)
        self.entry.bind("<KeyRelease>", self.on_key_release)

        self.score_label = tk.Label(root, text="점수: 0", font=("맑은 고딕", 14))
        self.score_label.pack()

        self.status_label = tk.Label(root, text="", font=("맑은 고딕", 12), fg="green")
        self.status_label.pack(pady=5)

        self.btn_frame = tk.Frame(root)
        self.btn_frame.pack(pady=20)

        self.start_btn = tk.Button(self.btn_frame, text="시작", font=("맑은 고딕", 12), width=10,
                                   command=self.start_game)
        self.start_btn.pack(side="left", padx=10)

        self.quit_btn = tk.Button(self.btn_frame, text="종료", font=("맑은 고딕", 12), width=10,
                                  command=root.quit)
        self.quit_btn.pack(side="left", padx=10)

    def start_game(self):
        self.score = 0
        self.time_left = 30
        self.running = True
        self.entry.config(state="normal")
        self.entry.delete(0, tk.END)
        self.entry.focus_set()
        self.start_btn.config(state="disabled")
        self.update_timer()
        self.new_word()
        self.update_score()
        self.status_label.config(text="")

    def new_word(self):
        self.current_word = random.choice(WORDS)
        self.word_label.config(text=self.current_word)

    def update_timer(self):
        if self.running and self.time_left > 0:
            self.time_left -= 1
            self.timer_label.config(text=f"남은 시간: {self.time_left}초")
            self.timer_id = self.root.after(1000, self.update_timer)
        elif self.time_left <= 0:
            self.end_game()

    def on_key_release(self, event):
        if not self.running:
            return
        typed = self.entry.get()
        if self.current_word.startswith(typed):
            self.entry.config(fg="black")
            self.status_label.config(text="", fg="green")
        else:
            self.entry.config(fg="red")
            self.status_label.config(text="❌ 틀린 글자입니다", fg="red")

    def check_input(self, event=None):
        if not self.running:
            return
        typed = self.entry.get().strip()
        if typed == self.current_word:
            self.score += 10
            self.entry.delete(0, tk.END)
            self.entry.config(fg="black")
            self.status_label.config(text="✅ 정답!", fg="green")
            self.new_word()
            self.update_score()
        else:
            self.entry.config(fg="red")
            self.status_label.config(text="❌ 틀렸습니다!", fg="red")

    def update_score(self):
        self.score_label.config(text=f"점수: {self.score}")

    def end_game(self):
        self.running = False
        self.entry.config(state="disabled")
        self.start_btn.config(state="normal")
        wpm = self.score // 10
        messagebox.showinfo("게임 종료",
                            f"시간 종료!\n\n점수: {self.score}점\n맞춘 단어 수: {wpm}개")
        self.word_label.config(text="")
        self.timer_label.config(text="남은 시간: 30초")
        self.status_label.config(text="")

if __name__ == "__main__":
    root = tk.Tk()
    game = TypingGame(root)
    root.mainloop()