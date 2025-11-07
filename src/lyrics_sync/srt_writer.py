"""SRT export helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

from .alignment import AlignmentResult, AlignedLine


class SRTWriter:
    """Write SRT files from alignment results."""

    def __init__(self, min_duration: float = 0.6) -> None:
        self.min_duration = min_duration

    def write(self, result: AlignmentResult, output_path: str | Path) -> None:
        path = Path(output_path)
        payload = self._build_payload(result.lines)
        _atomic_write(path, payload)

    def _build_payload(self, lines: Iterable[AlignedLine]) -> str:
        parts = []
        for index, line in enumerate(lines, start=1):
            start = line.start
            end = max(line.end, start + self.min_duration)
            parts.append(f"{index}\n{_format_timestamp(start)} --> {_format_timestamp(end)}\n{line.text}\n")
        return "\n".join(parts).strip() + "\n"


def _format_timestamp(value: float) -> str:
    if value < 0:
        value = 0.0
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = int(value % 60)
    milliseconds = int(round((value - int(value)) * 1000))
    if milliseconds >= 1000:
        milliseconds -= 1000
        seconds += 1
        if seconds >= 60:
            seconds -= 60
            minutes += 1
            if minutes >= 60:
                minutes -= 60
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        encoding="utf-8",
        newline="\n",
        dir=str(path.parent),
    ) as handle:
        handle.write(payload)
        temp_name = handle.name
    os.replace(temp_name, path)
