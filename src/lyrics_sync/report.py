"""Reporting utilities for CLI output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .alignment import AlignmentResult


@dataclass
class AlignmentReport:
    total_lines: int
    matched_lines: int
    fallback_lines: int
    reconstructed_words: bool
    warnings: List[str]

    @property
    def matched_ratio(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.matched_lines / self.total_lines

    def format_summary(self) -> str:
        parts = [
            f"Рядків загалом: {self.total_lines}",
            f"Автоматично вирівняно: {self.matched_lines} ({self.matched_ratio * 100:.1f}%)",
            f"Евристично виставлено: {self.fallback_lines}",
            "Використано реконструкцію слів: " + ("так" if self.reconstructed_words else "ні"),
        ]
        if self.warnings:
            parts.append("Попередження:")
            parts.extend(f" • {warn}" for warn in self.warnings)
        return "\n".join(parts)

    @classmethod
    def from_alignment(cls, result: AlignmentResult) -> "AlignmentReport":
        return cls(
            total_lines=len(result.lines),
            matched_lines=result.matched_count,
            fallback_lines=result.fallback_count,
            reconstructed_words=result.reconstructed_words,
            warnings=result.warnings,
        )
