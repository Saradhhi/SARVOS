"""
Parses computer-control instructions into a structured intent. Same
deterministic-classification pattern as every other agent here.

SCOPE NOTE: deliberately does NOT include keyboard/mouse simulation or
hotkey automation. Same reasoning as Terminal's exclusion of arbitrary
shell commands -- "simulate any keystroke or click" is an unbounded
capability fundamentally different from every allowlisted action here;
it could type or click anything, in any application. Window
resize/move/minimize and screen/mic recording are also out of scope for
this version -- real, separate pieces of work, not quick additions.

Risk tiers:
- SAFE: screenshot, clipboard read/write, lock workstation -- all fully
  reversible, no data-loss risk.
- SENSITIVE: volume/brightness changes, launching an application --
  real effects, but easily undone, matching write_file's tier.
- DESTRUCTIVE: closing an application (risk of losing unsaved work),
  shutdown, restart, sleep -- interrupts everything, requires the
  orchestrator's real confirmation gate before anything executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.schemas import RiskLevel


class Operation(str, Enum):
    SCREENSHOT = "screenshot"
    READ_CLIPBOARD = "read_clipboard"
    WRITE_CLIPBOARD = "write_clipboard"
    MUTE = "mute"
    UNMUTE = "unmute"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    SET_VOLUME = "set_volume"
    BRIGHTNESS_UP = "brightness_up"
    BRIGHTNESS_DOWN = "brightness_down"
    SET_BRIGHTNESS = "set_brightness"
    LOCK = "lock"
    LAUNCH_APP = "launch_app"
    CLOSE_APP = "close_app"
    SHUTDOWN = "shutdown"
    RESTART = "restart"
    SLEEP = "sleep"
    UNKNOWN = "unknown"


@dataclass
class ComputerControlIntent:
    operation: Operation
    risk: RiskLevel
    text_arg: str | None = None
    numeric_arg: int | None = None
    raw_instruction: str = ""


def _mk(operation: Operation, risk: RiskLevel, **kwargs) -> ComputerControlIntent:
    return ComputerControlIntent(operation=operation, risk=risk, **kwargs)


def classify(instruction: str) -> ComputerControlIntent:
    text = instruction.strip()
    lowered = text.lower()

    if re.search(r"\btake a screenshot\b|\bscreenshot\b", lowered):
        return _mk(Operation.SCREENSHOT, RiskLevel.SAFE, raw_instruction=instruction)

    if re.search(r"\bread (?:the )?clipboard\b|\bwhat'?s (?:in|on) (?:my |the )?clipboard\b", lowered):
        return _mk(Operation.READ_CLIPBOARD, RiskLevel.SAFE, raw_instruction=instruction)

    write_clip_match = re.search(
        r"\bcopy\s+['\"](.+?)['\"]\s+to\s+(?:the |my )?clipboard\b|"
        r"\bset\s+(?:the |my )?clipboard\s+to\s+['\"](.+?)['\"]",
        text, re.I,
    )
    if write_clip_match:
        value = write_clip_match.group(1) or write_clip_match.group(2)
        return _mk(Operation.WRITE_CLIPBOARD, RiskLevel.SAFE, text_arg=value, raw_instruction=instruction)

    if re.search(r"\block (?:my |the )?(?:computer|workstation|screen|pc)\b", lowered):
        return _mk(Operation.LOCK, RiskLevel.SAFE, raw_instruction=instruction)

    set_vol_match = re.search(r"\bset (?:the |my )?volume to (\d{1,3})%?", lowered)
    if set_vol_match:
        return _mk(Operation.SET_VOLUME, RiskLevel.SENSITIVE,
                    numeric_arg=int(set_vol_match.group(1)), raw_instruction=instruction)
    if re.search(r"\bmute\b(?! my microphone)", lowered):
        return _mk(Operation.MUTE, RiskLevel.SENSITIVE, raw_instruction=instruction)
    if re.search(r"\bunmute\b", lowered):
        return _mk(Operation.UNMUTE, RiskLevel.SENSITIVE, raw_instruction=instruction)
    if re.search(r"\b(?:volume up|turn (?:the )?volume up|increase (?:the )?volume)\b", lowered):
        return _mk(Operation.VOLUME_UP, RiskLevel.SENSITIVE, raw_instruction=instruction)
    if re.search(r"\b(?:volume down|turn (?:the )?volume down|decrease (?:the )?volume|lower (?:the )?volume)\b", lowered):
        return _mk(Operation.VOLUME_DOWN, RiskLevel.SENSITIVE, raw_instruction=instruction)

    set_bri_match = re.search(r"\bset (?:the |my )?brightness to (\d{1,3})%?", lowered)
    if set_bri_match:
        return _mk(Operation.SET_BRIGHTNESS, RiskLevel.SENSITIVE,
                    numeric_arg=int(set_bri_match.group(1)), raw_instruction=instruction)
    if re.search(r"\b(?:brightness up|increase (?:the )?brightness|brighten (?:the )?screen)\b", lowered):
        return _mk(Operation.BRIGHTNESS_UP, RiskLevel.SENSITIVE, raw_instruction=instruction)
    if re.search(r"\b(?:brightness down|decrease (?:the )?brightness|dim (?:the )?screen)\b", lowered):
        return _mk(Operation.BRIGHTNESS_DOWN, RiskLevel.SENSITIVE, raw_instruction=instruction)

    launch_match = re.search(
        r"^launch (?:the )?(?:app(?:lication)? )?(.+)$|"
        r"^start (?:the )?app(?:lication)? (.+)$",
        text, re.I,
    )
    if launch_match:
        name = (launch_match.group(1) or launch_match.group(2)).strip()
        if name:
            return _mk(Operation.LAUNCH_APP, RiskLevel.SENSITIVE, text_arg=name, raw_instruction=instruction)

    close_match = re.search(
        r"^close (?:the )?app(?:lication)? (.+)$|"
        r"^quit (.+)$|"
        r"^force quit (.+)$",
        text, re.I,
    )
    if close_match:
        name = next(g for g in close_match.groups() if g)
        name = name.strip()
        if name:
            return _mk(Operation.CLOSE_APP, RiskLevel.DESTRUCTIVE, text_arg=name, raw_instruction=instruction)

    if re.search(r"\bshut ?down (?:my |the )?(?:computer|pc|system)?\b", lowered):
        return _mk(Operation.SHUTDOWN, RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if re.search(r"\brestart (?:my |the )?(?:computer|pc|system)\b", lowered):
        return _mk(Operation.RESTART, RiskLevel.DESTRUCTIVE, raw_instruction=instruction)
    if re.search(r"\b(?:put (?:my |the )?computer to sleep|sleep (?:my |the )?computer)\b", lowered):
        return _mk(Operation.SLEEP, RiskLevel.DESTRUCTIVE, raw_instruction=instruction)

    return _mk(Operation.UNKNOWN, RiskLevel.SAFE, raw_instruction=instruction)


def looks_like_computer_control_request(instruction: str) -> bool:
    return classify(instruction).operation != Operation.UNKNOWN
