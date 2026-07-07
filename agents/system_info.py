"""
SystemInfoAgent -- real CPU/RAM/disk/battery/network queries via psutil.
Entirely read-only: every operation here is SAFE, no confirmation gating
needed at all (unlike Automation/Browser, which have SENSITIVE/DESTRUCTIVE
operations).

Fourth real-capability agent (after Automation, Browser, Research) --
notably the first one that's genuinely trivial to make fully real and
fully tested: no network dependency, no sandboxing concerns, works
identically in any environment (confirmed directly in the sandbox this
was built in, a headless Linux container with no battery/GPU -- handled
gracefully rather than assumed away).
"""

from __future__ import annotations

import psutil

from agents.base import BaseAgent
from agents.system_info_intent import Operation, classify
from core.schemas import AgentName, AgentResult, Task


def _format_bytes(n: int) -> str:
    """Human-readable byte formatting (GB is what matters most here)."""
    gb = n / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = n / (1024 ** 2)
    return f"{mb:.0f} MB"


class SystemInfoAgent(BaseAgent):
    name = AgentName.SYSTEM_INFO

    def handle(self, task: Task) -> AgentResult:
        intent = classify(task.instruction)
        handlers = {
            Operation.CPU: self._cpu,
            Operation.RAM: self._ram,
            Operation.DISK: self._disk,
            Operation.BATTERY: self._battery,
            Operation.NETWORK: self._network,
            Operation.ALL: self._all,
        }
        handler = handlers.get(intent.operation)
        if handler is None:
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=False,
                output=(
                    f"I couldn't work out what system info you wanted from: "
                    f"'{task.instruction}'. Try 'check my cpu', 'how much "
                    f"ram do I have', 'check my disk usage', 'battery "
                    f"status', or 'system info' for everything."
                ),
            )
        return handler(task)

    def _cpu(self, task: Task) -> AgentResult:
        percent = psutil.cpu_percent(interval=0.3)
        count = psutil.cpu_count()
        output = f"CPU: {percent:.0f}% used across {count} core(s)."
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"cpu_percent": percent, "cpu_count": count},
        )

    def _ram(self, task: Task) -> AgentResult:
        mem = psutil.virtual_memory()
        output = (
            f"RAM: {mem.percent:.0f}% used "
            f"({_format_bytes(mem.used)} of {_format_bytes(mem.total)})."
        )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"ram_percent": mem.percent, "ram_total": mem.total, "ram_used": mem.used},
        )

    def _disk(self, task: Task) -> AgentResult:
        disk = psutil.disk_usage("/")
        output = (
            f"Disk: {disk.percent:.0f}% used "
            f"({_format_bytes(disk.used)} of {_format_bytes(disk.total)}, "
            f"{_format_bytes(disk.free)} free)."
        )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"disk_percent": disk.percent, "disk_total": disk.total, "disk_free": disk.free},
        )

    def _battery(self, task: Task) -> AgentResult:
        battery = psutil.sensors_battery()
        if battery is None:
            output = "No battery detected -- this looks like a desktop system."
            return AgentResult(
                task_id=task.task_id, agent=self.name, success=True, output=output,
                data={"battery": {"present": False, "percent": None, "power_plugged": None}},
            )
        status = "plugged in" if battery.power_plugged else "on battery"
        output = f"Battery: {battery.percent:.0f}% ({status})."
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={
                "battery": {
                    "present": True,
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                }
            },
        )

    def _network(self, task: Task) -> AgentResult:
        net = psutil.net_io_counters()
        output = (
            f"Network: {_format_bytes(net.bytes_sent)} sent, "
            f"{_format_bytes(net.bytes_recv)} received (since last reboot)."
        )
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True, output=output,
            data={"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv},
        )

    def _all(self, task: Task) -> AgentResult:
        cpu = self._cpu(task)
        ram = self._ram(task)
        disk = self._disk(task)
        battery = self._battery(task)
        lines = [cpu.output, ram.output, disk.output, battery.output]
        return AgentResult(
            task_id=task.task_id, agent=self.name, success=True,
            output="\n".join(lines),
            data={
                "cpu": cpu.data, "ram": ram.data, "disk": disk.data,
                "battery": battery.data["battery"],
            },
        )
