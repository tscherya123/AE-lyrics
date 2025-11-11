"""Command-line interface for AE Lyrics Sync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from .alignment import LyricsAligner
from .audio_transcriber import AudioTranscriber
from .report import AlignmentReport
from .srt_writer import SRTWriter


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Автоматичне вирівнювання лірики та генерація SRT")
    parser.add_argument("audio", help="Шлях до аудіофайлу пісні")
    parser.add_argument("lyrics", help="Шлях до текстового файлу з лірикою")
    parser.add_argument("--output", "-o", help="Шлях для збереження SRT (за замовчуванням поряд з аудіо)")

    args = parser.parse_args(argv)

    try:
        lyrics_lines = _load_lyrics(args.lyrics)
    except FileNotFoundError:
        print("Помилка: файл лірики не знайдено.", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("Помилка: файл лірики повинен бути у кодуванні UTF-8.", file=sys.stderr)
        return 1

    if not lyrics_lines:
        print("Помилка: файл лірики порожній або містить лише порожні рядки.", file=sys.stderr)
        return 1

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print("Помилка: аудіофайл не знайдено.", file=sys.stderr)
        return 1

    try:
        print("[1/3] Розпізнавання аудіо…")
        transcriber = AudioTranscriber()
        transcription = transcriber.transcribe(audio_path)
    except FileNotFoundError:
        print("Помилка: не вдалося відкрити аудіофайл для читання.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - надаємо користувачу дружнє повідомлення
        print(f"Помилка розпізнавання: {exc}", file=sys.stderr)
        return 1

    if not transcription.words:
        print("Помилка: не вдалося розпізнати слова у файлі.", file=sys.stderr)
        return 1

    audio_duration = transcription.audio_duration
    duration_after_vad = transcription.duration_after_vad
    last_segment_end = (
        transcription.segments[-1].end if transcription.segments else 0.0
    )

    if audio_duration is not None:
        print(
            f"Тривалість аудіо за даними моделі: {audio_duration:.2f} с"
        )
    if duration_after_vad is not None and audio_duration is not None:
        removed = max(audio_duration - duration_after_vad, 0.0)
        if removed > 0.5:
            percent = removed / audio_duration * 100
            print(
                "Попередження: VAD-фільтр відкинув "
                f"близько {removed:.2f} с ({percent:.1f} % від аудіо)."
            )
    if (
        audio_duration is not None
        and audio_duration - last_segment_end > 1.0
    ):
        covered = last_segment_end / audio_duration * 100
        print(
            "Попередження: розпізнані сегменти покривають лише "
            f"~{covered:.1f} % тривалості аудіо."
        )

    print("Розпізнані сегменти:")
    for index, segment in enumerate(transcription.segments, start=1):
        print(
            f"  [{index:02d}] {segment.start:7.2f}–{segment.end:7.2f} с | "
            f"{segment.text or '(порожньо)'}"
        )

    print("Розпізнані слова:")
    for index, word in enumerate(transcription.words, start=1):
        confidence = (
            f" ({word.confidence:.2f})" if word.confidence is not None else ""
        )
        print(
            f"  [{index:03d}] {word.start:7.2f}–{word.end:7.2f} с | "
            f"{word.text}{confidence}"
        )

    print("[2/3] Вирівнювання рядків…")
    aligner = LyricsAligner()
    result = aligner.align(
        lyrics_lines,
        transcription.words,
        transcription.used_reconstruction,
    )

    print("[3/3] Експорт SRT…")
    output_path = Path(args.output) if args.output else audio_path.with_suffix(".srt")
    writer = SRTWriter(min_duration=aligner.min_duration)
    try:
        writer.write(result, output_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Помилка збереження SRT: {exc}", file=sys.stderr)
        return 1

    report = AlignmentReport.from_alignment(result)
    print(report.format_summary())
    print(f"SRT збережено до: {output_path}")
    return 0


def _load_lyrics(path: str | Path) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
