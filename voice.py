from openai import OpenAI
from config import OPENAI_API_KEY

_client = OpenAI(api_key=OPENAI_API_KEY)


def transcribe_voice(ogg_path: str) -> str:
    """Transcribe a Telegram voice note (.ogg) using the Whisper API."""
    with open(ogg_path, "rb") as audio_file:
        result = _client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text",
            language="en",
        )
    return result.strip()
