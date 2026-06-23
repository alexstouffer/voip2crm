"""Thin wrapper around WhisperX. Imports happen lazily so the rest of the
pipeline (e.g. the local dry-run path) works even on a machine without the
heavy ML deps loaded."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class Transcriber:
    def __init__(self, cfg: dict):
        self.model_name = cfg.get("model", "large-v2")
        self.device = cfg.get("device", "cpu")
        self.compute_type = cfg.get("compute_type", "int8")
        self.batch_size = int(cfg.get("batch_size", 16))
        self.language = cfg.get("language")
        self.diarize = bool(cfg.get("diarize", False))
        self.hf_token = cfg.get("hf_token") or None
        self.min_speakers = cfg.get("min_speakers")
        self.max_speakers = cfg.get("max_speakers")
        self._model = None
        self._whisperx = None

    def _ensure_loaded(self):
        if self._model is None:
            import whisperx  # imported here to keep startup cheap
            self._whisperx = whisperx
            self._model = whisperx.load_model(
                self.model_name, self.device, compute_type=self.compute_type
            )

    def transcribe(self, audio_path: str) -> tuple[str, list[dict]]:
        """Return (plain_text, segments). Segments include start/end/text and,
        when diarization is on, a 'speaker' label."""
        if not audio_path or not Path(audio_path).exists():
            return "", []

        self._ensure_loaded()
        wx = self._whisperx

        audio = wx.load_audio(audio_path)
        result = self._model.transcribe(
            audio, batch_size=self.batch_size,
            language=self.language if self.language else None,
        )
        lang = result.get("language", self.language or "en")

        # Word-level alignment improves timestamps (and diarization handoff).
        try:
            align_model, metadata = wx.load_align_model(language_code=lang, device=self.device)
            result = wx.align(
                result["segments"], align_model, metadata, audio, self.device,
                return_char_alignments=False,
            )
        except Exception:
            pass  # alignment is best-effort; raw segments still usable

        if self.diarize and self.hf_token:
            try:
                diarizer = wx.DiarizationPipeline(use_auth_token=self.hf_token, device=self.device)
                kwargs = {}
                if self.min_speakers:
                    kwargs["min_speakers"] = int(self.min_speakers)
                if self.max_speakers:
                    kwargs["max_speakers"] = int(self.max_speakers)
                diarize_segments = diarizer(audio, **kwargs)
                result = wx.assign_word_speakers(diarize_segments, result)
            except Exception:
                pass

        segments = result.get("segments", [])
        text = self._render_text(segments)
        return text, segments

    @staticmethod
    def _render_text(segments: list[dict]) -> str:
        lines = []
        for seg in segments:
            spk = seg.get("speaker")
            content = (seg.get("text") or "").strip()
            if not content:
                continue
            lines.append(f"[{spk}] {content}" if spk else content)
        return "\n".join(lines)
