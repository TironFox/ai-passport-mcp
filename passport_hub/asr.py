from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


class OfflineASR:
    """Pluggable local offline ASR for 16 kHz / 16-bit / mono PCM uploads.

    Priority order:
    1. mlx_whisper (Apple Silicon native, fastest on Mac)
    2. whisper-cli / whisper.cpp binary (via PASSPORT_WHISPER_CLI + PASSPORT_WHISPER_MODEL)
    3. faster-whisper (Python)
    4. Fallback placeholder (when no local ASR model is installed yet)
    """

    def __init__(self) -> None:
        self.mlx_model = os.environ.get(
            "PASSPORT_MLX_WHISPER_MODEL", "mlx-community/whisper-small-mlx"
        )
        self.whisper_cli = os.environ.get("PASSPORT_WHISPER_CLI") or shutil.which(
            "whisper-cli"
        )
        self.whisper_model = os.environ.get("PASSPORT_WHISPER_MODEL") or self._find_whisper_model()
        self.faster_whisper_model = (
            os.environ.get("PASSPORT_FASTER_WHISPER_MODEL") or self._find_faster_whisper_model()
        )
        self._fw_instance = None
        self.language = os.environ.get("PASSPORT_ASR_LANG", "zh")

    @staticmethod
    def _find_faster_whisper_model() -> str:
        home = Path.home()
        hf_hub = home / ".cache" / "huggingface" / "hub"
        candidates: list[Path] = []
        for repo in (
            "models--Systran--faster-whisper-small",
            "models--Systran--faster-whisper-base",
            "models--Systran--faster-whisper-medium",
        ):
            snaps = hf_hub / repo / "snapshots"
            try:
                if snaps.is_dir():
                    for snap in sorted(snaps.iterdir()):
                        if (snap / "model.bin").is_file():
                            candidates.append(snap)
            except OSError:
                continue
        for direct in (home / "whisper-base-fresh", home / "whisper-medium-fresh"):
            if (direct / "model.bin").is_file():
                candidates.append(direct)
        return str(candidates[0]) if candidates else ""

    def describe_backend(self) -> str:
        try:
            import mlx_whisper  # type: ignore  # noqa: F401

            return f"mlx_whisper ({self.mlx_model})"
        except ImportError:
            pass
        if self.whisper_cli and self.whisper_model and Path(self.whisper_model).exists():
            return f"whisper.cpp ({self.whisper_model})"
        if self.faster_whisper_model:
            try:
                import faster_whisper  # type: ignore  # noqa: F401

                return f"faster-whisper local ({self.faster_whisper_model})"
            except ImportError:
                return (
                    f"local model found ({self.faster_whisper_model}), "
                    "run with a Python that has 'faster-whisper' installed"
                )
        return "NOT CONFIGURED (no local Whisper model/runtime detected)"

    @staticmethod
    def _find_whisper_model() -> str:
        search_dirs = [
            Path.home() / ".cache" / "whisper",
            Path.home() / "Models",
            Path("/opt/homebrew/share/whisper-cpp"),
            Path(__file__).resolve().parent.parent / "models",
        ]
        preferred = (
            "ggml-small.bin",
            "ggml-small-q5_1.bin",
            "ggml-base.bin",
            "ggml-medium.bin",
            "ggml-tiny.bin",
        )
        for d in search_dirs:
            if not d.is_dir():
                continue
            for name in preferred:
                cand = d / name
                if cand.is_file():
                    return str(cand)
            for cand in sorted(d.glob("ggml-*.bin")):
                if cand.is_file():
                    return str(cand)
        return ""

    @staticmethod
    def _analyze_and_normalize_pcm16(pcm_bytes: bytes) -> tuple[bytes, int, float]:
        """Compute (peak, rms) and apply gentle auto-gain for quiet MEMS mic recordings."""
        n_samples = len(pcm_bytes) // 2
        if n_samples == 0:
            return pcm_bytes, 0, 0.0
        samples = array.array("h")
        samples.frombytes(pcm_bytes[: n_samples * 2])
        peak = max(abs(s) for s in samples)
        rms = math.sqrt(sum(s * s for s in samples) / float(n_samples))
        # Boost quiet-but-real speech (avoid amplifying pure digital zero / tiny floor noise).
        if 80 < peak < 18000:
            gain = min(12.0, 22000.0 / float(peak))
            for i in range(n_samples):
                v = int(samples[i] * gain)
                samples[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
            return samples.tobytes(), peak, rms
        return pcm_bytes, peak, rms

    @staticmethod
    def _write_wav(pcm_bytes: bytes, wav_path: Path, sample_rate: int = 16000) -> None:
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

    def transcribe_pcm16(self, pcm_bytes: bytes, sample_rate: int = 16000) -> tuple[str, str]:
        """Return (transcript, engine_name)."""
        if not pcm_bytes:
            return "", "empty"

        norm_pcm, peak, rms = self._analyze_and_normalize_pcm16(pcm_bytes)
        print(
            f"[passport-hub] ASR audio stats: {len(pcm_bytes)} bytes, "
            f"{len(pcm_bytes) / float(sample_rate * 2):.2f}s, peak={peak}, rms={rms:.1f}",
            flush=True,
        )
        if peak < 40 or rms < 10.0:
            return "", "silence"

        with tempfile.TemporaryDirectory(prefix="passport-asr-") as tmp:
            wav_path = Path(tmp) / "input.wav"
            self._write_wav(norm_pcm, wav_path, sample_rate=sample_rate)
            try:
                self._write_wav(norm_pcm, Path("/tmp/passport_last_voice.wav"), sample_rate=sample_rate)
            except OSError:
                pass

            # 1. Try mlx_whisper (Apple Silicon)
            try:
                import mlx_whisper  # type: ignore

                result = mlx_whisper.transcribe(
                    str(wav_path),
                    path_or_hf_repo=self.mlx_model,
                    language=self.language,
                )
                text = (result.get("text") or "").strip()
                return text, "mlx_whisper"
            except ImportError:
                pass

            # 2. Try whisper.cpp CLI
            if self.whisper_cli and self.whisper_model and Path(self.whisper_model).exists():
                proc = subprocess.run(
                    [
                        self.whisper_cli,
                        "-m",
                        self.whisper_model,
                        "-l",
                        self.language,
                        "-nt",
                        "-f",
                        str(wav_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                text = " ".join(line.strip() for line in proc.stdout.splitlines()).strip()
                return text, "whisper.cpp"

            # 3. Try faster-whisper using local cached model (never re-downloads)
            try:
                from faster_whisper import WhisperModel  # type: ignore

                if self._fw_instance is None:
                    model_ref = self.faster_whisper_model or "small"
                    self._fw_instance = WhisperModel(
                        model_ref,
                        device="cpu",
                        compute_type="int8",
                        local_files_only=bool(self.faster_whisper_model),
                    )
                segments, _ = self._fw_instance.transcribe(
                    str(wav_path),
                    language=self.language,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    initial_prompt="以下是普通话指令，可能包含代码、Antigravity、Claude、Codex、Trae、Wi-Fi。",
                )
                text = "".join(seg.text for seg in segments).strip()
                return text, "faster_whisper"
            except ImportError:
                pass

        duration_sec = len(pcm_bytes) / float(sample_rate * 2)
        return (
            f"[Voice {duration_sec:.1f}s received - install mlx-whisper or whisper.cpp for offline ASR]",
            "unconfigured",
        )
