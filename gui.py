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
            text="Add missing S/R items from Source to Master. Source files are never changed.\n"
            "Undo is available for the latest update only, while this app stays open.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))
        for row, name in enumerate(self.paths, start=2):
            label = {
                "Master": "Master folder (to update)",
                "Source": "Source folder (generated ARXML)",
                "XSD": "Validation schema (.xsd, optional)",
            }[name]
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            entry = ttk.Entry(self, textvariable=self.paths[name])
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            button = ttk.Button(self, text="Browse…", command=lambda n=name: self.browse(n))
            button.grid(row=row, column=2, padx=(8, 0), pady=4)
            self.controls.extend([entry, button])
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)
        self.scan_button = ttk.Button(actions, text="Preview Changes", command=self.start_scan)
        self.apply_button = ttk.Button(actions, text="Update Master", command=self.start_apply)
        self.revert_button = ttk.Button(actions, text="Undo Last Update", command=self.start_revert)
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
        self.summary = tk.StringVar(
            value="Select Master and Source folders, then click Preview Changes."
        )
        ttk.Label(self, textvariable=self.summary, wraplength=1050).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(12, 6)
        )
        self.detail = tk.Text(self, height=5, wrap="word", state="disabled", font=("Consolas", 10))
        self.detail.grid(row=8, column=0, columnspan=3, sticky="ew")
        self.status = tk.StringVar(value="Ready — supported ARXML version: AUTOSAR_00047")
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
            self.status.set(
                "Recovery is incomplete. Keep this app open and use "
                "Undo Last Update to restore Master."
            )
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
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.summary.set("Preview Changes to compare the selected folders.")
        self.set_detail("")
        self.status.set("Folders changed — click Preview Changes to refresh the results.")
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
                "scan": "Comparing Master and Source…",
                "apply": "Validating changes and updating Master…",
                "revert": "Restoring Master files and their original Date modified…",
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
            messagebox.showerror(
                "Folders required",
                "Select the Master folder to update and the Source folder "
                "containing generated ARXML.",
            )
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
            title = {
                "scan": "Could not preview changes",
                "apply": "Master update failed",
                "revert": "Could not undo the update",
                "report": "Could not save the report",
            }[action]
            guidance = "See the details below for the full error."
            if isinstance(result, PatchResult) and result.state == "recovery_required":
                guidance = (
                    "Some Master files may still be changed. Keep this app open. "
                    "Resolve the error, then click Undo Last Update to restore the original files."
                )
            elif action == "revert":
                guidance = "Resolve the error, then try Undo Last Update again. Keep this app open."
            self.status.set(f"{title}. {guidance}")
            self.set_detail(error)
            messagebox.showerror(title, f"{error.strip().splitlines()[-1]}\n\n{guidance}")
        elif action == "scan":
            self.plan = result
            self.report_target = result
            self.display_plan()
            if result.valid and not result.operations:
                messagebox.showinfo(
                    "No changes needed",
                    "No issues were found by the scan checks.\n"
                    "No supported S/R items need to be added to Master.\n\n"
                    f"Already present: {result.summary['EXISTS']} item(s).\n"
                    "XSD validation was not run; it runs only when updating Master "
                    "with a schema selected.",
                    parent=self.root,
                )
        elif action == "apply":
            self.plan = None
            self.tree.delete(*self.tree.get_children())
            self.rows.clear()
            self.last_error = ""
            self.undo_result = result
            self.report_target = result
            self.status.set(
                f"Master updated: {result.master_folder}. Preview Changes to compare again."
            )
            self.summary.set(f"Updated {len(result.changed_files)} Master file(s).")
            self.set_detail(
                f"Master: {result.master_folder}\n"
                "ARXML structure and reference checks passed.\n"
                f"{self.validation_summary(result)}\n"
                "Undo Last Update is available while this app stays open."
            )
            messagebox.showinfo(
                "Master updated",
                f"Updated {len(result.changed_files)} Master file(s).\n"
                f"{self.validation_summary(result)}\n\n"
                "Undo Last Update restores the previous contents and Date modified.\n"
                "Only the latest update can be undone. Closing this app clears undo data.",
            )
        elif action == "revert":
            self.plan = None
            self.last_error = ""
            self.report_target = result
            self.tree.delete(*self.tree.get_children())
            self.rows.clear()
            self.summary.set(f"Restored {len(result.changed_files)} Master file(s).")
            self.status.set("Undo complete. Click Preview Changes to compare again.")
            self.set_detail(
                f"Master: {result.master_folder}\n"
                "Original file contents and Date modified restored."
            )
            messagebox.showinfo(
                "Undo complete", "Master files and their original Date modified have been restored."
            )
        else:
            self.status.set(f"Reports saved: {result}")
        self._buttons()
        self.root.after(100, self.poll)

    @staticmethod
    def validation_summary(result):
        return (
            "XSD validation passed."
            if result.report.get("xsd_validated")
            else "XSD validation was not run (no schema selected)."
        )

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
        for warning in self.plan.warnings:
            row = self.tree.insert("", "end", values=("Connectivity", "", "WARNING", warning))
            self.rows[row] = warning
        s = self.plan.summary
        self.summary.set(
            f"New Types: {s['DataType']}    Interfaces: {s['Interface']}    Ports: {s['Port']}    "
            f"Connectors: {s['Connector']}    Warnings: {len(self.plan.warnings)}    "
            f"New Data Elements: {s['DataElement']}    New ComSpecs: {s['ComSpec']}    "
            f"New Access Points: {s['RunnableAccess']}    Conflicts: {s['CONFLICT']}    "
            f"Unsupported: {s['UNSUPPORTED']}    Already present: {s['EXISTS']}    "
            f"Errors: {s['Errors']}"
        )
        self.status.set(
            "Checks passed — review the proposed additions, then click Update Master."
            if self.plan.valid and self.plan.operations
            else "No supported additions needed. Master has not been changed."
            if self.plan.valid
            else "Update unavailable — review the errors, conflicts or unsupported items above."
        )
        self.set_detail(
            "Select an item to see file paths and required dependencies.\n"
            "XSD is an optional AUTOSAR schema file for checking XML structure. "
            "It is checked during Update Master. Without it, only the built-in checks run."
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
