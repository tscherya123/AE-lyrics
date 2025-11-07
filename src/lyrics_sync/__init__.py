"""Core package for AE Lyrics Sync."""

from .alignment import LyricsAligner, AlignmentResult
from .audio_transcriber import AudioTranscriber, WordTiming, SegmentTranscription
from .report import AlignmentReport
from .srt_writer import SRTWriter

__all__ = [
    "LyricsAligner",
    "AlignmentResult",
    "AudioTranscriber",
    "WordTiming",
    "SegmentTranscription",
    "AlignmentReport",
    "SRTWriter",
]
