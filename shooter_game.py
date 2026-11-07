import tkinter as tk
import random


class ShooterGame:
    def __init__(self, root):
        self.root = root
        self.root.title("간단 슈팅 게임")
        self.root.resizable(False, False)

        self.canvas_width = 600
        self.canvas_height = 700
        self.canvas = tk.Canvas(root, width=self.canvas_width, height=self.canvas_height, bg="black")
        self.canvas.pack()

        self.player_size = 40
        self.player_x = self.canvas_width // 2 - self.player_size // 2
        self.player_y = self.canvas_height - 80
        self.player_speed = 15
        self.bullets = []
        self.bullet_speed = 10
        self.enemies = []
        self.enemy_speed = 5
        self.score = 0
        self.game_over = False

        self.player = self.canvas.create_rectangle(
            self.player_x, self.player_y,
            self.player_x + self.player_size, self.player_y + self.player_size,
            fill="cyan"
        )
        self.score_text = self.canvas.create_text(
            10, 10, anchor="nw", text=f"점수: {self.score}",
            fill="white", font=("Arial", 16)
        )

        self.game_over_text = None
        self.restart_button = tk.Button(
            root, text="재시작", command=self.restart_game,
            font=("Arial", 14), bg="white", fg="black", width=10
        )
        self.restart_button.place(
            x=self.canvas_width // 2 - 50, y=self.canvas_height // 2 + 60
        )
        self.restart_button.place_forget()  # 처음에는 숨김

        self.root.bind("<KeyPress>", self.on_key_press)
        self.root.bind("<KeyRelease>", self.on_key_release)

        self.keys = set()
        self.spawn_enemy()
        self.game_loop()

    def on_key_press(self, event):
        self.keys.add(event.keysym)
        if event.keysym == "space" and not self.game_over:
            self.shoot()

    def on_key_release(self, event):
        self.keys.discard(event.keysym)

    def shoot(self):
        bx = self.player_x + self.player_size // 2 - 2
        by = self.player_y
        bullet = self.canvas.create_rectangle(
            bx, by, bx + 4, by + 10, fill="yellow"
        )
        self.bullets.append(bullet)

    def spawn_enemy(self):
        if self.game_over:
            return
        ex = random.randint(0, self.canvas_width - 30)
        enemy = self.canvas.create_rectangle(
            ex, 0, ex + 30, 30, fill="red"
        )
        self.enemies.append(enemy)
        self.root.after(random.randint(400, 900), self.spawn_enemy)

    def game_loop(self):
        if self.game_over:
            return

        # 플레이어 이동
        dx = 0
        if "Left" in self.keys:
            dx -= self.player_speed
        if "Right" in self.keys:
            dx += self.player_speed

        self.player_x = max(0, min(self.canvas_width - self.player_size, self.player_x + dx))
        self.canvas.coords(
            self.player, self.player_x, self.player_y,
            self.player_x + self.player_size, self.player_y + self.player_size
        )

        # 총알 이동 및 충돌
        for bullet in self.bullets[:]:
            self.canvas.move(bullet, 0, -self.bullet_speed)
            bx1, by1, bx2, by2 = self.canvas.coords(bullet)

            hit = False
            for enemy in self.enemies[:]:
                ex1, ey1, ex2, ey2 = self.canvas.coords(enemy)
                if bx2 > ex1 and bx1 < ex2 and by2 > ey1 and by1 < ey2:
                    self.canvas.delete(enemy)
                    self.canvas.delete(bullet)
                    self.enemies.remove(enemy)
                    self.bullets.remove(bullet)
                    self.score += 10
                    self.canvas.itemconfig(self.score_text, text=f"점수: {self.score}")
                    hit = True
                    break

            if hit:
                continue

            if by2 < 0:
                self.canvas.delete(bullet)
                self.bullets.remove(bullet)

        # 적 이동
        for enemy in self.enemies[:]:
            self.canvas.move(enemy, 0, self.enemy_speed)
            ex1, ey1, ex2, ey2 = self.canvas.coords(enemy)

            # 플레이어 충돌
            px1, py1, px2, py2 = self.canvas.coords(self.player)
            if ex2 > px1 and ex1 < px2 and ey2 > py1 and ey1 < py2:
                self.end_game()
                break

            # 화면 아래로 나가면
            if ey2 > self.canvas_height:
                self.canvas.delete(enemy)
                self.enemies.remove(enemy)

        self.root.after(30, self.game_loop)

    def end_game(self):
        self.game_over = True
        self.game_over_text = self.canvas.create_text(
            self.canvas_width // 2, self.canvas_height // 2 - 30,
            text=f"게임 오버!\n최종 점수: {self.score}",
            fill="white", font=("Arial", 24), justify="center"
        )
        self.restart_button.place(
            x=self.canvas_width // 2 - 50, y=self.canvas_height // 2 + 60
        )

    def restart_game(self):
        # 게임 오버 텍스트와 버튼 숨기기
        if self.game_over_text:
            self.canvas.delete(self.game_over_text)
            self.game_over_text = None
        self.restart_button.place_forget()

        # 게임 상태 초기화
        self.game_over = False
        self.score = 0
        self.canvas.itemconfig(self.score_text, text=f"점수: {self.score}")

        # 남은 적과 총알 제거
        for enemy in self.enemies[:]:
            self.canvas.delete(enemy)
        self.enemies.clear()

        for bullet in self.bullets[:]:
            self.canvas.delete(bullet)
        self.bullets.clear()

        # 플레이어 위치 초기화
        self.player_x = self.canvas_width // 2 - self.player_size // 2
        self.canvas.coords(
            self.player, self.player_x, self.player_y,
            self.player_x + self.player_size, self.player_y + self.player_size
        )

        self.keys.clear()
        self.spawn_enemy()
        self.game_loop()


if __name__ == "__main__":
    root = tk.Tk()
    game = ShooterGame(root)
    root.mainloop()