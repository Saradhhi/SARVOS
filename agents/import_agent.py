"""
agents/import_agent.py

Ingests a job-application-history CSV exported from an external auto-apply
tool (LoopCV, Teal, AutoApplyAI, etc.) into the ACTIVE candidate's local
application log.

WHY THIS SHAPE. SARVOS deliberately does not scrape LinkedIn, cross logins,
or auto-submit -- see the job assistant's docstring. Those are the parts the
external tool does, in a browser you control, under your own credentials.
This agent handles only the safe, reversible handoff: a file the external
tool already produced is READ and its rows recorded. SARVOS never talks to
LoopCV, holds no LoopCV credentials, and depends on no live service. If
LoopCV changes their API tomorrow, a CSV you exported yesterday still
imports. That durability is the whole reason this is a file drop and not an
API client.

The importer is SCHEMA-TOLERANT on purpose. Different tools name their
columns differently ("Company" vs "company_name" vs "Employer"), and I will
not hardcode one tool's exact headers and have it silently import nothing
when the headers differ. Instead it sniffs likely column names and tells you
plainly what it mapped, so a mismatch is visible rather than silent.

Everything here is SAFE: reading a CSV and recording rows changes nothing
irreversible, and duplicates are skipped rather than piling up.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from agents import job_config as config
from agents.automation import resolve_safe_path
from agents.base import BaseAgent
from agents.import_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task

# Where exported files are expected to land. A plain local folder -- which
# also transparently covers Google Drive: if your Drive desktop app syncs a
# folder to disk, point SARVOS_IMPORT_DIR at that synced path and it works
# identically. SARVOS reads a local directory either way; whether Drive is
# filling it is invisible here.
import os
IMPORT_DIR = os.environ.get("SARVOS_IMPORT_DIR", "sarvos_workspace/imported")

# Candidate column names -> the field we store. Each value is a list of
# lowercased header spellings we'll accept, most-specific first. Sniffed
# case-insensitively with surrounding spaces/underscores normalised.
_COLUMN_HINTS = {
    "company": ["company", "company name", "employer", "organisation", "organization"],
    "role": ["role", "job title", "title", "position", "job"],
    "url": ["url", "job url", "link", "job link", "posting url"],
    "status": ["status", "application status", "state"],
    "applied_at": ["applied at", "date applied", "applied date", "date", "applied on",
                   "application date", "submitted"],
    "source": ["source", "job board", "board", "platform", "via"],
}


def _norm(header: str) -> str:
    return header.strip().lower().replace("_", " ")


def _build_column_map(fieldnames: list[str]) -> dict[str, str]:
    """Map our fields to the actual CSV headers present. Returns {field:
    real_header}. A field absent from the CSV simply won't appear."""
    normalised = {_norm(h): h for h in (fieldnames or [])}
    mapping: dict[str, str] = {}
    for field, hints in _COLUMN_HINTS.items():
        for hint in hints:
            if hint in normalised:
                mapping[field] = normalised[hint]
                break
    return mapping


class ImportAgent(BaseAgent):
    name = AgentName.IMPORT

    def __init__(self, memory, job=None):
        super().__init__(memory)
        # Reuse the JobAgent for candidate resolution and the per-candidate
        # application store, so imported rows live exactly where manually
        # logged ones do -- one source of truth.
        self.job = job

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        if intent.operation == Operation.LIST_IMPORTS:
            return self._list_files(task)
        if intent.operation == Operation.IMPORT_FILE:
            return self._import(task, intent.filename)
        return self._fail(
            task,
            "I couldn't work out an import action. Try 'list import files' or "
            "'import applications from <file.csv>'.",
        )

    # ---- helpers ---------------------------------------------------------

    def _fail(self, task: Task, msg: str, error: str = "import_bad_request") -> AgentResult:
        return AgentResult(task_id=task.task_id, agent=self.name, success=False,
                           output=msg, error=error)

    def _active_candidate(self, task: Task):
        """Imported applications belong to a candidate, same as logged ones."""
        if self.job is None:
            return self._fail(task, "Import isn't wired to the job assistant.",
                              "import_not_wired")
        name = self.job._active_candidate()
        if name is None:
            return self._fail(
                task,
                "No active candidate. Pick one with 'use candidate <name>' "
                "first -- imported applications are recorded against them.",
                "no_active_candidate",
            )
        return name

    # ---- operations ------------------------------------------------------

    def _list_files(self, task: Task) -> AgentResult:
        root = Path(IMPORT_DIR)
        if not root.is_dir():
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"No import folder yet. Create {IMPORT_DIR} and drop your "
                       f"exported CSV there.",
                data={"files": []},
            )
        files = sorted(p.name for p in root.iterdir()
                       if p.is_file() and p.suffix.lower() in (".csv", ".tsv"))
        if not files:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True,
                output=f"No CSV files in {IMPORT_DIR}. Export your application "
                       f"history from LoopCV/Teal and drop it there.",
                data={"files": []},
            )
        listing = "\n".join(f"  {f}" for f in files)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"{len(files)} import file(s) in {IMPORT_DIR}:\n{listing}\n\n"
                   f"Import one with 'import applications from <name>'.",
            data={"files": files},
        )

    def _import(self, task: Task, filename: str) -> AgentResult:
        candidate = self._active_candidate(task)
        if isinstance(candidate, AgentResult):
            return candidate

        try:
            path = resolve_safe_path(filename, workspace_root=IMPORT_DIR)
        except Exception as e:
            return self._fail(task, f"Refusing to read '{filename}': {e}.",
                              "unsafe_import_path")
        if not path.is_file():
            return self._fail(
                task, f"'{filename}' isn't in {IMPORT_DIR}. Try 'list import files'.",
                "import_file_not_found",
            )

        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            return self._fail(task, f"Couldn't read '{filename}': {e}", "import_read_failed")

        # Sniff delimiter (CSV vs TSV) rather than assume.
        delimiter = "\t" if path.suffix.lower() == ".tsv" or "\t" in raw[:1000] else ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        colmap = _build_column_map(reader.fieldnames or [])

        if "company" not in colmap:
            return self._fail(
                task,
                "I couldn't find a company column in that file. The headers I "
                f"saw were: {reader.fieldnames}. I look for a column named "
                "something like 'Company', 'Employer', or 'Company Name'. If "
                "yours differs, rename the header and re-import.",
                "no_company_column",
            )

        # Existing applications, to dedupe against. A row is a duplicate if the
        # same company + role already exists for this candidate -- imported or
        # manual. Re-importing the same export is therefore safe and idempotent.
        existing = self.job._load_applications(candidate)
        seen = {(a.get("company", "").strip().lower(), a.get("role", "").strip().lower())
                for a in existing}
        next_id = max((a.get("id", 0) for a in existing), default=0) + 1

        added, skipped, blank = 0, 0, 0
        for row in reader:
            company = (row.get(colmap["company"], "") or "").strip()
            if not company:
                blank += 1
                continue
            role = (row.get(colmap.get("role", ""), "") or "").strip()
            key = (company.lower(), role.lower())
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            existing.append({
                "id": next_id,
                "company": company,
                "role": role,
                "url": (row.get(colmap.get("url", ""), "") or "").strip(),
                "status": (row.get(colmap.get("status", ""), "") or "applied").strip() or "applied",
                "applied_at": (row.get(colmap.get("applied_at", ""), "") or "").strip(),
                "source": (row.get(colmap.get("source", ""), "") or "imported").strip() or "imported",
                "imported_from": path.name,
            })
            next_id += 1
            added += 1

        self.job._save_applications(candidate, existing)

        mapped = ", ".join(f"{k}<-'{v}'" for k, v in colmap.items())
        lines = [
            f"Imported into {candidate} from {path.name}:",
            f"  {added} new application(s) added.",
        ]
        if skipped:
            lines.append(f"  {skipped} skipped as duplicates (already recorded).")
        if blank:
            lines.append(f"  {blank} row(s) skipped with no company name.")
        lines.append(f"\nColumns I mapped: {mapped}")
        lines.append("See them with 'list applications'.")
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output="\n".join(lines),
            data={"candidate": candidate, "added": added, "skipped": skipped,
                  "blank": blank, "column_map": colmap},
        )
