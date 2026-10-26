import sqlite3
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from todo_store import todo_store


PRIORITY_LABELS = {"low": "낮음", "medium": "보통", "high": "높음"}
PRIORITY_VALUES = {label: value for value, label in PRIORITY_LABELS.items()}


class TodoDesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("체크리스트")
        self.root.geometry("560x700")
        self.root.minsize(400, 420)
        self.root.configure(background="#f7f6f2")

        self.tasks: list[dict[str, Any]] = []
        self.selected_task_id: int | None = None
        self.task_vars: dict[int, tk.BooleanVar] = {}
        self.title_var = tk.StringVar()
        self.summary_var = tk.StringVar()

        self._configure_style()
        self._build_ui()
        self.refresh()
        self.root.after(5000, self._refresh_periodically)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Paper.TFrame", background="#fffefb")
        style.configure("Title.TLabel", background="#fffefb", foreground="#292822", font=("Segoe UI", 22, "bold"))
        style.configure("Count.TLabel", background="#fffefb", foreground="#89877f", font=("Segoe UI", 10))
        style.configure("Task.TCheckbutton", background="#fffefb", foreground="#37362f", font=("Segoe UI", 11), padding=(0, 9))
        style.configure("Done.Task.TCheckbutton", background="#fffefb", foreground="#a5a39b", font=("Segoe UI", 11), padding=(0, 9))
        style.map("Task.TCheckbutton", background=[("active", "#fffefb")])
        style.map("Done.Task.TCheckbutton", background=[("active", "#fffefb")])
        style.configure("Add.TButton", background="#eceae2", foreground="#4b4941", font=("Segoe UI", 10, "bold"), padding=(12, 7))
        style.map("Add.TButton", background=[("active", "#e2dfd5")])

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=(32, 28), style="Paper.TFrame")
        main.pack(fill="both", expand=True)

        heading = ttk.Frame(main, style="Paper.TFrame")
        heading.pack(fill="x", pady=(0, 18))
        ttk.Label(heading, text="체크리스트", style="Title.TLabel").pack(side="left")
        ttk.Label(heading, textvariable=self.summary_var, style="Count.TLabel").pack(side="right", pady=(9, 0))

        ttk.Separator(main).pack(fill="x", pady=(0, 8))
        list_frame = tk.Frame(main, background="#fffefb", highlightthickness=0)
        list_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_frame, background="#fffefb", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.task_frame = tk.Frame(self.canvas, background="#fffefb")
        self.task_window = self.canvas.create_window((0, 0), window=self.task_frame, anchor="nw")
        self.task_frame.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_task_frame)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

        ttk.Separator(main).pack(fill="x", pady=(10, 14))
        form = ttk.Frame(main, style="Paper.TFrame")
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        self.title_entry = ttk.Entry(form, textvariable=self.title_var, font=("Segoe UI", 11))
        self.title_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), ipady=6)
        self.title_entry.bind("<Return>", lambda _event: self.add_task())
        ttk.Button(form, text="추가", style="Add.TButton", command=self.add_task).grid(row=0, column=1)

    def _show_error(self, error: Exception) -> None:
        messagebox.showerror("할 일 처리 오류", str(error), parent=self.root)

    def refresh(self) -> None:
        try:
            self.tasks = todo_store.list_tasks()
        except (OSError, sqlite3.Error, ValueError) as error:
            self._show_error(error)
            return

        completed_count = sum(task["completed"] for task in self.tasks)
        self.summary_var.set(f"{completed_count} / {len(self.tasks)} 완료")
        for child in self.task_frame.winfo_children():
            child.destroy()
        self.task_vars.clear()

        if not self.tasks:
            ttk.Label(
                self.task_frame,
                text="할 일을 적고 체크해 보세요.",
                style="Count.TLabel",
                padding=(0, 18),
            ).pack(anchor="w")
        for task in self.tasks:
            row = tk.Frame(self.task_frame, background="#fffefb")
            row.pack(fill="x")
            variable = tk.BooleanVar(value=task["completed"])
            self.task_vars[task["id"]] = variable
            checkbox = ttk.Checkbutton(
                row,
                text=task["title"],
                variable=variable,
                style="Done.Task.TCheckbutton" if task["completed"] else "Task.TCheckbutton",
                command=lambda task_id=task["id"], completed=variable: self.toggle_task(task_id, completed.get()),
            )
            checkbox.pack(side="left", fill="x", expand=True)
            checkbox.bind("<Double-Button-1>", lambda _event, task_id=task["id"]: self.edit_task(task_id))
            checkbox.bind("<Button-1>", lambda _event, task_id=task["id"]: self._select_task(task_id), add="+")
            delete_button = tk.Button(
                row,
                text="×",
                command=lambda task_id=task["id"]: self.delete_task(task_id),
                background="#fffefb",
                foreground="#aaa79e",
                activebackground="#f1efe9",
                activeforeground="#6f6c63",
                borderwidth=0,
                font=("Segoe UI", 15),
                cursor="hand2",
                padx=8,
            )
            delete_button.pack(side="right")
            checkbox.bind("<Button-3>", lambda _event, task_id=task["id"]: self._select_task(task_id))
            delete_button.bind("<Enter>", lambda event: event.widget.configure(foreground="#6f6c63"))
            delete_button.bind("<Leave>", lambda event: event.widget.configure(foreground="#aaa79e"))
        self.canvas.yview_moveto(0)

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_task_frame(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.task_window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _refresh_periodically(self) -> None:
        self.refresh()
        self.root.after(5000, self._refresh_periodically)

    def _select_task(self, task_id: int) -> None:
        self.selected_task_id = task_id

    def _selected_task(self, task_id: int | None = None) -> dict[str, Any] | None:
        selected_id = task_id if task_id is not None else self.selected_task_id
        if selected_id is None:
            messagebox.showinfo("할 일 선택", "먼저 목록에서 할 일을 선택해 주세요.", parent=self.root)
            return None
        return next((task for task in self.tasks if task["id"] == selected_id), None)

    def add_task(self) -> None:
        try:
            task = todo_store.add_task(self.title_var.get())
        except (OSError, sqlite3.Error, ValueError) as error:
            self._show_error(error)
            return
        self.title_var.set("")
        self.selected_task_id = task["id"]
        self.title_entry.focus_set()
        self.refresh()

    def toggle_task(self, task_id: int | None = None, completed: bool | None = None) -> None:
        task = self._selected_task(task_id)
        if task is None:
            return
        next_completed = not task["completed"] if completed is None else completed
        try:
            todo_store.update_task(task["id"], {"completed": next_completed})
        except (KeyError, OSError, sqlite3.Error, ValueError) as error:
            self._show_error(error)
            return
        self.refresh()

    def edit_task(self, task_id: int | None = None) -> None:
        task = self._selected_task(task_id)
        if task is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("할 일 수정")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, padding=20)
        content.pack(fill="both", expand=True)

        title_var = tk.StringVar(value=task["title"])
        due_date_var = tk.StringVar(value=task["due_date"] or "")
        priority_var = tk.StringVar(value=PRIORITY_LABELS[task["priority"]])
        ttk.Label(content, text="제목").grid(row=0, column=0, sticky="w", pady=5)
        title_entry = ttk.Entry(content, textvariable=title_var, width=48)
        title_entry.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(content, text="메모").grid(row=1, column=0, sticky="nw", pady=5)
        description = tk.Text(content, width=48, height=5, wrap="word")
        description.insert("1.0", task["description"])
        description.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(content, text="마감일").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(content, textvariable=due_date_var).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(content, text="우선순위").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Combobox(
            content,
            textvariable=priority_var,
            values=tuple(PRIORITY_VALUES),
            state="readonly",
        ).grid(row=3, column=1, sticky="w", pady=5)
        buttons = ttk.Frame(content)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))

        def save_changes() -> None:
            try:
                todo_store.update_task(
                    task["id"],
                    {
                        "title": title_var.get(),
                        "description": description.get("1.0", "end-1c"),
                        "due_date": due_date_var.get().strip() or None,
                        "priority": PRIORITY_VALUES.get(priority_var.get()),
                    },
                )
            except (KeyError, OSError, sqlite3.Error, ValueError) as error:
                self._show_error(error)
                return
            dialog.destroy()
            self.refresh()

        ttk.Button(buttons, text="취소", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="저장", command=save_changes).pack(side="right", padx=(0, 8))
        title_entry.focus_set()
        dialog.bind("<Return>", lambda _event: save_changes())

    def delete_task(self, task_id: int | None = None) -> None:
        task = self._selected_task(task_id)
        if task is None:
            return
        if not messagebox.askyesno(
            "할 일 삭제",
            f"'{task['title']}' 할 일을 삭제할까요?",
            parent=self.root,
        ):
            return
        try:
            todo_store.delete_task(task["id"])
        except (KeyError, OSError, sqlite3.Error, ValueError) as error:
            self._show_error(error)
            return
        self.refresh()

def main() -> None:
    root = tk.Tk()
    TodoDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
