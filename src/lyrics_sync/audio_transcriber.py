"""Speech recognition and word preparation utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from faster_whisper import WhisperModel

from .text_normalizer import normalize_text, tokenize


@dataclass
class WordTiming:
    """Representation of a recognised word and its timing."""

    text: str
    start: float
    end: float
    confidence: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def normalised(self) -> str:
        return normalize_text(self.text)


@dataclass
class SegmentTranscription:
    """Container for recognised segment with text and optional words."""

    text: str
    start: float
    end: float
    words: List[WordTiming]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class TranscriptionResult:
    """Structured container with transcription payload and metadata."""

    words: List[WordTiming]
    segments: List[SegmentTranscription]
    used_reconstruction: bool
    audio_duration: float | None
    duration_after_vad: float | None


class AudioTranscriber:
    """Transcribe audio and provide word timings suitable for alignment."""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str | None = None,
        compute_type: str | None = None,
        fallback_compute_types: Sequence[str] | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device or "auto"
        self.compute_type = compute_type or "float16"
        self.fallback_compute_types: tuple[str, ...] = tuple(
            fallback_compute_types or ("int8_float16", "int8")
        )
        self._model: WhisperModel | None = None

    def _ensure_model(self) -> WhisperModel:
        if self._model is None:
            compute_preferences = [self.compute_type]
            for fallback in self.fallback_compute_types:
                if fallback not in compute_preferences:
                    compute_preferences.append(fallback)

            last_error: Exception | None = None
            for compute_type in compute_preferences:
                try:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=compute_type,
                    )
                except Exception as exc:  # noqa: BLE001 - пропонуємо кращу сумісність
                    last_error = exc
                    self._model = None
                    continue
                else:
                    break

            if self._model is None and last_error is not None:
                raise last_error
        return self._model

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        """Transcribe ``audio_path`` and return structured results for alignment."""

        model = self._ensure_model()
        segments: List[SegmentTranscription] = []
        words: List[WordTiming] = []

        raw_segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            temperature=0.0,
        )

        for segment in raw_segments:
            seg_words = [
                WordTiming(text=word.word.strip(), start=word.start or segment.start, end=word.end or segment.end, confidence=word.probability)
                for word in segment.words
                if word.word.strip()
            ]
            seg = SegmentTranscription(
                text=segment.text.strip(),
                start=segment.start,
                end=segment.end,
                words=seg_words,
            )
            segments.append(seg)
            words.extend(seg_words)

        needs_reconstruction = self._words_need_reconstruction(words)

        if needs_reconstruction:
            words = self._reconstruct_words_from_segments(segments)

        return TranscriptionResult(
            words=words,
            segments=segments,
            used_reconstruction=needs_reconstruction,
            audio_duration=getattr(info, "duration", None),
            duration_after_vad=getattr(info, "duration_after_vad", None),
        )

    def _words_need_reconstruction(self, words: Sequence[WordTiming]) -> bool:
        tokens = [w.normalised for w in words if w.normalised]
        if len(tokens) < 4:
            return True
        counts = Counter(tokens)
        unique_ratio = len(counts) / len(tokens)
        if unique_ratio < 0.3:
            return True
        most_common = counts.most_common(1)[0][1]
        if most_common / len(tokens) > 0.55:
            return True
        avg_duration = sum(w.duration for w in words) / max(len(words), 1)
        if avg_duration < 0.05:
            return True
        return False

    def _reconstruct_words_from_segments(self, segments: Sequence[SegmentTranscription]) -> List[WordTiming]:
        reconstructed: List[WordTiming] = []
        for segment in segments:
            tokens = [token for token in tokenize(segment.text) if token]
            if not tokens:
                continue
            duration = max(segment.duration, 0.01)
            step = duration / len(tokens)
            for index, token in enumerate(tokens):
                start = segment.start + step * index
                end = min(segment.end, start + step)
                reconstructed.append(WordTiming(text=token, start=start, end=end, confidence=None))
        return reconstructed


def segment_words_to_tokens(words: Iterable[WordTiming]) -> List[str]:
    return [normalize_text(word.text) for word in words if normalize_text(word.text)]
