from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class OfflineTTS:
    """Offline macOS TTS producing 16 kHz / 16-bit / mono signed PCM for ES8311."""

    def __init__(self, voice: str | None = None, rate_wpm: int = 195) -> None:
        self.voice = voice or os.environ.get("PASSPORT_TTS_VOICE", "")
        self.rate_wpm = rate_wpm

    def synthesize_pcm16(self, text: str) -> bytes:
        clean = (text or "").strip()
        if not clean:
            clean = "Task completed."

        say_bin = shutil.which("say")
        afconvert_bin = shutil.which("afconvert")
        if not say_bin or not afconvert_bin:
            raise RuntimeError("macOS 'say' and 'afconvert' are required for offline TTS")

        with tempfile.TemporaryDirectory(prefix="passport-tts-") as tmp:
            aiff_path = Path(tmp) / "speech.aiff"
            pcm_path = Path(tmp) / "speech.pcm"

            say_cmd = [say_bin, "-r", str(self.rate_wpm), "-o", str(aiff_path)]
            if self.voice:
                say_cmd.extend(["-v", self.voice])
            say_cmd.append(clean)
            subprocess.run(say_cmd, check=True, capture_output=True)
            if not aiff_path.exists() or aiff_path.stat().st_size == 0:
                raise RuntimeError("macOS 'say' produced empty audio (check Speech service access)")

            # Convert directly to little-endian 16-bit signed PCM at 16 kHz mono
            # matching AI Passport BSP: bsp_audio_set_format(16000, 16, 1)
            convert_cmd = [
                afconvert_bin,
                "-f",
                "caff",
                "-d",
                "LEI16@16000",
                "-c",
                "1",
                str(aiff_path),
                str(pcm_path),
            ]
            # Use raw PCM via ffmpeg if available; otherwise parse raw data from wav.
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-i",
                        str(aiff_path),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "s16le",
                        str(pcm_path),
                    ],
                    check=True,
                    capture_output=True,
                )
                pcm = pcm_path.read_bytes()
                if not pcm:
                    raise RuntimeError("macOS 'say' produced empty audio (check Speech service access)")
                return pcm

            wav_path = Path(tmp) / "speech.wav"
            subprocess.run(
                [
                    afconvert_bin,
                    "-f",
                    "WAVE",
                    "-d",
                    "LEI16@16000",
                    "-c",
                    "1",
                    str(aiff_path),
                    str(wav_path),
                ],
                check=True,
                capture_output=True,
            )
            raw = wav_path.read_bytes()
            data_idx = raw.find(b"data")
            if data_idx == -1 or data_idx + 8 > len(raw):
                raise RuntimeError("Failed to locate PCM data chunk in converted WAV")
            return raw[data_idx + 8 :]
