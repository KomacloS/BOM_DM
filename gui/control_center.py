# root: gui/control_center.py
import os
import sys
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, filedialog
from tkinter.scrolledtext import ScrolledText

import requests

BASE_URL = "http://localhost:8000"
LOG_DIR = Path("logs")
LOG_UPDATE_MS = 1000

PROC: subprocess.Popen | None = None
TOKEN: str | None = None
ROOT: tk.Tk | None = None
STATUS_VAR: tk.StringVar | None = None
LOG_WIDGET: ScrolledText | None = None


def update_status() -> None:
    if STATUS_VAR is None:
        return
    running = PROC is not None and PROC.poll() is None
    STATUS_VAR.set("RUNNING" if running else "STOPPED")


def start_server() -> None:
    global PROC
    if PROC and PROC.poll() is None:
        messagebox.showinfo("Server", "Server already running")
        return
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"server_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
    f = open(log_file, "a")
    PROC = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app"],
        stdout=f,
        stderr=subprocess.STDOUT,
    )
    update_status()


def stop_server() -> None:
    global PROC
    if PROC and PROC.poll() is None:
        PROC.terminate()
        try:
            PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            PROC.kill()
    PROC = None
    update_status()


def restart_server() -> None:
    stop_server()
    start_server()


def run_tests() -> None:
    def worker() -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True
        )
        if result.returncode == 0:
            messagebox.showinfo("Tests", result.stdout.strip())
        else:
            messagebox.showerror("Tests failed", result.stdout + "\n" + result.stderr)

    threading.Thread(target=worker, daemon=True).start()


def trigger_backup() -> None:
    from app.main import nightly_backup

    def worker() -> None:
        try:
            nightly_backup()
            messagebox.showinfo("Backup", "Backup complete")
        except Exception as exc:  # pragma: no cover - backup errors
            messagebox.showerror("Backup failed", str(exc))

    threading.Thread(target=worker, daemon=True).start()


def login_dialog() -> str | None:
    assert ROOT is not None
    dlg = tk.Toplevel(ROOT)
    dlg.title("Login")
    tk.Label(dlg, text="Username").grid(row=0, column=0, sticky="e")
    user_e = tk.Entry(dlg)
    user_e.grid(row=0, column=1)
    tk.Label(dlg, text="Password").grid(row=1, column=0, sticky="e")
    pass_e = tk.Entry(dlg, show="*")
    pass_e.grid(row=1, column=1)
    token_box: dict[str, str] = {}

    def submit() -> None:
        resp = requests.post(
            f"{BASE_URL}/auth/token",
            data={"username": user_e.get(), "password": pass_e.get()},
        )
        if resp.status_code == 200:
            token_box["token"] = resp.json()["access_token"]
            dlg.destroy()
        else:
            messagebox.showerror("Login failed", resp.text)

    tk.Button(dlg, text="Login", command=submit).grid(row=2, column=0, columnspan=2)
    dlg.grab_set()
    ROOT.wait_window(dlg)
    return token_box.get("token")


def ensure_token() -> str | None:
    global TOKEN
    if TOKEN:
        return TOKEN
    TOKEN = login_dialog()
    return TOKEN


def download_export(endpoint: str, default: str) -> None:
    token = ensure_token()
    if not token:
        return
    path = filedialog.asksaveasfilename(defaultextension=default, initialfile=default)
    if not path:
        return
    resp = requests.get(
        f"{BASE_URL}{endpoint}", headers={"Authorization": f"Bearer {token}"}
    )
    if resp.status_code == 200:
        with open(path, "wb") as f:
            f.write(resp.content)
        messagebox.showinfo("Saved", f"File saved to {path}")
    else:
        messagebox.showerror("Error", f"{resp.status_code}: {resp.text}")


def update_log() -> None:
    if LOG_WIDGET is None:
        return
    latest = None
    if LOG_DIR.exists():
        logs = list(LOG_DIR.glob("*.log"))
        if logs:
            latest = max(logs, key=lambda p: p.stat().st_mtime)
    if latest and latest.exists():
        with latest.open() as f:
            lines = f.readlines()[-200:]
        LOG_WIDGET.configure(state="normal")
        LOG_WIDGET.delete("1.0", tk.END)
        LOG_WIDGET.insert(tk.END, "".join(lines))
        LOG_WIDGET.configure(state="disabled")
    if ROOT is not None:
        ROOT.after(LOG_UPDATE_MS, update_log)


def on_close() -> None:
    if PROC and PROC.poll() is None:
        if not messagebox.askyesno("Quit", "Server is running. Stop and exit?"):
            return
        stop_server()
    assert ROOT is not None
    ROOT.destroy()


def build_ui(root: tk.Tk | None = None) -> tk.Tk:
    global ROOT, STATUS_VAR, LOG_WIDGET
    ROOT = root or tk.Tk()
    ROOT.title("BOM Platform – Control Center")
    ROOT.protocol("WM_DELETE_WINDOW", on_close)

    top = tk.Frame(ROOT)
    top.pack(fill="x")
    tk.Button(top, text="▶ Start", command=start_server).pack(side="left")
    tk.Button(top, text="✖ Stop", command=stop_server).pack(side="left")
    tk.Button(top, text="↻ Restart", command=restart_server).pack(side="left")

    STATUS_VAR = tk.StringVar(value="RUNNING" if detect_server() else "STOPPED")
    tk.Label(top, textvariable=STATUS_VAR).pack(side="left", padx=10)

    manual = tk.LabelFrame(ROOT, text="Manual actions")
    manual.pack(fill="x", pady=5)
    tk.Label(manual, text="Run unit-tests").grid(row=0, column=0, sticky="w")
    tk.Button(manual, text="Run", command=run_tests).grid(row=0, column=1)
    tk.Label(manual, text="Trigger backup").grid(row=1, column=0, sticky="w")
    tk.Button(manual, text="Run", command=trigger_backup).grid(row=1, column=1)
    tk.Label(manual, text="Download BOM CSV").grid(row=2, column=0, sticky="w")
    tk.Button(
        manual,
        text="Save As…",
        command=lambda: download_export("/export/bom.csv", "bom.csv"),
    ).grid(row=2, column=1)
    tk.Label(manual, text="Download TestResults XLSX").grid(row=3, column=0, sticky="w")
    tk.Button(
        manual,
        text="Save As…",
        command=lambda: download_export("/export/testresults.xlsx", "testresults.xlsx"),
    ).grid(row=3, column=1)
    manual.grid_columnconfigure(0, weight=1)

    log_frame = tk.LabelFrame(ROOT, text="Live log tail (last 200 lines)")
    log_frame.pack(fill="both", expand=True, pady=5)
    LOG_WIDGET = ScrolledText(log_frame, state="disabled", height=20)
    LOG_WIDGET.pack(fill="both", expand=True)

    ROOT.after(LOG_UPDATE_MS, update_log)
    return ROOT


def detect_server() -> bool:
    try:
        requests.get(f"{BASE_URL}/health", timeout=1)
        return True
    except Exception:
        return False


def main() -> None:
    root = build_ui()
    update_status()
    root.mainloop()


if __name__ == "__main__":
    main()
