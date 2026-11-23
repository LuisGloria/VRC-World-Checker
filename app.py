from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import analyzer
from analyzer import CAUTION, DANGER, SAFE
from charts import build_figure
from vrc_api import TwoFactorRequired, VRChatClient, VRCError, config_dir, extract_world_id

POLL_INTERVAL_MS = 100

COLOR_NEUTRAL_TEXT = "#52514e"
COLOR_MUTED_TEXT = "#898781"
COLOR_GOOD_TEXT = "#0c7a0c"
COLOR_ERROR_TEXT = "#d03b3b"
VERDICT_COLORS = {SAFE: COLOR_GOOD_TEXT, CAUTION: "#b06a00", DANGER: COLOR_ERROR_TEXT}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("VRChat World Checker")
        root.geometry("1180x760")

        self.client = VRChatClient()
        self.blocklist_path = config_dir() / "blocklist.json"
        self.finished_tasks: queue.Queue = queue.Queue()
        self.current_report = None
        self.chart_canvas = None

        self._build_login_row()
        self._build_world_row()
        self._build_result_panels()

        self.root.after(POLL_INTERVAL_MS, self._process_finished_tasks)
        self.run_in_background(self.client.resume, self._on_resume_finished)  # reuse a saved session

    def _build_login_row(self) -> None:
        login_row = ttk.Frame(self.root, padding=8)
        login_row.pack(fill="x")

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()

        ttk.Label(login_row, text="VRChat user").grid(row=0, column=0, sticky="w")
        ttk.Entry(login_row, textvariable=self.username_var, width=22).grid(row=0, column=1, padx=4)
        ttk.Label(login_row, text="Password").grid(row=0, column=2, sticky="w")
        ttk.Entry(login_row, textvariable=self.password_var, show="*", width=22).grid(row=0, column=3, padx=4)

        self.login_button = ttk.Button(login_row, text="Log in", command=self.on_login_clicked)
        self.login_button.grid(row=0, column=4, padx=4)

        self.login_status_label = ttk.Label(login_row, text="Not logged in", foreground=COLOR_MUTED_TEXT)
        self.login_status_label.grid(row=0, column=5, padx=8, sticky="w")

    def _build_world_row(self) -> None:
        world_row = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        world_row.pack(fill="x")

        ttk.Label(world_row, text="World ID or link").pack(side="left")

        self.world_input_var = tk.StringVar()
        world_entry = ttk.Entry(world_row, textvariable=self.world_input_var, width=60)
        world_entry.pack(side="left", padx=6)
        world_entry.bind("<Return>", lambda _event: self.on_analyze_clicked())

        self.analyze_button = ttk.Button(world_row, text="Analyze", command=self.on_analyze_clicked)
        self.analyze_button.pack(side="left")

        self.blocklist_button = ttk.Button(
            world_row, text="Add to blocklist", command=self.on_blocklist_clicked, state="disabled")
        self.blocklist_button.pack(side="left", padx=6)

        self.export_button = ttk.Button(
            world_row, text="Export JSON", command=self.on_export_clicked, state="disabled")
        self.export_button.pack(side="left")

    def _build_result_panels(self) -> None:
        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left_panel = ttk.Frame(panes)
        self.chart_panel = ttk.Frame(panes)
        panes.add(left_panel, weight=1)
        panes.add(self.chart_panel, weight=1)

        self.verdict_label = tk.Label(
            left_panel, text="Enter a world ID to begin",
            font=("TkDefaultFont", 16, "bold"), fg=COLOR_NEUTRAL_TEXT, anchor="w")
        self.verdict_label.pack(fill="x", pady=(0, 6))

        self.report_text = scrolledtext.ScrolledText(left_panel, wrap="word", font=("TkFixedFont", 10))
        self.report_text.pack(fill="both", expand=True)
        self.report_text.configure(state="disabled")

    def _show_report_text(self, content: str) -> None:
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", content)
        self.report_text.configure(state="disabled")

    # bg tasks and stuffs
    def run_in_background(self, task, on_finished) -> None:
        #Run task() on a worker thread and then call on_finished(result, error) on the main thread.
        def worker() -> None:
            try:
                outcome = (task(), None)
            except Exception as error:  # shown to the user instead of crashing
                outcome = (None, error)
            self.finished_tasks.put((on_finished, outcome))

        threading.Thread(target=worker, daemon=True).start()

    def _process_finished_tasks(self) -> None:
        try:
            while True:
                on_finished, (result, error) = self.finished_tasks.get_nowait()
                on_finished(result, error)
        except queue.Empty:
            pass
        self.root.after(POLL_INTERVAL_MS, self._process_finished_tasks)

    # login
    def _on_resume_finished(self, user, error) -> None:
        if user:
            self._show_logged_in(user)

    def _show_logged_in(self, user: dict) -> None:
        display_name = user.get("displayName", "?")
        self.login_status_label.configure(text=f"Logged in as {display_name}", foreground=COLOR_GOOD_TEXT)
        self.password_var.set("")  # don't keep the password around once we're in

    def on_login_clicked(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showinfo("Login", "Enter your VRChat username and password.")
            return

        self.login_button.configure(state="disabled")
        self.login_status_label.configure(text="Logging in...", foreground=COLOR_MUTED_TEXT)
        self.run_in_background(lambda: self.client.login(username, password), self._on_login_finished)

    def _on_login_finished(self, user, error) -> None:
        if isinstance(error, TwoFactorRequired):
            code = simpledialog.askstring(
                "Two-factor authentication",
                "Enter your 2FA code (authenticator, email or recovery code):",
                parent=self.root)
            if code:
                required_methods = error.methods
                self.run_in_background(
                    lambda: self.client.verify_2fa(required_methods, code), self._on_login_finished)
                return
            error = VRCError("2FA cancelled.")

        self.login_button.configure(state="normal")
        if error:
            self.login_status_label.configure(text="Not logged in", foreground=COLOR_ERROR_TEXT)
            messagebox.showerror("Login failed", str(error))
        else:
            self._show_logged_in(user)

    # Analyze me this pls weird algo
    def on_analyze_clicked(self) -> None:
        world_id = extract_world_id(self.world_input_var.get())
        if not world_id:
            messagebox.showinfo("World ID", "That ain't a world ID (wrld_xxxxxxxx-xxxx-...).")
            return

        self.analyze_button.configure(state="disabled")
        self.verdict_label.configure(text="Analyzing...", fg=COLOR_NEUTRAL_TEXT)

        blocklist = analyzer.load_blocklist(self.blocklist_path)
        self.run_in_background(
            lambda: analyzer.fetch_and_analyze(self.client, world_id, blocklist),
            self._on_analysis_finished)

    def _on_analysis_finished(self, report, error) -> None:
        self.analyze_button.configure(state="normal")

        if error:
            self.verdict_label.configure(text="Could not analyze", fg=COLOR_ERROR_TEXT)
            self._show_report_text(str(error))
            return

        self.current_report = report
        self.verdict_label.configure(
            text=f"{report.verdict}  ({report.score}/100)", fg=VERDICT_COLORS[report.verdict])
        self._show_report_text(analyzer.render_text(report))
        self.blocklist_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self._show_charts(report)

    def _show_charts(self, report) -> None:
        if self.chart_canvas:
            self.chart_canvas.get_tk_widget().destroy()

        self.chart_canvas = FigureCanvasTkAgg(build_figure(report), master=self.chart_panel)
        self.chart_canvas.draw()
        self.chart_canvas.get_tk_widget().pack(fill="both", expand=True)

    # extras
    def on_blocklist_clicked(self) -> None:
        report = self.current_report
        if not report:
            return

        question = f"Add this world and its author ({report.author_name}) to your local blocklist?"
        if messagebox.askyesno("Blocklist", question):
            analyzer.add_to_blocklist(self.blocklist_path, report.world_id, report.author_id)
            self.on_analyze_clicked()  # re-run so the new analyzation shows immediately

    def on_export_clicked(self) -> None:
        report = self.current_report
        if not report:
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"{report.world_id}.json",
            filetypes=[("JSON", "*.json")])
        if save_path:
            with open(save_path, "w", encoding="utf-8") as output_file:
                json.dump(report.to_dict(), output_file, indent=2)


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
