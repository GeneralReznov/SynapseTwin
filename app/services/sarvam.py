from __future__ import annotations
import os
import io
import base64
import json
import logging
import httpx
import re as _re

logger = logging.getLogger(__name__)

SARVAM_BASE     = "https://api.sarvam.ai"
SARVAM_LLM_BASE = "https://api.sarvam.ai/v1"
SARVAM_KEY      = os.getenv("SARVAM_API_KEY", "")

LANG_CODE: dict[str, str] = {
    "English":   "en-IN", "Hindi":     "hi-IN", "Hinglish":  "hi-IN",
    "Marathi":   "mr-IN", "Tamil":     "ta-IN", "Bengali":   "bn-IN",
    "Telugu":    "te-IN", "Kannada":   "kn-IN", "Gujarati":  "gu-IN",
    "Malayalam": "ml-IN", "Punjabi":   "pa-IN", "Odia":      "od-IN",
}
LANG_NAME: dict[str, str] = {v: k for k, v in LANG_CODE.items()}

# gTTS language code mapping (BCP-47 → ISO 639-1 used by gTTS)
_GTTS_LANG_MAP: dict[str, str] = {
    "en-IN": "en", "hi-IN": "hi", "mr-IN": "mr", "ta-IN": "ta",
    "bn-IN": "bn", "te-IN": "te", "kn-IN": "kn", "gu-IN": "gu",
    "ml-IN": "ml", "pa-IN": "pa", "od-IN": "or",
}

# gTTS TLD for Indian English
_GTTS_TLD_MAP: dict[str, str] = {
    "en-IN": "co.in",
}


def _headers() -> dict:
    return {"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"}


async def _gtts_tts(text: str, language_code: str = "en-IN") -> dict:
    """Generate speech using gTTS as fallback. Returns same shape as Sarvam TTS."""
    try:
        from gtts import gTTS
        lang = _GTTS_LANG_MAP.get(language_code, "en")
        tld  = _GTTS_TLD_MAP.get(language_code, "com")
        # Truncate to reasonable length
        clean_text = text[:3000]
        tts = gTTS(text=clean_text, lang=lang, tld=tld, slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        audio_bytes = buf.read()
        audio_b64   = base64.b64encode(audio_bytes).decode("utf-8")
        return {
            "success":      True,
            "audioBase64":  audio_b64,
            "languageCode": language_code,
            "provider":     "gtts",
            "format":       "mp3",
        }
    except ImportError:
        logger.error("gTTS not installed — run: pip install gtts")
        return {"success": False, "error": "gTTS not available", "fallback": "browser"}
    except Exception as exc:
        logger.error(f"gTTS error: {exc}")
        return {"success": False, "error": str(exc), "fallback": "browser"}


# ── Speech-to-Text ─────────────────────────────────────────────────────────────

_MIME_TO_EXT: dict[str, str] = {
    "audio/webm":              "webm",
    "audio/webm;codecs=opus":  "webm",
    "audio/mp4":               "mp4",
    "audio/ogg":               "ogg",
    "audio/ogg;codecs=opus":   "ogg",
    "audio/wav":               "wav",
    "audio/mpeg":              "mp3",
    "audio/x-m4a":             "m4a",
}


async def _raw_sarvam_stt(audio_bytes: bytes, language_code: str, mime_type: str) -> dict:
    """Pure Sarvam STT call — no fallback logic. Used as the fallback path when Groq fails."""
    if not SARVAM_KEY:
        return {"success": False, "transcript": "", "error": "SARVAM_API_KEY not configured"}
    ext          = _MIME_TO_EXT.get(mime_type.split(";")[0].strip().lower(), "webm")
    filename     = f"audio.{ext}"
    content_type = mime_type.split(";")[0].strip() or "audio/webm"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {"file": (filename, audio_bytes, content_type)}
            data  = {"model": "saaras:v3", "language_code": language_code, "with_timestamps": "false"}
            resp  = await client.post(
                f"{SARVAM_BASE}/speech-to-text",
                files=files, data=data,
                headers={"api-subscription-key": SARVAM_KEY},
            )
            resp.raise_for_status()
            return {
                "success":      True,
                "transcript":   resp.json().get("transcript", ""),
                "languageCode": language_code,
                "provider":     "sarvam",
            }
    except Exception as exc:
        logger.warning(f"Sarvam STT failed: {exc}")
        return {"success": False, "transcript": "", "error": str(exc)}


async def speech_to_text(
    audio_bytes: bytes,
    language_code: str = "hi-IN",
    mime_type: str = "audio/webm",
) -> dict:
    """STT — Groq (Whisper) is primary. Falls back to Sarvam AI, then to the browser's Web Speech API."""
    try:
        from app.services.groq_service import speech_to_text as groq_stt, is_available as groq_available
        if groq_available():
            result = await groq_stt(audio_bytes, mime_type, language_code)
            if result.get("success"):
                result["languageCode"] = language_code
                return result
            logger.warning(f"Groq STT failed ({result.get('error')}) — falling back to Sarvam")
        else:
            logger.info("GROQ_API_KEY not configured — trying Sarvam STT fallback")
    except Exception as exc:
        logger.warning(f"Groq STT error ({exc}) — falling back to Sarvam")

    result = await _raw_sarvam_stt(audio_bytes, language_code, mime_type)
    if result.get("success"):
        return result

    return {
        "success":      False,
        "transcript":   "",
        "fallbackHint": "browser_speech_api",
        "message":      "Voice transcription unavailable. Your browser's microphone will handle voice input.",
    }


# ── Text-to-Speech ─────────────────────────────────────────────────────────────

async def _raw_sarvam_tts(text: str, language_code: str) -> dict:
    """Pure Sarvam TTS call — no fallback logic. Used as the fallback path when Groq fails."""
    if not SARVAM_KEY:
        return {"success": False, "error": "SARVAM_API_KEY not configured"}
    try:
        payload = {
            "inputs":               [text[:2500]],
            "target_language_code": language_code,
            "speaker":              "meera",
            "pitch":                0, "pace": 1.0, "loudness": 1.5,
            "enable_preprocessing": True,
            "model":                "bulbul:v3",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SARVAM_BASE}/text-to-speech",
                json=payload, headers=_headers(),
            )
            resp.raise_for_status()
            audio = resp.json().get("audios", [None])[0]
            if audio:
                return {
                    "success":      True,
                    "audioBase64":  audio,
                    "languageCode": language_code,
                    "provider":     "sarvam",
                    "format":       "wav",
                }
            return {"success": False, "error": "Sarvam TTS returned no audio"}
    except Exception as exc:
        logger.warning(f"Sarvam TTS failed: {exc}")
        return {"success": False, "error": str(exc)}


async def text_to_speech(text: str, language_code: str = "hi-IN") -> dict:
    """TTS — Groq (PlayAI) is primary. Falls back to Sarvam AI, then to gTTS as a last resort."""
    if not text:
        return {"success": False, "error": "empty text"}

    try:
        from app.services.groq_service import text_to_speech as groq_tts, is_available as groq_available
        if groq_available():
            result = await groq_tts(text)
            if result.get("success"):
                result["languageCode"] = language_code
                return result
            logger.warning(f"Groq TTS failed ({result.get('error')}) — falling back to Sarvam")
        else:
            logger.info("GROQ_API_KEY not configured — trying Sarvam TTS fallback")
    except Exception as exc:
        logger.warning(f"Groq TTS error ({exc}) — falling back to Sarvam")

    result = await _raw_sarvam_tts(text, language_code)
    if result.get("success"):
        return result
    logger.warning(f"Sarvam TTS fallback also failed ({result.get('error')}) — falling back to gTTS")

    return await _gtts_tts(text, language_code)


# ── Translation ────────────────────────────────────────────────────────────────

async def translate_text(
    text: str,
    source_lang: str = "hi-IN",
    target_lang: str = "en-IN",
) -> dict:
    if not SARVAM_KEY or source_lang == target_lang:
        return {"success": True, "translatedText": text}
    try:
        payload = {
            "input":                text,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            "speaker_gender":       "Male",
            "mode":                 "formal",
            "enable_preprocessing": True,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SARVAM_BASE}/translate", json=payload, headers=_headers(),
            )
            resp.raise_for_status()
            return {"success": True, "translatedText": resp.json().get("translated_text", text)}
    except Exception as exc:
        logger.error(f"Sarvam translate error: {exc}")
        return {"success": False, "translatedText": text, "error": str(exc)}


# ── Language Detection ─────────────────────────────────────────────────────────

async def detect_language(text: str) -> dict:
    if not SARVAM_KEY:
        return {"success": False, "languageCode": "en-IN", "languageName": "English"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{SARVAM_BASE}/text-lid", json={"input": text}, headers=_headers(),
            )
            resp.raise_for_status()
            code = resp.json().get("language_code", "en-IN")
            return {"success": True, "languageCode": code, "languageName": LANG_NAME.get(code, "English")}
    except Exception:
        has_devanagari = bool(_re.search(r"[\u0900-\u097F]", text))
        return {
            "success":      False,
            "languageCode": "hi-IN" if has_devanagari else "en-IN",
            "languageName": "Hindi" if has_devanagari else "English",
        }

def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers the LLM sometimes adds."""
    text = text.strip()
    text = _re.sub(r"^```(?:json)?\s*", "", text, flags=_re.IGNORECASE)
    text = _re.sub(r"\s*```$", "", text)
    return text.strip()


async def _raw_sarvam_chat(system_prompt: str, user_message: str, json_mode: bool) -> dict:
    """Pure Sarvam chat call (sarvam-30b) with its own retry loop — no fallback logic.
    Used as the fallback path when Groq is unconfigured or fails."""
    if not SARVAM_KEY:
        return {"success": False, "content": "", "error": "SARVAM_API_KEY not configured"}
    last_exc = None
    for attempt in range(3):
        try:
            payload = {
                "model":    "sarvam-30b",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens":  1024,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    f"{SARVAM_LLM_BASE}/chat/completions",
                    json=payload, headers=_headers(),
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if json_mode:
                    content = _strip_markdown_fences(content)
                return {"success": True, "content": content, "provider": "sarvam"}
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                import asyncio
                await asyncio.sleep(1.5 * (attempt + 1))
    logger.warning(f"Sarvam LLM failed after 3 attempts: {last_exc}")
    return {"success": False, "content": "", "error": str(last_exc)}


async def chat_completion(
    system_prompt: str,
    user_message: str,
    json_mode: bool = False,
) -> dict:
    """LLM completion — Groq is primary. Falls back to Sarvam AI only if Groq is
    unconfigured or fails after retries, so AI reasoning stays Groq-first end to end."""
    try:
        from app.services.groq_service import chat_completion as groq_chat, is_available as groq_available
        if groq_available():
            result = await groq_chat(
                system_prompt, user_message,
                json_mode=json_mode, temperature=0.3, max_tokens=1024,
            )
            if result.get("success"):
                result["provider"] = "groq"
                return result
            logger.warning(f"Groq LLM failed ({result.get('error')}) — falling back to Sarvam")
        else:
            logger.info("GROQ_API_KEY not configured — using Sarvam fallback for chat completion")
    except Exception as exc:
        logger.warning(f"Groq LLM error ({exc}) — falling back to Sarvam")

    result = await _raw_sarvam_chat(system_prompt, user_message, json_mode)
    if result.get("success"):
        return result

    return {"success": False, "content": "", "mock": True}


async def analyze_json(system_prompt: str, user_message: str) -> dict:
    """Convenience wrapper that always requests JSON output (Groq-primary, Sarvam-fallback)."""
    result = await chat_completion(system_prompt, user_message, json_mode=True)
    if not result.get("success"):
        return {}
    try:
        text = _strip_markdown_fences(result["content"])
        return json.loads(text)
    except Exception as e:
        logger.error(f"Groq/Sarvam JSON parse error: {e} — content: {result['content'][:200]}")
        return {}


# ── Emotion Detection ──────────────────────────────────────────────────────────

async def detect_emotion(text: str) -> dict:
    result = await chat_completion(
        "You are an emotion analysis engine. Analyze the text and return a JSON object with: "
        "emotion (string: happy|sad|excited|neutral|tired|stressed|anxious|frustrated|calm|energized), "
        "intensity (0.0-1.0), sentimentScore (-1.0 to 1.0), burnoutSignals (boolean), stressKeywords (string array). "
        "Return ONLY valid JSON.",
        text,
        json_mode=True,
    )
    if not result["success"]:
        return {"emotion": "neutral", "intensity": 0.5, "sentimentScore": 0, "burnoutSignals": False, "stressKeywords": []}
    try:
        return json.loads(result["content"])
    except Exception:
        return {"emotion": "neutral", "intensity": 0.5, "sentimentScore": 0, "burnoutSignals": False, "stressKeywords": []}


# ── Entity Extraction ──────────────────────────────────────────────────────────

async def extract_entities(text: str) -> dict:
    result = await chat_completion(
        "You are an entity extraction engine for a personal health/productivity app. "
        "Extract structured data from the user's input and return JSON with these optional fields: "
        "mood: {score:1-10, energyLevel:'High|Medium|Low', stressLevel:'High|Medium|Low', notes:string}, "
        "sleep: {hours:number, quality:'Good|Fair|Poor'}, "
        "exercise: {done:boolean, type:string, durationMinutes:number, intensity:'High|Medium|Low'}, "
        "habits: {meditation:boolean, gym:boolean, walking:boolean, journaling:boolean, reading:boolean, deepWork:boolean}, "
        "goals: [{title:string, progress:number}], "
        "socialInteraction: {person:string, relationship:string}, "
        "work: {focusHours:number, meetings:number, stressful:boolean}, "
        "learning: {topic:string, type:'course|book|video|article'}, "
        "waterIntake: number (liters). "
        "Only include fields you can confidently extract. Return valid JSON only.",
        text,
        json_mode=True,
    )
    if not result["success"]:
        return {}
    try:
        return json.loads(result["content"])
    except Exception:
        return {}


# ── Twin Response ──────────────────────────────────────────────────────────────

async def generate_twin_response(
    user_input: str,
    graph_context: dict,
    language: str = "en-IN",
) -> str:
    lang_name = LANG_NAME.get(language, "English")
    result = await chat_completion(
        f"You are SynapseTwin, an AI Digital Twin assistant. You speak like a caring, intelligent personal coach. "
        f"You have access to the user's knowledge graph data: {json.dumps(graph_context)}. "
        f"Respond in {lang_name}. Be concise (2-4 sentences), insightful, and action-oriented. "
        f"Reference actual data from the graph when relevant. If you detect burnout risk, gently address it.",
        user_input,
    )
    return result["content"] if result["success"] else \
           "I've noted your entry and updated your knowledge graph. Keep tracking — patterns emerge over time."
