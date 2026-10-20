"""
Local Whisper transcription with word-level timestamps (faster-whisper).

Word timings matter beyond the transcript itself: later phases cut on them and
animate captions word by word. Runs on the eGPU when an NVIDIA card is attached
(CUDA) and on the CPU otherwise. The model stays loaded between clips; if the
GPU disappears mid-run (eGPU unplugged), the next attempt reloads on the CPU.
"""
import logging
import os
import sys
import warnings
import wave
from pathlib import Path

import numpy as np

from settings import WHISPER_DEVICE, WHISPER_LANGUAGE, WHISPER_LANGUAGES, WHISPER_MODEL

log = logging.getLogger("studio.transcribe")

# Model downloads from Hugging Face: Windows without Developer Mode can't make
# symlinks (the cache just uses a bit more disk), and no token is needed for
# public models. Neither warning needs action, so keep them out of the log.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
warnings.filterwarnings("ignore", module="huggingface_hub")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

_model = None
_device = None
_gpu_load_failed = False


def _add_cuda_dll_dirs() -> None:
    """On Windows, the CUDA libraries come from pip wheels (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) whose DLLs sit in site-packages/nvidia/<lib>/bin — not on
    the DLL search path. Add them so CTranslate2 can find cuBLAS and cuDNN."""
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def gpu_count() -> int:
    _add_cuda_dll_dirs()
    import ctranslate2
    try:
        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def _load(force_cpu: bool = False):
    global _model, _device, _gpu_load_failed
    from faster_whisper import WhisperModel

    use_gpu = not force_cpu and WHISPER_DEVICE != "cpu" and gpu_count() > 0
    if WHISPER_DEVICE == "cuda" and not use_gpu and not force_cpu:
        log.warning("WHISPER_DEVICE=cuda but no CUDA device found; using the CPU")
    if use_gpu:
        try:
            name = WHISPER_MODEL or "large-v3-turbo"
            _model, _device = WhisperModel(name, device="cuda", compute_type="float16"), "cuda"
            log.info("Whisper %s loaded on the GPU", name)
            return
        except Exception as e:  # missing cuDNN/cuBLAS, out of VRAM, eGPU gone
            _gpu_load_failed = True
            log.warning("GPU load failed (%s); falling back to the CPU", e)
    name = WHISPER_MODEL or "small"
    _model, _device = WhisperModel(name, device="cpu", compute_type="int8"), "cpu"
    log.info("Whisper %s loaded on the CPU", name)


def device() -> str | None:
    return _device


def _load_wav(wav_path: Path) -> np.ndarray:
    """The 16 kHz mono 16-bit WAV extract.extract_audio writes, as float32 samples.
    Handing Whisper samples (not a path) skips its own decoder, PyAV, whose current
    releases reject an option faster-whisper 1.2.1 passes (metadata_errors)."""
    with wave.open(str(wav_path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16000):
            raise RuntimeError(f"unexpected WAV format in {wav_path.name}")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


# Nudges Whisper towards Simplified characters; simplified() catches the rest.
_ZH_PROMPT = "以下是普通话的句子，用简体中文。"


def simplified(text: str) -> str:
    """Chinese text in Simplified characters (Whisper often writes Traditional)."""
    import zhconv
    return zhconv.convert(text, "zh-cn")


def _language(audio: np.ndarray) -> tuple[str | None, float | None]:
    """The clip's language, chosen among WHISPER_LANGUAGES only (Whisper alone may
    hear Singlish as Malay). A fixed WHISPER_LANGUAGE wins."""
    if WHISPER_LANGUAGE:
        return WHISPER_LANGUAGE, None
    if not WHISPER_LANGUAGES:
        return None, None  # any language
    if len(WHISPER_LANGUAGES) == 1:
        return WHISPER_LANGUAGES[0], None
    try:
        _, _, probs = _model.detect_language(audio, vad_filter=True, language_detection_segments=2)
    except Exception as e:  # e.g. no speech at all: the first language is as good as any
        log.info("Language detection failed (%s); using %s", e, WHISPER_LANGUAGES[0])
        return WHISPER_LANGUAGES[0], None
    allowed = [(lang, p) for lang, p in probs if lang in WHISPER_LANGUAGES]
    lang, p = max(allowed, key=lambda x: x[1]) if allowed else (WHISPER_LANGUAGES[0], 0.0)
    return lang, p


def _hotwords() -> str | None:
    """The trade glossary's terms (glossary.py: MCST, HDB, screed, brand names...), so Whisper hears
    them right. faster-whisper (1.2.1) puts hotwords before every window, not just the first, unlike
    initial_prompt. None when the glossary can't be read (Whisper still runs)."""
    try:
        import glossary
        return glossary.hotwords()
    except Exception as e:  # a transcript beats no transcript
        log.warning("No glossary for Whisper: %s", e)
        return None


def _transcribe_once(wav_path: Path) -> dict:
    audio = _load_wav(wav_path)
    language, picked_p = _language(audio)
    segments, info = _model.transcribe(
        audio,
        language=language,
        initial_prompt=_ZH_PROMPT if language == "zh" else None,
        hotwords=_hotwords(),
        word_timestamps=True,
        vad_filter=True,  # skips silence, which also stops Whisper inventing words in it
    )
    zh = (language or info.language) == "zh"
    fix = simplified if zh else (lambda t: t)
    out_segments, words = [], []
    for seg in segments:  # a generator: decoding happens while iterating
        out_segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": fix(seg.text.strip()),
                             "logprob": round(seg.avg_logprob, 3)})  # Whisper's confidence, for quality.py
        for w in seg.words or []:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": fix(w.word.strip())})
    return {
        "language": language or info.language,
        "language_probability": round(picked_p if picked_p is not None else info.language_probability, 3),
        "device": _device,
        "text": " ".join(s["text"] for s in out_segments).strip(),
        "segments": out_segments,
        "words": words,
    }


def transcribe(wav_path: Path) -> dict:
    # Reload when there's no model yet, or when we're on the CPU only because the
    # eGPU was unplugged and it is back now. A GPU that failed to load (missing
    # CUDA libraries) is not retried every clip — restart the worker after fixing.
    if _model is None or (_device == "cpu" and not _gpu_load_failed
                          and WHISPER_DEVICE != "cpu" and gpu_count() > 0):
        _load()
    try:
        return _transcribe_once(wav_path)
    except Exception as e:
        if _device != "cuda":
            raise
        log.warning("GPU transcription failed (%s); retrying on the CPU", e)
        _load(force_cpu=True)
        return _transcribe_once(wav_path)
