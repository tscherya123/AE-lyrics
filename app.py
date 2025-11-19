import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import sys
import os
import re
import subprocess
import shutil
from pathlib import Path
from datetime import timedelta
import stable_whisper
import whisper
import torch

# --- НАЛАШТУВАННЯ UI ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- ЛОГІКА ОБРОБКИ ---

def format_timestamp(seconds):
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def normalize_text(text):
    return re.sub(r'[\s\W_]+', '', text).lower()

class LyricsApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AI Lyrics Aligner (Whisper Large-v3)")
        self.geometry("1100x700")

        self.audio_path = None
        self.srt_result = ""

        # --- СІТКА ГОЛОВНОГО ВІКНА ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ================= ЛІВА КОЛОНКА (ТЕКСТ) =================
        self.left_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.left_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        self.lbl_lyrics = ctk.CTkLabel(self.left_frame, text="Текст пісні:", font=("Arial", 14, "bold"))
        self.lbl_lyrics.pack(anchor="w", pady=(0, 5))

        self.textbox_lyrics = ctk.CTkTextbox(self.left_frame, font=("Consolas", 12), undo=True)
        self.textbox_lyrics.pack(fill="both", expand=True)
        
        # АКТИВАЦІЯ УНІВЕРСАЛЬНИХ ГАРЯЧИХ КЛАВІШ
        self.enable_universal_hotkeys(self.textbox_lyrics)

        # ================= ПРАВА КОЛОНКА (КЕРУВАННЯ) =================
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        # 1. Вибір файлу
        self.file_frame = ctk.CTkFrame(self.right_frame)
        self.file_frame.pack(fill="x", pady=(0, 10))

        self.btn_select = ctk.CTkButton(self.file_frame, text="📂 Вибрати аудіо", command=self.select_file)
        self.btn_select.pack(side="left", padx=10, pady=10)

        self.lbl_filename = ctk.CTkLabel(self.file_frame, text="Файл не обрано", text_color="gray")
        self.lbl_filename.pack(side="left", padx=10, pady=10)

        # 2. Кнопка СТАРТ
        self.btn_start = ctk.CTkButton(self.right_frame, text="СТАРТ ОБРОБКИ", command=self.start_processing_thread, 
                                       fg_color="green", hover_color="darkgreen", height=50, font=("Arial", 16, "bold"))
        self.btn_start.pack(fill="x", pady=(0, 10))

        # 3. Лог
        self.lbl_log = ctk.CTkLabel(self.right_frame, text="Статус виконання:", font=("Arial", 12, "bold"))
        self.lbl_log.pack(anchor="w", pady=(0, 5))

        self.textbox_log = ctk.CTkTextbox(self.right_frame, font=("Consolas", 11), text_color="#00ff00", fg_color="black")
        self.textbox_log.pack(fill="both", expand=True, pady=(0, 10))

        # 4. Кнопки збереження
        self.action_frame = ctk.CTkFrame(self.right_frame)
        self.action_frame.pack(fill="x", side="bottom")

        self.btn_copy = ctk.CTkButton(self.action_frame, text="Копіювати SRT", command=self.copy_to_clipboard, state="disabled")
        self.btn_copy.pack(side="left", padx=5, pady=10, expand=True, fill="x")

        self.btn_save = ctk.CTkButton(self.action_frame, text="Зберегти файл...", command=self.save_file, state="disabled")
        self.btn_save.pack(side="right", padx=5, pady=10, expand=True, fill="x")

    def enable_universal_hotkeys(self, ctk_textbox):
        """
        Прив'язка гарячих клавіш через коди фізичних кнопок (Keycodes).
        Це працює незалежно від розкладки (UA/EN).
        """
        text_widget = ctk_textbox._textbox

        def on_control_key(event):
            # event.keycode містить цифровий код клавіші.
            # На Windows стандартні коди для літер збігаються з ASCII у верхньому регістрі.
            
            # 65 = A (Select All)
            # 67 = C (Copy)
            # 86 = V (Paste)
            # 88 = X (Cut)
            # 90 = Z (Undo)
            
            if event.keycode == 65: # Ctrl + A
                ctk_textbox.tag_add("sel", "1.0", "end")
                return "break"
            
            elif event.keycode == 67: # Ctrl + C
                text_widget.event_generate("<<Copy>>")
                return "break"
            
            elif event.keycode == 86: # Ctrl + V
                text_widget.event_generate("<<Paste>>")
                return "break"
            
            elif event.keycode == 88: # Ctrl + X
                text_widget.event_generate("<<Cut>>")
                return "break"
            
            elif event.keycode == 90: # Ctrl + Z
                text_widget.event_generate("<<Undo>>")
                return "break"

        # Прив'язуємось до події "Будь-яка кнопка при затиснутому Control"
        text_widget.bind("<Control-Key>", on_control_key)

    def select_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.flac *.m4a")])
        if file_path:
            self.audio_path = file_path
            self.lbl_filename.configure(text=os.path.basename(file_path), text_color="white")

    def log(self, message):
        self.textbox_log.configure(state="normal")
        self.textbox_log.insert("end", str(message) + "\n")
        self.textbox_log.see("end")
        self.textbox_log.configure(state="disabled")

    def start_processing_thread(self):
        if not self.audio_path:
            messagebox.showerror("Помилка", "Виберіть аудіофайл!")
            return
        
        lyrics_text = self.textbox_lyrics.get("1.0", "end").strip()
        if not lyrics_text:
            messagebox.showerror("Помилка", "Вставте текст пісні!")
            return

        self.btn_start.configure(state="disabled", text="Обробка... (Чекайте)")
        self.textbox_log.configure(state="normal")
        self.textbox_log.delete("1.0", "end")
        self.textbox_log.configure(state="disabled")

        threading.Thread(target=self.run_logic, args=(self.audio_path, lyrics_text), daemon=True).start()

    def run_logic(self, audio_path, lyrics_text):
        try:
            temp_lyrics_file = "temp_lyrics_ui.txt"
            with open(temp_lyrics_file, "w", encoding="utf-8") as f:
                f.write(lyrics_text)

            self.log("--- 🎧 Початок: Відділення вокалу (Demucs)... ---")
            
            track_name = Path(audio_path).stem
            command = ["demucs", "-n", "htdemucs", "--two-stems=vocals", "-d", "cpu", audio_path]
            
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            for line in process.stdout:
                self.log(line.strip())
            process.wait()

            vocal_path = os.path.join("separated", "htdemucs", track_name, "vocals.wav")
            
            if not os.path.exists(vocal_path):
                raise Exception("Demucs не створив файл вокалу.")
            
            self.log("✅ Вокал відділено.")

            self.log("--- 🧠 Завантаження моделі LARGE-V3... ---")
            model = stable_whisper.load_model('large-v3')

            self.log("--- 🌍 Визначення мови... ---")
            audio = whisper.load_audio(vocal_path)
            audio = whisper.pad_or_trim(audio)
            
            n_mels = model.dims.n_mels 
            mel = whisper.log_mel_spectrogram(audio, n_mels=n_mels).to(model.device)
            
            _, probs = model.detect_language(mel)
            lang = max(probs, key=probs.get)
            self.log(f"✅ Мова: {lang.upper()}")

            self.log("--- ⏳ Початок детального вирівнювання... ---")
            
            result = model.align(
                vocal_path, 
                lyrics_text, 
                language=lang,
                fast_mode=False,       
                suppress_silence=False, 
                regroup=False           
            )

            self.log("--- 📏 Smart Match: Формування SRT... ---")
            
            whisper_words = []
            for segment in result.segments:
                whisper_words.extend(segment.words)
            
            original_lines = [line.strip() for line in lyrics_text.split('\n') if line.strip()]

            srt_content = ""
            whisper_idx = 0
            max_whisper_idx = len(whisper_words)

            for i, line in enumerate(original_lines):
                target_clean = normalize_text(line)
                if not target_clean: continue

                current_collected_text = ""
                start_word_idx = whisper_idx
                
                while whisper_idx < max_whisper_idx:
                    w_obj = whisper_words[whisper_idx]
                    w_text_clean = normalize_text(w_obj.word)
                    current_collected_text += w_text_clean
                    whisper_idx += 1
                    if len(current_collected_text) >= len(target_clean):
                        break
                
                if start_word_idx < max_whisper_idx:
                    start_time = whisper_words[start_word_idx].start
                    end_word = whisper_words[min(whisper_idx - 1, max_whisper_idx - 1)]
                    end_time = end_word.end
                    
                    srt_content += f"{i + 1}\n"
                    srt_content += f"{format_timestamp(start_time)} --> {format_timestamp(end_time)}\n"
                    srt_content += f"{line}\n\n"
                else:
                    self.log(f"⚠️ УВАГА: Рядок не знайдено в аудіо: '{line}'")

            self.srt_result = srt_content
            self.log("🎉🎉🎉 ГОТОВО! 🎉🎉🎉")
            
            if os.path.exists("separated"):
                shutil.rmtree("separated")
            if os.path.exists(temp_lyrics_file):
                os.remove(temp_lyrics_file)

            self.btn_copy.configure(state="normal")
            self.btn_save.configure(state="normal")
            self.btn_start.configure(state="normal", text="СТАРТ ОБРОБКИ")

        except Exception as e:
            self.log(f"❌ ПОМИЛКА: {e}")
            self.btn_start.configure(state="normal", text="СТАРТ ОБРОБКИ")
            import traceback
            self.log(traceback.format_exc())

    def copy_to_clipboard(self):
        self.clipboard_clear()
        self.clipboard_append(self.srt_result)
        messagebox.showinfo("Info", "SRT скопійовано в буфер обміну!")

    def save_file(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".srt", filetypes=[("SRT Files", "*.srt")])
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.srt_result)
            messagebox.showinfo("Info", f"Збережено у {file_path}")

if __name__ == "__main__":
    app = LyricsApp()
    app.mainloop()