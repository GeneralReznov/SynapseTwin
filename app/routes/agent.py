"""Agent pipeline routes — text, voice, async dispatch."""
import uuid
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.services.agent import run_agent_pipeline
from app.services.sarvam import speech_to_text, text_to_speech
from app.services.neo4j_service import get_weekly_insights

router = APIRouter(prefix="/api/agent", tags=["agent"])

# ── In-memory job store (Render Workflow simulation) ────────────────────────────
_job_store: dict[str, dict] = {}


def _create_job(user_id: str, payload: dict) -> str:
    job_id = str(uuid.uuid4())
    _job_store[job_id] = {
        "id":          job_id,
        "userId":      user_id,
        "status":      "queued",
        "payload":     payload,
        "createdAt":   time.time(),
        "completedAt": None,
        "result":      None,
        "error":       None,
    }
    return job_id


async def _execute_job(job_id: str):
    job = _job_store.get(job_id)
    if not job:
        return
    job["status"] = "running"
    try:
        payload = job["payload"]
        user_id = payload["user_id"]
        # Fetch weekly_data required by run_agent_pipeline
        try:
            weekly = await get_weekly_insights(user_id)
            weekly_data = weekly.get("weeklyData", [])
        except Exception:
            weekly_data = []
        result = await run_agent_pipeline(
            raw_input=payload["raw_input"],
            user_id=user_id,
            preferred_language=payload.get("preferred_language", "en-IN"),
            weekly_data=weekly_data,
        )
        job["status"]      = "completed"
        job["result"]      = result
        job["completedAt"] = time.time()
    except Exception as exc:
        job["status"] = "failed"
        job["error"]  = str(exc)
        job["completedAt"] = time.time()


# ── POST /api/agent/process ────────────────────────────────────────────────────
class ProcessBody(BaseModel):
    text: str
    preferredLanguage: Optional[str] = "en-IN"


@router.post("/process")
async def process_text(body: ProcessBody, current_user: dict = Depends(require_auth)):
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")

    user_id = current_user["userId"]
    try:
        weekly = await get_weekly_insights(user_id)
        weekly_data = weekly.get("weeklyData", [])
    except Exception:
        weekly_data = []

    result = await run_agent_pipeline(
        raw_input=body.text,
        user_id=user_id,
        preferred_language=body.preferredLanguage or "en-IN",
        weekly_data=weekly_data,
    )
    return result


# ── POST /api/agent/voice ──────────────────────────────────────────────────────
@router.post("/voice")
async def process_voice(
    audio: UploadFile = File(...),
    preferredLanguage: str = Form(default="en-IN"),
    current_user: dict = Depends(require_auth),
):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio file is required")

    mime_type  = audio.content_type or "audio/webm"
    stt_result = await speech_to_text(audio_bytes, preferredLanguage, mime_type)
    transcript  = stt_result.get("transcript", "")
    if not transcript:
        return {"success": False, "error": "Could not transcribe audio. Please try again."}

    user_id = current_user["userId"]
    try:
        weekly = await get_weekly_insights(user_id)
        weekly_data = weekly.get("weeklyData", [])
    except Exception:
        weekly_data = []

    result = await run_agent_pipeline(
        raw_input=transcript,
        user_id=user_id,
        preferred_language=preferredLanguage,
        weekly_data=weekly_data,
    )

    tts_audio = None
    if result.get("twinResponse"):
        tts = await text_to_speech(result["twinResponse"], result.get("responseLanguage", preferredLanguage))
        if tts.get("success"):
            tts_audio = tts.get("audioBase64")

    return {**result, "transcript": transcript, "ttsAudio": tts_audio}


# ── POST /api/agent/dispatch — Render Workflow async dispatch ──────────────────
class DispatchBody(BaseModel):
    text: str
    preferredLanguage: Optional[str] = "en-IN"


@router.post("/dispatch")
async def dispatch(
    body: DispatchBody,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_auth),
):
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")

    user_id = current_user["userId"]
    payload = {
        "raw_input":          body.text,
        "user_id":            user_id,
        "preferred_language": body.preferredLanguage or "en-IN",
    }
    job_id = _create_job(user_id, payload)
    background_tasks.add_task(_execute_job, job_id)
    return {"jobId": job_id, "status": "queued", "message": "Agent pipeline dispatched via Render Workflow"}


# ── GET /api/agent/job/:jobId ──────────────────────────────────────────────────
@router.get("/job/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(require_auth)):
    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["userId"] != current_user["userId"]:
        raise HTTPException(status_code=403, detail="You do not have access to this job.")
    return {
        "found":   True,
        "id":      job["id"],
        "status":  job["status"],
        "result":  job["result"],
        "error":   job["error"],
        "retries": 0,
    }


# ── GET /api/agent/jobs ────────────────────────────────────────────────────────
@router.get("/jobs")
async def list_jobs(current_user: dict = Depends(require_auth)):
    user_id = current_user["userId"]
    jobs = [
        {"id": j["id"], "status": j["status"], "createdAt": j["createdAt"], "completedAt": j["completedAt"]}
        for j in sorted(_job_store.values(), key=lambda x: x["createdAt"], reverse=True)
        if j["userId"] == user_id
    ]
    return {"jobs": jobs}
