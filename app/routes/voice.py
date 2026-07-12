"""Voice routes — STT, TTS, Translation, Language Detection."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.services.sarvam import speech_to_text, text_to_speech, translate_text, detect_language

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/stt")
async def stt(
    audio: UploadFile = File(...),
    languageCode: str = Form(default="hi-IN"),
    current_user: dict = Depends(require_auth),
):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio file is required")
    mime_type = audio.content_type or "audio/webm"
    result = await speech_to_text(audio_bytes, languageCode, mime_type)
    return result


class TTSBody(BaseModel):
    text: str
    languageCode: str = "hi-IN"


@router.post("/tts")
async def tts(body: TTSBody, current_user: dict = Depends(require_auth)):
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")
    result = await text_to_speech(body.text, body.languageCode)
    return result


class TranslateBody(BaseModel):
    text: str
    sourceLanguage: str = "hi-IN"
    targetLanguage: str = "en-IN"


@router.post("/translate")
async def translate(body: TranslateBody, current_user: dict = Depends(require_auth)):
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")
    result = await translate_text(body.text, body.sourceLanguage, body.targetLanguage)
    return result


class DetectBody(BaseModel):
    text: str


@router.post("/detect")
async def detect(body: DetectBody, current_user: dict = Depends(require_auth)):
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")
    result = await detect_language(body.text)
    return result
