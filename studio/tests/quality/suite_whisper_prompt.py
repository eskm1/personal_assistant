"""The glossary's hotwords can no longer overflow Whisper's 448-token window (2026-10-21 failed edit:
"maximum decoding length must be > 0"). faster-whisper's model and tokenizer are stubbed.
Run: python tests/quality/suite_whisper_prompt.py (from studio/)"""
import sys, types, wave
from pathlib import Path
import numpy as np
from common import WORK, ok, finish
import transcribe, glossary

class Enc:
    def __init__(self, n): self.ids = list(range(n))
class Tok:  # one token per 3 characters, like a busy glossary
    def encode(self, text, add_special_tokens=False): return Enc(len(text) // 3 + 1)
class Seg:
    start, end, text, avg_logprob = 0.0, 1.0, "the screed", -0.2
    words = [types.SimpleNamespace(start=0.0, end=0.4, word="the"), types.SimpleNamespace(start=0.4, end=1.0, word="screed")]
class Model:
    hf_tokenizer = Tok()
    def __init__(self): self.calls = []
    def transcribe(self, audio, **kw):
        self.calls.append(kw.get("hotwords"))
        if kw.get("hotwords") and self.overflow:
            raise ValueError("The maximum decoding length must be > 0")
        return iter([Seg()]), types.SimpleNamespace(language="en", language_probability=0.99)

long = ", ".join(f"Brand{i} Term" for i in range(80))   # ~1,000 characters
fit = transcribe._fit_hotwords
m = Model(); m.overflow = False
transcribe._model = m
cut = fit(long)
ok(cut and len(Tok().encode(" " + cut).ids) <= transcribe.HOTWORD_TOKENS, "a long glossary is cut to fit HOTWORD_TOKENS")
ok(cut.split(", ")[0] == "Brand0 Term" and long.startswith(cut), "the first (shortest, most-misheard) terms are the ones kept")
ok(fit("MCST, HDB") == "MCST, HDB", "a short glossary is untouched")
ok(fit(None) is None and fit("") is None, "no glossary stays none")
transcribe._model = types.SimpleNamespace()
ok(fit(long) and len(" " + fit(long)) // 2 <= transcribe.HOTWORD_TOKENS, "without a tokenizer, a character estimate still caps it")
ok(transcribe.HOTWORD_TOKENS + 223 + 5 < 448, "hotwords + the previous text + markers always leave room to decode")

# The overflow itself: the edit carries on with a transcript made without the glossary.
wav = WORK / "a.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes((np.zeros(16000)).astype(np.int16).tobytes())
m = Model(); m.overflow = True
transcribe._model, transcribe._device = m, "cpu"
transcribe._language = lambda audio: ("en", 0.99)
glossary.hotwords = lambda: "MCST, HDB, screed"
out = transcribe.transcribe(wav)
ok([w["word"] for w in out["words"]] == ["the", "screed"], "an overflow no longer fails: the transcript comes back")
ok(m.calls == ["MCST, HDB, screed", None], "tried with the glossary, then again without it")
m2 = Model(); m2.overflow = False; transcribe._model = m2
transcribe.transcribe(wav)
ok(m2.calls == ["MCST, HDB, screed"], "a normal clip is transcribed once, with the glossary")
class Other(Model):
    def transcribe(self, audio, **kw): raise ValueError("something else")
transcribe._model = Other()
try:
    transcribe.transcribe(wav); ok(False, "another ValueError still raises")
except ValueError as e:
    ok("something else" in str(e), "another ValueError still raises")
finish()
