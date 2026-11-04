"""Small Tk desktop interface; XML work runs off the UI thread."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from diff_engine import Plan, scan
from file_transaction import PatchResult, TransactionError
from patch_engine import apply_patch, revert_patch, save_report


class Application(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=16)
        self.root = root
        self.plan: Plan | None = None
        self.undo_result: PatchResult | None = None
        self.report_target: Plan | PatchResult | None = None
        self.last_error = ""
        self.busy = False
        self.messages: queue.Queue = queue.Queue()
        self.paths = {name: tk.StringVar() for name in ["Master", "Source", "XSD"]}
        self.controls = []
        self.rows = {}
        self.pack(fill="both", expand=True)
        root.title("AUTOSAR S/R Incremental Patcher")
        root.geometry("1150x760")
        root.minsize(850, 600)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(6, weight=1)
        ttk.Label(self, text="AUTOSAR S/R Incremental Patcher", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            self,
            text="Add generated data elements, ComSpecs and runnable accesses. "
            "Revert Last Apply restores original files and modification times during this session.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))
        for row, name in enumerate(self.paths, start=2):
            label = (
                f"{name} ARXML Folder" if name in {"Master", "Source"} else "XSD File (optional)"
            )
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            entry = ttk.Entry(self, textvariable=self.paths[name])
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            button = ttk.Button(self, text="Browse…", command=lambda n=name: self.browse(n))
            button.grid(row=row, column=2, padx=(8, 0), pady=4)
            self.controls.extend([entry, button])
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)
        self.scan_button = ttk.Button(actions, text="Scan & Compare", command=self.start_scan)
        self.apply_button = ttk.Button(actions, text="Apply to Master", command=self.start_apply)
        self.revert_button = ttk.Button(
            actions, text="Revert Last Apply", command=self.start_revert
        )
        self.report_button = ttk.Button(actions, text="Save Report…", command=self.start_report)
        for button in [self.scan_button, self.apply_button, self.revert_button, self.report_button]:
            button.pack(side="left", padx=(0, 8))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self.progress.pack(side="right")
        table = ttk.Frame(self)
        table.grid(row=6, column=0, columnspan=3, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table,
            columns=("Type", "Object", "Status", "Reason"),
            show="headings",
            selectmode="browse",
        )
        for name, width in [("Type", 120), ("Object", 460), ("Status", 100), ("Reason", 450)]:
            self.tree.heading(name, text=name)
            self.tree.column(name, width=width, stretch=name in {"Object", "Reason"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        for status, color in [
            ("ADD", "#15633d"),
            ("CONFLICT", "#b42318"),
            ("UNSUPPORTED", "#8f4b00"),
            ("ERROR", "#b42318"),
        ]:
            self.tree.tag_configure(status, foreground=color)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.summary = tk.StringVar(value="Select Master and Source folders, then scan.")
        ttk.Label(self, textvariable=self.summary, wraplength=1050).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(12, 6)
        )
        self.detail = tk.Text(self, height=5, wrap="word", state="disabled", font=("Consolas", 10))
        self.detail.grid(row=8, column=0, columnspan=3, sticky="ew")
        self.status = tk.StringVar(value="Ready — supported profile: AUTOSAR_00047")
        ttk.Label(self, textvariable=self.status, wraplength=1050).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        for name in ["Master", "Source"]:
            self.paths[name].trace_add("write", self.invalidate)
        self._buttons()
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        if self.busy:
            self.status.set("Please wait for the current operation to finish before closing.")
        elif self.undo_result and self.undo_result.state == "recovery_required":
            self.status.set("Recovery is incomplete. Use Revert Last Apply before closing.")
        else:
            self.root.destroy()

    def _buttons(self):
        self.scan_button.configure(state="disabled" if self.busy else "normal")
        ready = self.plan is not None and self.plan.valid and bool(self.plan.operations)
        recovery = self.undo_result is not None and self.undo_result.state == "recovery_required"
        ready = ready and not recovery
        self.apply_button.configure(state="normal" if ready and not self.busy else "disabled")
        can_revert = (
            self.undo_result is not None
            and self.undo_result.can_revert
            and self.selected_master_matches_undo()
        )
        self.revert_button.configure(state="normal" if can_revert and not self.busy else "disabled")
        self.report_button.configure(
            state="normal" if self.report_target and not self.busy else "disabled"
        )
        for control in self.controls:
            control.configure(state="disabled" if self.busy else "normal")

    def selected_master_matches_undo(self):
        try:
            return (
                self.undo_result is not None
                and Path(self.paths["Master"].get()).expanduser().resolve()
                == self.undo_result.master_folder
            )
        except (OSError, ValueError, RuntimeError):
            return False

    def invalidate(self, *_):
        self.plan = None
        self.report_target = None
        self.last_error = ""
        self.status.set("Inputs changed — scan again.")
        self._buttons()

    def browse(self, name):
        selected = (
            filedialog.askopenfilename(title="Select AUTOSAR XSD", filetypes=[("XSD", "*.xsd")])
            if name == "XSD"
            else filedialog.askdirectory(title=f"Select {name} folder", mustexist=True)
        )
        if selected:
            self.paths[name].set(selected)

    def run_worker(self, action, work):
        self.busy = True
        self._buttons()
        self.progress.start(12)
        self.status.set(
            {
                "scan": "Scanning ARXML…",
                "apply": "Patching and validating…",
                "revert": "Restoring original files and modification times…",
                "report": "Saving report…",
            }[action]
        )

        def worker():
            try:
                self.messages.put((action, work(), None))
            except TransactionError as exc:
                self.messages.put((action, exc.result, traceback.format_exc()))
            except Exception:
                self.messages.put((action, None, traceback.format_exc()))

        threading.Thread(target=worker, daemon=False).start()

    def start_scan(self):
        master, source = (self.paths[n].get().strip() for n in ["Master", "Source"])
        if not master or not source:
            messagebox.showerror("Select inputs", "Select both Master and Source ARXML folders.")
            return
        self.plan = None
        self.report_target = None
        self.last_error = ""
        self.run_worker("scan", lambda: scan(master, source))

    def start_apply(self):
        plan = self.plan
        if self.busy or not plan or not plan.valid or not plan.operations:
            return
        if self.undo_result and self.undo_result.state == "recovery_required":
            return
        xsd = self.paths["XSD"].get().strip() or None
        self.run_worker("apply", lambda: apply_patch(plan, xsd))

    def start_revert(self):
        result = self.undo_result
        if not result or not result.can_revert or self.busy:
            return
        if not self.selected_master_matches_undo():
            return
        self.report_target = result
        self.run_worker("revert", lambda: revert_patch(result))

    def start_report(self):
        if self.report_target is None or self.busy:
            return
        folder = filedialog.askdirectory(
            title="Select a new or empty report folder", mustexist=False
        )
        if folder:
            plan, error = self.report_target, self.last_error
            self.run_worker(
                "report", lambda: save_report(plan, folder, "failed" if error else "scanned", error)
            )

    def poll(self):
        try:
            action, result, error = self.messages.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.busy = False
        self.progress.stop()
        if error:
            if isinstance(result, PatchResult):
                self.report_target = result
                if result.can_revert:
                    self.undo_result = result
            if action in {"apply", "revert"}:
                self.plan = None
            self.last_error = error
            self.status.set("Operation failed. See details; Save Report includes the full error.")
            self.set_detail(error)
            messagebox.showerror("Operation failed", error.strip().splitlines()[-1])
        elif action == "scan":
            self.plan = result
            self.report_target = result
            self.display_plan()
            if result.valid and not result.operations:
                messagebox.showinfo(
                    "Scan complete",
                    "Preflight checks passed.\n"
                    "No supported S/R additions are needed.\n\n"
                    f"{result.summary['EXISTS']} equivalent item(s) found in Master.",
                    parent=self.root,
                )
        elif action == "apply":
            self.plan = None
            self.last_error = ""
            self.undo_result = result
            self.report_target = result
            self.status.set(f"Master updated: {result.master_folder}. Scan again to refresh.")
            self.summary.set(f"Updated {len(result.changed_files)} Master file(s).")
            self.set_detail(
                f"Master: {result.master_folder}\n"
                "References, structure, isolation and idempotency passed.\n"
                "Revert Last Apply is available while this app stays open."
            )
            messagebox.showinfo(
                "Patch complete",
                "Master updated in place.\n"
                "Use Revert Last Apply to restore the previous files and modification times.\n"
                "Undo data expires when this app closes.",
            )
        elif action == "revert":
            self.plan = None
            self.last_error = ""
            self.report_target = result
            self.tree.delete(*self.tree.get_children())
            self.rows.clear()
            self.summary.set(f"Restored {len(result.changed_files)} Master file(s).")
            self.status.set("Revert complete. Scan again to compare.")
            self.set_detail(
                f"Master: {result.master_folder}\nOriginal bytes and modification times restored."
            )
            messagebox.showinfo(
                "Revert complete", "Original files and modification times restored."
            )
        else:
            self.status.set(f"Reports saved: {result}")
        self._buttons()
        self.root.after(100, self.poll)

    def display_plan(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        order = {"CONFLICT": 0, "UNSUPPORTED": 1, "ADD": 2}
        visible = (record for record in self.plan.records if record.Action in order)
        for record in sorted(visible, key=lambda r: order[r.Action]):
            row = self.tree.insert(
                "",
                "end",
                values=(record.Category, record.ObjectPath, record.Action, record.Reason),
                tags=(record.Action,),
            )
            self.rows[row] = (
                f"{record.ObjectPath}\n{record.Reason}\nSource: {record.SourceFile}\n"
                f"Target: {record.TargetFile}\nDependencies: {', '.join(record.Dependencies)}"
            )
        for error in self.plan.errors:
            row = self.tree.insert("", "end", values=("Input", "", "ERROR", error), tags=("ERROR",))
            self.rows[row] = error
        s = self.plan.summary
        self.summary.set(
            f"New Data Elements: {s['DataElement']}    New ComSpecs: {s['ComSpec']}    "
            f"New Access Points: {s['RunnableAccess']}    Conflicts: {s['CONFLICT']}    "
            f"Unsupported: {s['UNSUPPORTED']}    Exists: {s['EXISTS']}    Errors: {s['Errors']}"
        )
        self.status.set(
            "Preflight passed — ready to patch."
            if self.plan.valid and self.plan.operations
            else "No additions needed."
            if self.plan.valid
            else "Preflight blocked — review errors, conflicts or unsupported items."
        )
        self.set_detail(
            "Select a row for full paths and dependency details. "
            "XSD validation runs on Apply when an XSD file is selected."
        )

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_detail(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.set_detail(self.rows.get(selected[0], ""))


def launch():
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    Application(root)
    root.mainloop()
