# SynapseTwin — Render Workflows Service

This folder is a **separate, standalone codebase** for the Render Workflows sponsor
track. It does **not** modify the main SynapseTwin app in this repl — it's meant to
be pushed to its **own GitHub repository** and deployed as its **own Workflow
service** on Render, alongside your existing SynapseTwin API.

## What this actually is

This service defines **SynapseTwin's AI Digital Twin pipeline as a real,
multi-stage Render Workflow** — the same reasoning pipeline the main app runs
inline on each request, but here it runs as a genuine chain of connected,
independently-retryable tasks on Render's infrastructure:

```
run_digital_twin_pipeline   (parent task — chains all of the below)
 ├─ 1. detect_language        → identifies the input's language
 ├─ 2. detect_emotion         → Sarvam/Groq AI: mood, stress signals, sentiment
 ├─ 3. extract_entities       → Sarvam/Groq AI: mood, sleep, exercise, habits, goals
 ├─ 4. generate_recommendation→ Sarvam/Groq AI: personalized coaching response
 └─ 5. log_pipeline_result    → writes the full run to Neo4j AuraDB as graph nodes
```

Each stage is its own `@app.task` — a standalone unit of work Render can queue,
retry, and scale independently. The parent task chains them together, so a
single triggered run performs five connected steps of real work (three AI calls,
one language-detection heuristic, one Neo4j write) — not just a deployed API
wrapper.

This satisfies the track's requirements:
- **Multiple connected tasks/stages** — 5 chained tasks per run.
- **Meaningful work beyond a simple API call** — AI reasoning + graph persistence.
- **Demonstrated via a live Render deployment** — see deploy steps below; the
  Render Dashboard shows every run's status, duration, and per-stage logs.

## Why this lives in its own folder/repo

Render Workflow services are deployed independently of web/worker services — you
create them via **Render Dashboard → New → Workflow**, pointing at their own
repo. Bundling workflow tasks into the same repo as a web app is possible but
Render's own tutorials and examples always structure workflows as their own
deployable unit, which is what this folder is for. Push this folder's contents
to a new GitHub repo before deploying.

## Files

| File | Purpose |
|---|---|
| `main.py` | Workflow entry point — registers all 5 tasks and starts the SDK |
| `ai_providers.py` | Sarvam AI (primary) + Groq (fallback) chat/completion helper, self-contained |
| `neo4j_logger.py` | Writes each pipeline run to Neo4j AuraDB as connected graph nodes |
| `trigger_client.py` | Example script showing how *any* app (e.g. the main SynapseTwin API) triggers a run of this workflow and waits for its result |
| `requirements.txt` | `render_sdk`, `httpx`, `neo4j` — this service's only dependencies |
| `.env.example` | Required environment variables |

## Deploying this as a real Render Workflow

1. **Push this folder to its own GitHub repo.**
   ```bash
   cd render-workflows
   git init
   git add .
   git commit -m "SynapseTwin Digital Twin pipeline as a Render Workflow"
   git remote add origin https://github.com/<you>/synapsetwin-workflow.git
   git push -u origin main
   ```

2. **Create the Workflow service on Render.**
   - Render Dashboard → **New → Workflow**
   - Connect the GitHub repo you just pushed
   - Language: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python main.py`
   - Add environment variables from `.env.example` (Sarvam, Groq, Neo4j credentials — the *same* AuraDB instance your main app already uses)

3. **Verify registration.** Once deployed, the Render Dashboard's Workflow page
   lists all 5 registered tasks (`detect_language`, `detect_emotion`,
   `extract_entities`, `generate_recommendation`, `log_pipeline_result`,
   `run_digital_twin_pipeline`) with their slugs, e.g.
   `synapsetwin-workflow/run_digital_twin_pipeline`.

4. **Trigger a run** — either manually from the Dashboard (for a quick demo), or
   from code. `trigger_client.py` shows both:
   - **Manual trigger** (Dashboard → task page → "Trigger Run") is the fastest
     way to produce evidence of a live run for judging — the Dashboard shows
     each stage's status, duration, and logs.
   - **Programmatic trigger** from your main SynapseTwin API: set
     `RENDER_API_KEY` in the API's environment, then call
     `render.workflows.start_task("synapsetwin-workflow/run_digital_twin_pipeline", [...])`
     the same way `trigger_client.py` does, e.g. from a new `/api/agent/pipeline-run`
     endpoint if you want the main app to kick off a workflow run itself.

