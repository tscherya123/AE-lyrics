"""Lyrics alignment logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Sequence

from .audio_transcriber import WordTiming
from .text_normalizer import join_tokens, tokenize


@dataclass
class AlignedLine:
    index: int
    text: str
    start: float
    end: float
    matched: bool
    score: float | None = None


@dataclass
class AlignmentResult:
    lines: List[AlignedLine]
    matched_count: int
    fallback_count: int
    reconstructed_words: bool
    warnings: List[str] = field(default_factory=list)


@dataclass
class _WindowMatch:
    start_index: int
    end_index: int
    score: float
    start_time: float
    end_time: float


class LyricsAligner:
    """Align lyric lines with recognised words."""

    def __init__(
        self,
        min_gap: float = 0.12,
        min_duration: float = 0.6,
        search_window_words: int = 40,
        min_score: float = 0.55,
    ) -> None:
        self.min_gap = min_gap
        self.min_duration = min_duration
        self.search_window_words = search_window_words
        self.min_score = min_score

    def align(self, lines: Sequence[str], words: Sequence[WordTiming], reconstructed_words: bool) -> AlignmentResult:
        cleaned_lines = [line for line in (line.strip() for line in lines) if line]
        aligned: List[AlignedLine] = []
        pointer = 0
        matched = 0
        fallback = 0
        warnings: List[str] = []

        prev_end = 0.0
        last_word_end = words[-1].end if words else 0.0

        for idx, line in enumerate(cleaned_lines, start=1):
            tokens = tokenize(line)
            window = self._find_best_window(tokens, words, pointer, prev_end)

            if window and window.score >= self.min_score:
                start_time = window.start_time
                end_time = max(window.end_time, start_time + self.min_duration)
                pointer = min(window.end_index + 1, len(words))
                matched += 1
                score = window.score
            else:
                start_time = max(prev_end + self.min_gap, words[pointer].start if pointer < len(words) else prev_end)
                if start_time > last_word_end:
                    start_time = max(prev_end + self.min_gap, last_word_end + self.min_gap)
                end_time = start_time + self.min_duration
                score = None
                pointer = self._advance_pointer_after_fallback(words, pointer, end_time)
                fallback += 1

            if aligned:
                prev = aligned[-1]
                if start_time <= prev.end:
                    cutoff = start_time - self.min_gap
                    new_end = min(prev.end, cutoff)
                    min_allowed = prev.start + self.min_duration
                    if new_end < min_allowed:
                        new_end = min_allowed
                        warnings.append(
                            f"Рядок {prev.index} не вдалося скоротити без порушення мінімальної тривалості"
                        )
                    prev.end = max(prev.start + 0.01, new_end)

            aligned.append(AlignedLine(index=idx, text=line, start=start_time, end=end_time, matched=score is not None, score=score))
            prev_end = end_time

        if fallback / max(len(cleaned_lines), 1) > 0.3:
            warnings.append("Забагато рядків розставлено евристично")

        return AlignmentResult(
            lines=aligned,
            matched_count=matched,
            fallback_count=fallback,
            reconstructed_words=reconstructed_words,
            warnings=warnings,
        )

    def _find_best_window(
        self,
        tokens: Sequence[str],
        words: Sequence[WordTiming],
        pointer: int,
        prev_end: float,
    ) -> _WindowMatch | None:
        if not tokens or pointer >= len(words):
            return None

        best: _WindowMatch | None = None
        max_window = min(len(words), pointer + self.search_window_words)
        token_string = join_tokens(tokens)

        for start_index in range(pointer, max_window):
            start_word = words[start_index]
            if start_word.end < prev_end - 0.5:
                continue

            window_tokens: List[str] = []
            last_end_time = start_word.end
            for end_index in range(start_index, max_window):
                word = words[end_index]
                last_end_time = word.end
                normalised = word.normalised
                if not normalised:
                    continue
                window_tokens.append(normalised)
                if not window_tokens:
                    continue
                if len(window_tokens) > len(tokens) + 6:
                    break
                score = self._score_window(token_string, tokens, window_tokens)
                score -= self._time_gap_penalty(start_word.start, prev_end)
                if best is None or score > best.score:
                    best = _WindowMatch(
                        start_index=start_index,
                        end_index=end_index,
                        score=score,
                        start_time=start_word.start,
                        end_time=last_end_time,
                    )
            if best and best.score > 0.95:
                break
        return best

    def _score_window(
        self,
        token_string: str,
        target_tokens: Sequence[str],
        window_tokens: Sequence[str],
    ) -> float:
        window_string = join_tokens(window_tokens)
        if not window_string:
            return 0.0
        similarity = SequenceMatcher(None, token_string, window_string).ratio()
        target_set = set(target_tokens)
        window_set = set(window_tokens)
        overlap = len(target_set & window_set) / max(len(target_set), 1)
        length_penalty = abs(len(window_tokens) - len(target_tokens)) / max(len(target_tokens), 1)
        return 0.75 * similarity + 0.25 * overlap - 0.1 * length_penalty

    def _time_gap_penalty(self, start_time: float, prev_end: float) -> float:
        if prev_end <= 0:
            return 0.0
        gap = max(0.0, start_time - prev_end)
        if gap <= 3.0:
            return gap * 0.01
        return 0.03 + (gap - 3.0) * 0.02

    def _advance_pointer_after_fallback(self, words: Sequence[WordTiming], pointer: int, end_time: float) -> int:
        while pointer < len(words) and words[pointer].end <= end_time:
            pointer += 1
        return pointer
