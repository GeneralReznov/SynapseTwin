"""SynapseTwin — Digital Twin pipeline as a real Render Workflow.

Deploy this file (and this folder) as its own Render "Workflow" service
(Render Dashboard → New → Workflow). It registers 6 tasks: 5 stage tasks
plus 1 parent task that chains them into a single connected pipeline run.

Local dev:  python main.py
Render:     Build Command: pip install -r requirements.txt
            Start Command: python main.py
"""
from __future__ import annotations
import re
import time
import uuid
import logging

from render_sdk import Workflows

from ai_providers import chat_completion, analyze_json
from neo4j_logger import log_pipeline_run_async
import asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("synapsetwin.workflow")

app = Workflows()

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


# ── Stage 1 — Language detection ────────────────────────────────────────────

@app.task
def detect_language(text: str) -> dict:
    """Lightweight heuristic language detection (no external call needed for this stage)."""
    is_hindi = bool(_DEVANAGARI_RE.search(text))
    return {
        "languageCode": "hi-IN" if is_hindi else "en-IN",
        "languageName": "Hindi" if is_hindi else "English",
    }


# ── Stage 2 — Emotion detection (Sarvam primary, Groq fallback) ────────────

@app.task
def detect_emotion(text: str) -> dict:
    result = analyze_json(
        "You are an emotion analysis engine. Analyze the text and return a JSON object with: "
        "emotion (string: happy|sad|excited|neutral|tired|stressed|anxious|frustrated|calm|energized), "
        "intensity (0.0-1.0), sentimentScore (-1.0 to 1.0), burnoutSignals (boolean). "
        "Return ONLY valid JSON.",
        text,
    )
    if not result:
        return {"emotion": "neutral", "intensity": 0.5, "sentimentScore": 0, "burnoutSignals": False}
    return result


# ── Stage 3 — Entity extraction (Sarvam primary, Groq fallback) ────────────

@app.task
def extract_entities(text: str) -> dict:
    result = analyze_json(
        "You are an entity extraction engine for a personal health/productivity app. "
        "Extract structured data and return JSON with optional fields: "
        "mood: {score:1-10, energyLevel, stressLevel}, sleep: {hours, quality}, "
        "exercise: {done, type, durationMinutes}, goals: [{title, progress}], "
        "work: {focusHours, meetings}. Only include fields you can confidently extract. "
        "Return valid JSON only.",
        text,
    )
    return result or {}


# ── Stage 4 — Recommendation generation (Sarvam primary, Groq fallback) ────

@app.task
def generate_recommendation(text: str, emotion: dict, entities: dict) -> str:
    result = chat_completion(
        "You are SynapseTwin, an AI Digital Twin coach running inside a Render Workflow. "
        f"Detected emotion: {emotion}. Extracted data: {entities}. "
        "Respond in 2-4 concise, action-oriented sentences referencing the detected state.",
        text,
    )
    return result["content"] if result.get("success") else \
        "Entry processed. Your Digital Twin will keep tracking patterns as more data comes in."


# ── Stage 5 — Persist the run to Neo4j AuraDB ───────────────────────────────

@app.task
def log_pipeline_result(user_id: str, run_id: str, stages: list, summary: str) -> dict:
    return asyncio.run(log_pipeline_run_async(user_id, run_id, stages, summary))


# ── Parent task — chains all 5 stages into one connected workflow run ───────

@app.task
def run_digital_twin_pipeline(text: str, user_id: str = "demo-user") -> dict:
    """The actual multi-stage, multi-task Render Workflow.

    Calling each stage's underlying function here is what makes this ONE
    workflow made of several connected tasks, rather than five independent,
    unrelated jobs. Each stage still exists as a first-class registered task
    (visible and individually triggerable/retryable in the Render Dashboard).
    """
    run_id = str(uuid.uuid4())
    stages_log: list[dict] = []

    # Each call below invokes another registered task's function directly —
    # this is what Render's docs describe as "chaining tasks by calling one
    # task's function from another" (see workflows-defining#organizing-tasks).
    # Every stage still exists as its own first-class task: independently
    # visible, triggerable, and retryable from the Render Dashboard.

    t0 = time.time()
    lang = detect_language(text)
    stages_log.append({"name": "detect_language", "status": "done", "ms": int((time.time() - t0) * 1000)})

    t0 = time.time()
    emotion = detect_emotion(text)
    stages_log.append({"name": "detect_emotion", "status": "done", "ms": int((time.time() - t0) * 1000)})

    t0 = time.time()
    entities = extract_entities(text)
    stages_log.append({"name": "extract_entities", "status": "done", "ms": int((time.time() - t0) * 1000)})

    t0 = time.time()
    recommendation = generate_recommendation(text, emotion, entities)
    stages_log.append({"name": "generate_recommendation", "status": "done", "ms": int((time.time() - t0) * 1000)})

    t0 = time.time()
    log_result = log_pipeline_result(user_id, run_id, stages_log, recommendation)
    stages_log.append({"name": "log_pipeline_result", "status": "done", "ms": int((time.time() - t0) * 1000)})

    return {
        "runId": run_id,
        "language": lang,
        "emotion": emotion,
        "entities": entities,
        "recommendation": recommendation,
        "neo4j": log_result,
        "stages": stages_log,
    }


if __name__ == "__main__":
    app.start()
