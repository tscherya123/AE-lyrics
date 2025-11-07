from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import threading
import unicodedata
import wave
import tkinter.messagebox as messagebox
from dataclasses import dataclass
from tkinter import filedialog
from typing import Iterable, Sequence

import customtkinter as ctk


DEFAULT_DURATION_SECONDS = 3
MIN_BLOCK_DURATION = 1.5
MAX_BLOCK_DURATION = 8.0
WORDS_PER_SECOND = 3.0
CHARS_PER_SECOND = 14.0
PAUSE_BETWEEN_BLOCKS = 0.35
MIN_GAP_BETWEEN_LINES = 0.12

try:  # pragma: no cover - optional dependency
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - gracefully degrade when not installed
    MutagenFile = None


@dataclass(slots=True)
class SubtitleBlock:
    index: int
    start: float
    end: float
    lines: list[str]

    def to_srt(self) -> list[str]:
        return [
            str(self.index),
            f"{format_timestamp(self.start)} --> {format_timestamp(self.end)}",
            *self.lines,
            "",
        ]


class SRTBuilder:
    def __init__(
        self,
        lyrics_text: str,
        audio_duration: float | None = None,
        word_timings: Sequence["WordTiming"] | None = None,
    ) -> None:
        self._lyrics_text = lyrics_text
        self._audio_duration = audio_duration
        self._word_timings = list(word_timings) if word_timings else None

    def build(self) -> str:
        blocks = self._split_into_blocks(self._lyrics_text)
        if not blocks:
            return ""

        timings = None
        if self._word_timings:
            timings = self._timings_from_word_alignment(blocks, self._word_timings)

        if not timings:
            durations = [self._estimate_block_duration(block) for block in blocks]
            timings = self._timings_for_blocks(durations)

        subtitles: list[str] = []
        for index, (block, (start, end)) in enumerate(zip(blocks, timings), start=1):
            subtitles.extend(SubtitleBlock(index=index, start=start, end=end, lines=block).to_srt())
        return "\n".join(subtitles).strip()

    def _timings_from_word_alignment(
        self, blocks: list[list[str]], word_timings: Sequence["WordTiming"]
    ) -> list[tuple[float, float]] | None:
        line_infos: list[tuple[int, int, str]] = []
        for block_index, block in enumerate(blocks):
            for line_index, line in enumerate(block):
                if line.strip():
                    line_infos.append((block_index, line_index, line))

        if not line_infos:
            return None

        lyric_tokens: list[tuple[int, int, int, str]] = []
        for line_global_index, (block_index, line_index, line) in enumerate(line_infos):
            for token_index, token in enumerate(self._tokenize_line(line)):
                normalized = self._normalize_token(token)
                if normalized:
                    lyric_tokens.append((line_global_index, block_index, token_index, normalized))

        recognized_tokens: list[tuple[int, str]] = []
        for idx, item in enumerate(word_timings):
            normalized = self._normalize_token(item.word)
            if normalized:
                recognized_tokens.append((idx, normalized))

        if not lyric_tokens or not recognized_tokens:
            return None

        lyric_norms = [token[-1] for token in lyric_tokens]
        recognized_norms = [token[1] for token in recognized_tokens]

        alignment = self._align_tokens(recognized_norms, lyric_norms)
        if not alignment:
            return None

        line_ranges: list[tuple[float | None, float | None]] = [(None, None) for _ in line_infos]
        for lyric_index, recognized_index in alignment.items():
            if 0 <= lyric_index < len(lyric_tokens) and 0 <= recognized_index < len(recognized_tokens):
                line_index = lyric_tokens[lyric_index][0]
                word_index = recognized_tokens[recognized_index][0]
                timing = word_timings[word_index]
                start, end = line_ranges[line_index]
                if start is None or timing.start < start:
                    start = timing.start
                if end is None or timing.end > end:
                    end = timing.end
                line_ranges[line_index] = (start, end)

        line_ranges = self._fill_missing_line_timings(line_ranges, line_infos)
        if not line_ranges:
            return None

        block_timings: list[tuple[float, float]] = []
        for block_index, block in enumerate(blocks):
            relevant_lines = [
                line_ranges[idx]
                for idx, (line_block, _, _) in enumerate(line_infos)
                if line_block == block_index
            ]
            if not relevant_lines:
                return None
            starts = [start for start, _ in relevant_lines if start is not None]
            ends = [end for _, end in relevant_lines if end is not None]
            if not starts or not ends:
                return None
            block_timings.append((min(starts), max(ends)))

        return self._ensure_monotonic(block_timings)

    def _fill_missing_line_timings(
        self,
        line_ranges: list[tuple[float | None, float | None]],
        line_infos: list[tuple[int, int, str]],
    ) -> list[tuple[float, float]] | None:
        filled: list[tuple[float, float]] = []
        last_end = 0.0
        total_lines = len(line_ranges)

        for index, ((start, end), (_, _, line_text)) in enumerate(zip(line_ranges, line_infos)):
            if start is None or end is None:
                duration = self._estimate_block_duration([line_text])
                start = max(last_end + MIN_GAP_BETWEEN_LINES, 0.0)
                end = start + duration

                for future_index in range(index + 1, total_lines):
                    next_start, next_end = line_ranges[future_index]
                    if next_start is not None and next_end is not None:
                        available = next_start - MIN_GAP_BETWEEN_LINES - start
                        if available > 0:
                            end = min(start + duration, start + available)
                        break
            else:
                start = max(start, last_end + MIN_GAP_BETWEEN_LINES)

            if end <= start:
                end = start + max(0.2, MIN_GAP_BETWEEN_LINES)

            if self._audio_duration:
                end = min(end, self._audio_duration)
                start = min(start, end)

            filled.append((start, end))
            last_end = end

        return filled

    def _ensure_monotonic(self, timings: list[tuple[float, float]]) -> list[tuple[float, float]]:
        adjusted: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in timings:
            start = max(start, cursor)
            if end <= start:
                end = start + max(0.2, MIN_GAP_BETWEEN_LINES)
            if self._audio_duration:
                end = min(end, self._audio_duration)
                start = min(start, end)
            adjusted.append((start, end))
            cursor = end + PAUSE_BETWEEN_BLOCKS
        return adjusted

    @staticmethod
    def _align_tokens(recognized: Sequence[str], lyrics: Sequence[str]) -> dict[int, int]:
        try:
            import difflib
        except ImportError:  # pragma: no cover - should always be available
            return {}

        matcher = difflib.SequenceMatcher(a=recognized, b=lyrics, autojunk=False)
        alignment: dict[int, int] = {}
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in {"equal", "replace"}:
                length = min(i2 - i1, j2 - j1)
                for offset in range(length):
                    alignment[j1 + offset] = i1 + offset
        return alignment

    @staticmethod
    def _tokenize_line(line: str) -> list[str]:
        return re.findall(r"[\w’']+", line, flags=re.UNICODE)

    @staticmethod
    def _normalize_token(token: str) -> str:
        normalized_chars: list[str] = []
        for char in token.lower():
            category = unicodedata.category(char)
            if category.startswith(("L", "N")):
                normalized_chars.append(char)
        return "".join(normalized_chars)

    def _split_into_blocks(self, lyrics_text: str) -> list[list[str]]:
        blocks: list[list[str]] = []
        current_block: list[str] = []
        for raw_line in lyrics_text.splitlines():
            line = raw_line.strip()
            if not line:
                if current_block:
                    blocks.append(current_block)
                    current_block = []
                continue
            current_block.append(line)
        if current_block:
            blocks.append(current_block)
        return blocks

    def _estimate_block_duration(self, block: Iterable[str]) -> float:
        word_count = sum(
            max(1, len(re.findall(r"[\w’']+", line, flags=re.UNICODE))) for line in block
        )
        char_count = sum(len(line) for line in block)

        word_based = word_count / WORDS_PER_SECOND
        char_based = char_count / CHARS_PER_SECOND
        base_duration = max(word_based, char_based)
        duration = max(base_duration, MIN_BLOCK_DURATION)
        if duration < DEFAULT_DURATION_SECONDS:
            duration = DEFAULT_DURATION_SECONDS
        return min(duration, MAX_BLOCK_DURATION)

    def _timings_for_blocks(self, durations: list[float]) -> list[tuple[float, float]]:
        if not durations:
            return []

        pause = PAUSE_BETWEEN_BLOCKS if len(durations) > 1 else 0.0
        total_duration = sum(durations)
        total_pause = pause * (len(durations) - 1)

        if self._audio_duration and self._audio_duration > total_pause + 0.1:
            available = max(self._audio_duration - total_pause, 0.1)
            scale = available / total_duration if total_duration else 1.0
            durations = [duration * scale for duration in durations]

        timings: list[tuple[float, float]] = []
        current = 0.0
        for duration in durations:
            start = current
            end = start + duration
            timings.append((start, end))
            current = end + pause
        return timings


def format_timestamp(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    total_seconds, milliseconds = divmod(total_milliseconds, 1000)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


@dataclass(slots=True)
class WordTiming:
    word: str
    start: float
    end: float


class SRTGeneratorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AE Lyrics SRT Generator")
        self.geometry("900x700")
        self.minsize(800, 600)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.selected_file: pathlib.Path | None = None
        self.audio_duration: float | None = None
        self._word_timings_cache: dict[pathlib.Path, list[WordTiming]] = {}
        self.srt_result: str = ""
        self._transcription_in_progress = False

        self._create_widgets()
        self._layout_widgets()
        self._update_controls_state()

    def _create_widgets(self) -> None:
        self.file_frame = ctk.CTkFrame(self)
        self.file_label = ctk.CTkLabel(
            self.file_frame,
            text="Файл пісні не вибрано",
            anchor="w",
            wraplength=450,
        )
        self.browse_button = ctk.CTkButton(
            self.file_frame,
            text="Обрати файл",
            command=self._on_browse_clicked,
            width=150,
        )

        self.generate_checkbox_var = ctk.BooleanVar(value=False)
        self.generate_checkbox = ctk.CTkCheckBox(
            self,
            text="Згенерувати текст пісні автоматично",
            variable=self.generate_checkbox_var,
            command=self._on_generate_checkbox_changed,
        )

        self.lyrics_label = ctk.CTkLabel(self, text="Текст пісні")
        self.lyrics_textbox = ctk.CTkTextbox(self, wrap="word", height=200)
        self.lyrics_textbox.bind("<<Modified>>", self._on_lyrics_modified)

        self.generate_button = ctk.CTkButton(
            self,
            text="Згенерувати SRT",
            command=self._on_generate_srt_clicked,
        )

        self.preview_label = ctk.CTkLabel(self, text="Попередній перегляд SRT")
        self.preview_textbox = ctk.CTkTextbox(self, wrap="word", height=200)
        self.preview_textbox.configure(state="disabled")

        self.actions_frame = ctk.CTkFrame(self)
        self.copy_button = ctk.CTkButton(
            self.actions_frame,
            text="Скопіювати",
            command=self._on_copy_clicked,
        )
        self.export_button = ctk.CTkButton(
            self.actions_frame,
            text="Експортувати...",
            command=self._on_export_clicked,
        )

    def _layout_widgets(self) -> None:
        self.file_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        self.file_frame.columnconfigure(0, weight=1)
        self.file_label.grid(row=0, column=0, padx=(10, 20), pady=10, sticky="ew")
        self.browse_button.grid(row=0, column=1, padx=(0, 10), pady=10)

        self.generate_checkbox.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")
        self.lyrics_label.grid(row=2, column=0, padx=20, sticky="w")
        self.lyrics_textbox.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="nsew")

        self.generate_button.grid(row=4, column=0, padx=20, pady=10, sticky="ew")

        self.preview_label.grid(row=5, column=0, padx=20, sticky="w")
        self.preview_textbox.grid(row=6, column=0, padx=20, pady=(0, 10), sticky="nsew")

        self.actions_frame.grid(row=7, column=0, padx=20, pady=(0, 20), sticky="e")
        self.copy_button.grid(row=0, column=0, padx=(0, 10))
        self.export_button.grid(row=0, column=1)

        self.grid_rowconfigure(3, weight=1)
        self.grid_rowconfigure(6, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _update_controls_state(self) -> None:
        if self.generate_checkbox_var.get():
            self.lyrics_textbox.configure(state="disabled")
        else:
            self.lyrics_textbox.configure(state="normal")

        has_text = bool(self._get_lyrics_text().strip()) and not self._transcription_in_progress
        can_generate = has_text
        self.generate_button.configure(state="normal" if can_generate else "disabled")

        has_result = bool(self.srt_result.strip())
        button_state = "normal" if has_result else "disabled"
        self.copy_button.configure(state=button_state)
        self.export_button.configure(state=button_state)

    def _on_browse_clicked(self) -> None:
        filetypes = [
            ("Аудіо або текст", "*.mp3 *.wav *.flac *.ogg *.m4a *.txt"),
            ("Усі файли", "*.*"),
        ]
        selected = filedialog.askopenfilename(title="Оберіть файл пісні", filetypes=filetypes)
        if not selected:
            return
        self.selected_file = pathlib.Path(selected)
        self.file_label.configure(text=f"Вибрано: {self.selected_file.name}")
        self._word_timings_cache.clear()
        self.audio_duration = self._detect_audio_duration(self.selected_file)
        if self.generate_checkbox_var.get():
            self._generate_lyrics_from_file()
        self._update_controls_state()

    def _on_lyrics_modified(self, event) -> None:
        if self.lyrics_textbox.edit_modified():
            self.lyrics_textbox.edit_modified(False)
            self._update_controls_state()

    def _on_generate_checkbox_changed(self) -> None:
        if self.generate_checkbox_var.get():
            self._generate_lyrics_from_file()
        self._update_controls_state()

    def _generate_lyrics_from_file(self) -> None:
        if not self.selected_file:
            messagebox.showinfo(
                "Немає файлу",
                "Спочатку оберіть файл пісні, щоб спробувати згенерувати текст.",
            )
            self.generate_checkbox_var.set(False)
            return
        suffix = self.selected_file.suffix.lower()
        if suffix in {".txt", ".lrc"}:
            self._load_text_file(self.selected_file)
        else:
            self._transcribe_audio_file(self.selected_file)

    def _get_lyrics_text(self) -> str:
        if self.generate_checkbox_var.get():
            self.lyrics_textbox.configure(state="normal")
            text = self.lyrics_textbox.get("1.0", "end")
            self.lyrics_textbox.configure(state="disabled")
            return text
        return self.lyrics_textbox.get("1.0", "end")

    def _on_generate_srt_clicked(self) -> None:
        lyrics_text = self._get_lyrics_text().strip()
        if not lyrics_text:
            messagebox.showinfo("Немає тексту", "Введіть текст пісні перед генерацією SRT.")
            return
        word_timings = self._get_word_timings()
        builder = SRTBuilder(
            lyrics_text,
            audio_duration=self.audio_duration,
            word_timings=word_timings,
        )
        self.srt_result = builder.build()
        if not self.srt_result:
            messagebox.showinfo("Немає тексту", "Не вдалося побудувати субтитри для порожнього тексту.")
            return
        self.preview_textbox.configure(state="normal")
        self.preview_textbox.delete("1.0", "end")
        self.preview_textbox.insert("1.0", self.srt_result)
        self.preview_textbox.configure(state="disabled")
        self._update_controls_state()

    def _on_copy_clicked(self) -> None:
        if not self.srt_result:
            return
        self.clipboard_clear()
        self.clipboard_append(self.srt_result)
        self.update()
        messagebox.showinfo("Скопійовано", "SRT скопійовано в буфер обміну.")

    def _on_export_clicked(self) -> None:
        if not self.srt_result:
            return
        default_name = "lyrics.srt"
        if self.selected_file:
            default_name = self.selected_file.with_suffix(".srt").name
        destination = filedialog.asksaveasfilename(
            title="Збереження SRT",
            defaultextension=".srt",
            initialfile=default_name,
            filetypes=[("SubRip", "*.srt"), ("Усі файли", "*.*")],
        )
        if not destination:
            return
        try:
            pathlib.Path(destination).write_text(self.srt_result, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Не вдалося зберегти", str(exc))
        else:
            messagebox.showinfo("Успішно", "Файл SRT збережено.")

    def _load_text_file(self, path: pathlib.Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            messagebox.showerror(
                "Помилка читання",
                "Не вдалося прочитати текст з файлу. Спробуйте інший файл або введіть текст вручну.",
            )
            self.generate_checkbox_var.set(False)
            return
        except OSError as exc:
            messagebox.showerror("Помилка доступу", str(exc))
            self.generate_checkbox_var.set(False)
            return

        self.lyrics_textbox.configure(state="normal")
        self.lyrics_textbox.delete("1.0", "end")
        self.lyrics_textbox.insert("1.0", text.strip())
        if self.generate_checkbox_var.get():
            self.lyrics_textbox.configure(state="disabled")
        self._update_controls_state()
        self._word_timings_cache.clear()

    def _transcribe_audio_file(self, path: pathlib.Path) -> None:
        self._transcription_in_progress = True
        self.lyrics_textbox.configure(state="normal")
        self.lyrics_textbox.delete("1.0", "end")
        self.lyrics_textbox.insert(
            "1.0",
            "Очікування результату... Це може зайняти деякий час залежно від тривалості треку.",
        )
        self.lyrics_textbox.configure(state="disabled")
        self._update_controls_state()

        def worker() -> None:
            try:
                text = self._offline_transcribe(path)
            except RuntimeError as exc:
                message = str(exc)
                self.after(0, self._handle_transcription_error, message)
                return
            self.after(0, self._handle_transcription_success, text)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_transcription_success(self, text: str) -> None:
        self._transcription_in_progress = False
        if not text.strip():
            messagebox.showinfo(
                "Розпізнавання не дало результату",
                "Не вдалося автоматично отримати текст пісні. Введіть його вручну.",
            )
            self.generate_checkbox_var.set(False)
            self.lyrics_textbox.configure(state="normal")
            self.lyrics_textbox.delete("1.0", "end")
            return
        self.lyrics_textbox.configure(state="normal")
        self.lyrics_textbox.delete("1.0", "end")
        self.lyrics_textbox.insert("1.0", text.strip())
        self.lyrics_textbox.configure(state="disabled")
        self._update_controls_state()

    def _handle_transcription_error(self, message: str) -> None:
        self._transcription_in_progress = False
        messagebox.showerror("Автоматичне розпізнавання недоступне", message)
        self.generate_checkbox_var.set(False)
        self.lyrics_textbox.configure(state="normal")
        self.lyrics_textbox.delete("1.0", "end")
        self._update_controls_state()

    def _offline_transcribe(self, path: pathlib.Path) -> str:
        try:
            import speech_recognition as sr
        except ImportError as exc:  # pragma: no cover - optional feature
            raise RuntimeError(
                "Пакет speech_recognition не встановлено. Встановіть його та повторіть спробу."
            ) from exc

        recognizer = sr.Recognizer()
        temp_path: pathlib.Path | None = None
        try:
            try:
                with sr.AudioFile(str(path)) as source:
                    audio_data = recognizer.record(source)
            except ValueError:
                temp_path = self._convert_audio_to_wav(path)
                with sr.AudioFile(str(temp_path)) as source:
                    audio_data = recognizer.record(source)
        except Exception as exc:  # pragma: no cover - delegated to recognizer
            raise RuntimeError("Не вдалося обробити вибраний аудіофайл.") from exc
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        if not hasattr(recognizer, "recognize_vosk"):
            raise RuntimeError(
                "Поточна версія speech_recognition не підтримує метод recognize_vosk. "
                "Оновіть пакет або скористайтеся ручним введенням тексту."
            )

        try:
            return recognizer.recognize_vosk(audio_data)
        except sr.RequestError:
            raise RuntimeError(
                "Для офлайн-розпізнавання потрібна установка vosk-моделі. "
                "Переконайтеся, що встановлено пакет vosk і завантажено модель."
            )
        except sr.UnknownValueError:
            return ""

    def _convert_audio_to_wav(self, path: pathlib.Path) -> pathlib.Path:
        try:
            from pydub import AudioSegment
        except ImportError as exc:  # pragma: no cover - optional feature
            raise RuntimeError(
                "Формат файлу не підтримується без пакетів pydub та ffmpeg. "
                "Встановіть їх або надайте файл у форматі WAV/FLAC."
            ) from exc

        try:
            audio = AudioSegment.from_file(path)
        except Exception as exc:  # pragma: no cover - delegated to pydub
            raise RuntimeError("Не вдалося прочитати аудіофайл для конвертації.") from exc

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = pathlib.Path(tmp.name)
        try:
            audio.export(temp_path, format="wav")
        except Exception as exc:  # pragma: no cover - delegated to pydub/ffmpeg
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("Не вдалося конвертувати аудіофайл у формат WAV.") from exc
        return temp_path

    def _detect_audio_duration(self, path: pathlib.Path) -> float | None:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".lrc", ".srt"}:
            return None
        if suffix == ".wav":
            try:
                with wave.open(str(path), "rb") as wav_file:
                    frames = wav_file.getnframes()
                    rate = wav_file.getframerate()
                    if rate:
                        return frames / float(rate)
            except (wave.Error, OSError):
                return None
        if MutagenFile is not None:
            try:
                metadata = MutagenFile(str(path))
            except Exception:  # pragma: no cover - metadata parsing errors
                metadata = None
            if metadata and getattr(metadata, "info", None):
                length = getattr(metadata.info, "length", None)
                if length:
                    return float(length)
        return None

    def _get_word_timings(self) -> list[WordTiming] | None:
        if not self.selected_file or not self.audio_duration:
            return None

        cached = self._word_timings_cache.get(self.selected_file)
        if cached is not None:
            return cached

        try:
            timings = self._extract_word_timings(self.selected_file)
        except RuntimeError as exc:
            messagebox.showwarning("Точна синхронізація недоступна", str(exc))
            return None
        else:
            self._word_timings_cache[self.selected_file] = timings
            return timings

    def _extract_word_timings(self, path: pathlib.Path) -> list[WordTiming]:
        try:
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:  # pragma: no cover - optional feature
            raise RuntimeError(
                "Пакет vosk не встановлено. Встановіть його та вкажіть шлях до моделі "
                "у змінній середовища VOSK_MODEL_PATH, щоб синхронізувати текст."
            ) from exc

        model_path = os.environ.get("VOSK_MODEL_PATH")
        if not model_path:
            raise RuntimeError(
                "Не вказано шлях до vosk-моделі. Задайте змінну середовища VOSK_MODEL_PATH "
                "на каталог із попередньо завантаженою моделлю."
            )

        model_dir = pathlib.Path(model_path).expanduser()
        if not model_dir.exists():
            raise RuntimeError(f"Каталог з vosk-моделлю не знайдено: {model_dir}")

        prepared_audio, temp_created = self._prepare_audio_for_vosk(path)

        try:
            model = Model(str(model_dir))
        except Exception as exc:  # pragma: no cover - delegated to vosk
            if temp_created:
                prepared_audio.unlink(missing_ok=True)
            raise RuntimeError("Не вдалося завантажити vosk-модель.") from exc

        recognizer = KaldiRecognizer(model, 16000)
        recognizer.SetWords(True)

        word_timings: list[WordTiming] = []
        try:
            with wave.open(str(prepared_audio), "rb") as wav_file:
                if wav_file.getframerate() != 16000 or wav_file.getnchannels() != 1:
                    raise RuntimeError(
                        "Файл не вдалося привести до формату PCM 16кГц mono, необхідного для vosk."
                    )

                while True:
                    data = wav_file.readframes(4000)
                    if not data:
                        break
                    if recognizer.AcceptWaveform(data):
                        word_timings.extend(self._collect_vosk_words(recognizer.Result()))
                word_timings.extend(self._collect_vosk_words(recognizer.FinalResult()))
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - delegated to vosk/wave
            raise RuntimeError("Не вдалося обробити аудіо для синхронізації.") from exc
        finally:
            if temp_created:
                prepared_audio.unlink(missing_ok=True)

        if not word_timings:
            raise RuntimeError("Не вдалося отримати слова з vosk-розпізнавання.")

        return word_timings

    @staticmethod
    def _collect_vosk_words(result_json: str) -> list[WordTiming]:
        try:
            payload = json.loads(result_json)
        except json.JSONDecodeError:
            return []

        words_data = payload.get("result")
        if not isinstance(words_data, list):
            return []

        collected: list[WordTiming] = []
        for item in words_data:
            word = str(item.get("word", ""))
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            collected.append(WordTiming(word=word, start=start, end=end))
        return collected

    def _prepare_audio_for_vosk(self, path: pathlib.Path) -> tuple[pathlib.Path, bool]:
        try:
            with wave.open(str(path), "rb") as wav_file:
                if (
                    wav_file.getframerate() == 16000
                    and wav_file.getnchannels() == 1
                    and wav_file.getsampwidth() == 2
                ):
                    return path, False
        except (wave.Error, OSError):
            pass

        try:
            from pydub import AudioSegment
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Для приведення файлу до формату PCM 16кГц mono необхідні пакети pydub та ffmpeg."
            ) from exc

        try:
            audio = AudioSegment.from_file(path)
        except Exception as exc:  # pragma: no cover - delegated to pydub
            raise RuntimeError("Не вдалося прочитати аудіофайл для синхронізації.") from exc

        audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = pathlib.Path(tmp.name)
        try:
            audio.export(temp_path, format="wav")
        except Exception as exc:  # pragma: no cover - delegated to pydub/ffmpeg
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("Не вдалося конвертувати аудіо до формату WAV для vosk.") from exc

        return temp_path, True


def main() -> None:
    app = SRTGeneratorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
