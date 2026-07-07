"""
TerminalAgent -- real system diagnostics (running processes, current user,
hostname, OS version) via direct Python library calls: psutil, getpass,
socket, platform. Deliberately NOT subprocess/shell execution -- see
agents/terminal_intent.py's module docstring for why arbitrary command
execution isn't something this project builds.

Real-capability agent, all operations read-only and SAFE -- no
confirmation gating needed, same as System Info.
"""

from __future__ import annotations

import getpass
import platform
import socket

import psutil

from agents.base import BaseAgent
from agents.terminal_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task

MAX_PROCESSES_SHOWN = 15


class TerminalAgent(BaseAgent):
    name = AgentName.TERMINAL

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        handlers = {
            Operation.PROCESSES: self._processes,
            Operation.CURRENT_USER: self._current_user,
            Operation.HOSTNAME: self._hostname,
            Operation.OS_VERSION: self._os_version,
        }
        handler = handlers.get(intent.operation)
        if handler is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"I couldn't work out what terminal info you wanted "
                    f"from: '{task.instruction}'. Try 'show me the "
                    f"running processes', 'whoami', 'what's my hostname', "
                    f"or 'what OS version am I running'."
                ),
            )
        return handler(task)

    def _processes(self, task: Task) -> AgentResult:
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda p: p.get("memory_percent") or 0, reverse=True)
        top = procs[:MAX_PROCESSES_SHOWN]

        lines = [f"{p['name']} (PID {p['pid']})" for p in top]
        output = f"Top {len(top)} processes by memory usage:\n" + "\n".join(lines)
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"process_count": len(procs), "top_processes": top},
        )

    def _current_user(self, task: Task) -> AgentResult:
        user = getpass.getuser()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"You're logged in as: {user}",
            data={"user": user},
        )

    def _hostname(self, task: Task) -> AgentResult:
        name = socket.gethostname()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"Hostname: {name}",
            data={"hostname": name},
        )

    def _os_version(self, task: Task) -> AgentResult:
        version_str = platform.platform()
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output=f"OS: {version_str}",
            data={"os_version": version_str, "system": platform.system()},
        )
