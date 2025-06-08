# root: gui/control_center.py
import os
import sys
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from tkinter.scrolledtext import ScrolledText

import requests
from app.config import DATABASE_URL

def _reexec_into_venv() -> None:
    """Relaunch with the venv's python if available."""
    if os.environ.get("BOM_NO_REEXEC"):
        return
    if sys.prefix == sys.base_prefix:
        root = Path(__file__).resolve().parents[1]
        exe = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )
        if exe.exists():
            os.environ["BOM_NO_REEXEC"] = "1"
            os.execv(str(exe), [str(exe)] + sys.argv)

_reexec_into_venv()

BASE_URL = "http://localhost:8000"
LOG_DIR = Path("logs")
LOG_UPDATE_MS = 1000

PROC: subprocess.Popen | None = None
TOKEN: str | None = None
ROOT: tk.Tk | None = None
STATUS_VAR: tk.StringVar | None = None
LOG_WIDGET: ScrolledText | None = None


class ServerTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x")
        tk.Button(btn_frame, text="▶ Start", command=start_server).pack(side="left")
        tk.Button(btn_frame, text="✖ Stop", command=stop_server).pack(side="left")
        tk.Button(btn_frame, text="↻ Restart", command=restart_server).pack(side="left")
        global STATUS_VAR, LOG_WIDGET
        STATUS_VAR = tk.StringVar(value="RUNNING" if detect_server() else "STOPPED")
        tk.Label(btn_frame, textvariable=STATUS_VAR).pack(side="left", padx=10)

        log_frame = tk.LabelFrame(self, text="Live log tail (last 200 lines)")
        log_frame.pack(fill="both", expand=True, pady=5)
        LOG_WIDGET = ScrolledText(log_frame, state="disabled", height=20)
        LOG_WIDGET.pack(fill="both", expand=True)


class BOMItemsTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.tree = ttk.Treeview(self, columns=("id", "pn", "desc", "qty", "ref"), show="headings")
        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
        self.tree.pack(fill="both", expand=True)
        btns = tk.Frame(self)
        btns.pack(fill="x")
        tk.Button(btns, text="Refresh", command=self.refresh).pack(side="left")
        tk.Button(btns, text="Add", command=self.add_item).pack(side="left")
        tk.Button(btns, text="Edit", command=self.edit_item).pack(side="left")
        tk.Button(btns, text="Delete", command=self.delete_item).pack(side="left")
        self.refresh()

    def api_headers(self) -> dict[str, str]:
        token = ensure_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def refresh(self) -> None:
        resp = requests.get(f"{BASE_URL}/bom/items", headers=self.api_headers())
        if resp.status_code == 200:
            for i in self.tree.get_children():
                self.tree.delete(i)
            for item in resp.json():
                self.tree.insert("", "end", values=(item["id"], item["part_number"], item["description"], item["quantity"], item.get("reference") or ""))

    def add_item(self) -> None:
        self._item_dialog()

    def edit_item(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0], "values")
        self._item_dialog(item)

    def delete_item(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = self.tree.item(sel[0], "values")[0]
        headers = self.api_headers()
        if not headers:
            return
        resp = requests.delete(f"{BASE_URL}/bom/items/{iid}", headers=headers)
        if resp.status_code == 204:
            self.refresh()
        else:
            messagebox.showerror("Error", resp.text)

    def _item_dialog(self, values: tuple | None = None) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Item" if values is None else "Edit Item")
        tk.Label(dlg, text="Part number").grid(row=0, column=0)
        pn = tk.Entry(dlg)
        pn.grid(row=0, column=1)
        tk.Label(dlg, text="Description").grid(row=1, column=0)
        desc = tk.Entry(dlg)
        desc.grid(row=1, column=1)
        tk.Label(dlg, text="Quantity").grid(row=2, column=0)
        qty = tk.Entry(dlg)
        qty.grid(row=2, column=1)
        tk.Label(dlg, text="Reference").grid(row=3, column=0)
        ref = tk.Entry(dlg)
        ref.grid(row=3, column=1)

        if values:
            pn.insert(0, values[1])
            desc.insert(0, values[2])
            qty.insert(0, values[3])
            ref.insert(0, values[4])

        def submit() -> None:
            data = {
                "part_number": pn.get(),
                "description": desc.get(),
                "quantity": int(qty.get() or 1),
                "reference": ref.get() or None,
            }
            headers = self.api_headers()
            if not headers:
                return
            if values:
                iid = values[0]
                resp = requests.put(f"{BASE_URL}/bom/items/{iid}", json=data, headers=headers)
            else:
                resp = requests.post(f"{BASE_URL}/bom/items", json=data, headers=headers)
            if resp.status_code in (200, 201):
                dlg.destroy()
                self.refresh()
            else:
                messagebox.showerror("Error", resp.text)

        tk.Button(dlg, text="Save", command=submit).grid(row=4, column=0, columnspan=2)
        dlg.grab_set()
        dlg.wait_window()


class ImportPDFTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        tk.Button(self, text="Select PDF", command=self.select_file).pack(pady=10)

    def select_file(self) -> None:
        token = ensure_token()
        if not token:
            return
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        with open(path, "rb") as f:
            files = {"file": (os.path.basename(path), f, "application/pdf")}
            resp = requests.post(
                f"{BASE_URL}/bom/import", files=files, headers={"Authorization": f"Bearer {token}"}
            )
        if resp.status_code == 200:
            messagebox.showinfo("Import", "Import complete")
        else:
            messagebox.showerror("Import failed", resp.text)


class QuoteTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.text = tk.Text(self, height=10, width=40)
        self.text.pack(fill="both", expand=True)
        tk.Button(self, text="Refresh", command=self.refresh).pack(pady=5)
        self.refresh()

    def refresh(self) -> None:
        resp = requests.get(f"{BASE_URL}/bom/quote")
        if resp.status_code == 200:
            self.text.delete("1.0", tk.END)
            self.text.insert(tk.END, resp.text)
        else:
            messagebox.showerror("Error", resp.text)


class TestResultsTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.tree = ttk.Treeview(
            self,
            columns=("id", "sn", "date", "result", "details"),
            show="headings",
        )
        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
        self.tree.pack(fill="both", expand=True)

        btns = tk.Frame(self)
        btns.pack(fill="x")
        tk.Button(btns, text="Refresh", command=self.refresh).pack(side="left")
        tk.Button(btns, text="Add", command=self.add_result).pack(side="left")
        self.refresh()

    def api_headers(self) -> dict[str, str]:
        token = ensure_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def refresh(self) -> None:
        resp = requests.get(f"{BASE_URL}/testresults", headers=self.api_headers())
        if resp.status_code == 200:
            for i in self.tree.get_children():
                self.tree.delete(i)
            for r in resp.json():
                self.tree.insert(
                    "",
                    "end",
                    values=(r["test_id"], r.get("serial_number"), r["date_tested"], r["result"], r.get("failure_details")),
                )

    def add_result(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Add Result")
        tk.Label(dlg, text="Serial number").grid(row=0, column=0)
        sn = tk.Entry(dlg)
        sn.grid(row=0, column=1)
        tk.Label(dlg, text="Pass (true/false)").grid(row=1, column=0)
        res = tk.Entry(dlg)
        res.grid(row=1, column=1)
        tk.Label(dlg, text="Details").grid(row=2, column=0)
        det = tk.Entry(dlg)
        det.grid(row=2, column=1)

        def submit() -> None:
            data = {
                "serial_number": sn.get() or None,
                "result": res.get().lower() in {"1", "true", "yes"},
                "failure_details": det.get() or None,
            }
            headers = self.api_headers()
            if not headers:
                return
            resp = requests.post(f"{BASE_URL}/testresults", json=data, headers=headers)
            if resp.status_code == 201:
                dlg.destroy()
                self.refresh()
            else:
                messagebox.showerror("Error", resp.text)

        tk.Button(dlg, text="Save", command=submit).grid(row=3, column=0, columnspan=2)
        dlg.grab_set()
        dlg.wait_window()


class TraceabilityTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        comp_f = tk.LabelFrame(self, text="Component")
        comp_f.pack(fill="x")
        tk.Label(comp_f, text="Part number").grid(row=0, column=0)
        self.comp_e = tk.Entry(comp_f)
        self.comp_e.grid(row=0, column=1)
        tk.Button(comp_f, text="Query", command=self.query_component).grid(row=0, column=2)

        board_f = tk.LabelFrame(self, text="Board")
        board_f.pack(fill="x")
        tk.Label(board_f, text="Serial number").grid(row=0, column=0)
        self.board_e = tk.Entry(board_f)
        self.board_e.grid(row=0, column=1)
        tk.Button(board_f, text="Query", command=self.query_board).grid(row=0, column=2)

        self.text = tk.Text(self, height=10)
        self.text.pack(fill="both", expand=True)

    def query_component(self) -> None:
        pn = self.comp_e.get()
        self.text.delete("1.0", tk.END)
        if not pn:
            return
        resp = requests.get(f"{BASE_URL}/traceability/component/{pn}")
        if resp.status_code == 200:
            self.text.insert(tk.END, resp.text)
        else:
            messagebox.showerror("Error", resp.text)


class ExportTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        tk.Button(self, text="Download BOM CSV", command=lambda: download_export("/export/bom.csv", "bom.csv")).pack(pady=5)
        tk.Button(self, text="Download TestResults XLSX", command=lambda: download_export("/export/testresults.xlsx", "testresults.xlsx")).pack(pady=5)
        tk.Button(self, text="Trigger Backup", command=trigger_backup).pack(pady=5)


class UsersTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.tree = ttk.Treeview(self, columns=("username", "role"), show="headings")
        for c in self.tree["columns"]:
            self.tree.heading(c, text=c)
        self.tree.pack(fill="both", expand=True)
        btns = tk.Frame(self)
        btns.pack(fill="x")
        tk.Button(btns, text="Refresh", command=self.refresh).pack(side="left")
        tk.Button(btns, text="Add", command=self.add_user).pack(side="left")
        self.refresh()

    def api_headers(self) -> dict[str, str]:
        token = ensure_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def refresh(self) -> None:
        # No endpoint for listing users; show admin only
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tree.insert("", "end", values=("admin", "admin"))

    def add_user(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Add User")
        tk.Label(dlg, text="Username").grid(row=0, column=0)
        user_e = tk.Entry(dlg)
        user_e.grid(row=0, column=1)
        tk.Label(dlg, text="Password").grid(row=1, column=0)
        pw_e = tk.Entry(dlg, show="*")
        pw_e.grid(row=1, column=1)

        def submit() -> None:
            headers = self.api_headers()
            if not headers:
                return
            data = {"username": user_e.get(), "password": pw_e.get(), "role": "user"}
            resp = requests.post(f"{BASE_URL}/auth/register", json=data, headers=headers)
            if resp.status_code == 201:
                dlg.destroy()
                self.refresh()
            else:
                messagebox.showerror("Error", resp.text)

        tk.Button(dlg, text="Save", command=submit).grid(row=2, column=0, columnspan=2)
        dlg.grab_set()
        dlg.wait_window()


class SettingsTab(tk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.mode = tk.StringVar(value="sqlite" if "sqlite" in DATABASE_URL else "postgres")
        tk.Radiobutton(self, text="Embedded SQLite", variable=self.mode, value="sqlite").pack(anchor="w")
        tk.Radiobutton(self, text="External Postgres", variable=self.mode, value="postgres").pack(anchor="w")
        tk.Button(self, text="Apply", command=self.apply).pack(pady=5)

    def apply(self) -> None:
        new_url = "sqlite:///./app.db" if self.mode.get() == "sqlite" else "postgresql://user:pass@localhost/bom"
        if new_url != DATABASE_URL:
            os.environ["DATABASE_URL"] = new_url
            messagebox.showinfo("Settings", "Restart application for changes to take effect")


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
    global ROOT
    ROOT = root or tk.Tk()
    ROOT.title("BOM Platform – Control Center")
    ROOT.protocol("WM_DELETE_WINDOW", on_close)

    notebook = ttk.Notebook(ROOT)
    notebook.pack(fill="both", expand=True)

    server = ServerTab(notebook)
    bom = BOMItemsTab(notebook)
    pdf = ImportPDFTab(notebook)
    quote = QuoteTab(notebook)
    results = TestResultsTab(notebook)
    trace = TraceabilityTab(notebook)
    export = ExportTab(notebook)
    users = UsersTab(notebook)
    settings = SettingsTab(notebook)

    notebook.add(server, text="Server")
    notebook.add(bom, text="BOM Items")
    notebook.add(pdf, text="Import PDF")
    notebook.add(quote, text="Quote")
    notebook.add(results, text="Test Results")
    notebook.add(trace, text="Traceability")
    notebook.add(export, text="Exports & Backups")
    notebook.add(users, text="Users")
    notebook.add(settings, text="Settings")

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
