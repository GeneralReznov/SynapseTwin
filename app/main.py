"""SynapseTwin — FastAPI entry point."""
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.db.neo4j_db import test_connection, init_schema
from app.routes import users, agent, voice, memory, insights, graph, goals, enterprise, notifications
from app.routes import learning, environment

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("synapsetwin")

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["100/15minutes"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🧠 SynapseTwin FastAPI starting…")
    await init_schema()
    await test_connection()
    logger.info("✅ Ready")
    yield
    logger.info("SynapseTwin shutting down")


app = FastAPI(
    title="SynapseTwin API",
    description="Your AI Digital Twin — understanding your life as a connected system",
    version="3.0.0",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global error handler ───────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}")
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Health / root ──────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status":       "healthy",
        "service":      "SynapseTwin API",
        "version":      "3.0.0",
        "neo4j":        "configured" if os.getenv("NEO4J_URI")            else "not configured",
        "sarvam":       "configured" if os.getenv("SARVAM_API_KEY")       else "not configured",
        "groq":         "configured" if os.getenv("GROQ_API_KEY")         else "not configured",
        "openweather":  "configured" if os.getenv("OPENWEATHER_API_KEY")  else "not configured",
        "tts_fallback": "gtts",
        "integrations": ["coursera", "udemy", "openweather", "location", "groq_ai"],
    }


@app.get("/api")
async def api_root():
    return {
        "service":  "SynapseTwin API",
        "tagline":  "Your AI Digital Twin — understanding your life as a connected system",
        "version":  "3.0.0",
        "endpoints": {
            "agent":         "/api/agent",
            "voice":         "/api/voice",
            "memory":        "/api/memory",
            "insights":      "/api/insights",
            "graph":         "/api/graph",
            "users":         "/api/users",
            "goals":         "/api/goals",
            "enterprise":    "/api/enterprise",
            "notifications": "/api/notifications",
        },
    }


# ── API routers ────────────────────────────────────────────────────────────────
app.include_router(users.router)
app.include_router(agent.router)
app.include_router(voice.router)
app.include_router(memory.router)
app.include_router(insights.router)
app.include_router(graph.router)
app.include_router(goals.router)
app.include_router(enterprise.router)
app.include_router(notifications.router)
app.include_router(learning.router)
app.include_router(environment.router)

# ── Serve static frontend ──────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")

    @app.get("/")
    async def serve_root():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        # Browsers auto-request this on every page load; avoid noisy 404s in logs.
        favicon_path = os.path.join(FRONTEND_DIR, "static", "favicon.ico")
        if os.path.isfile(favicon_path):
            return FileResponse(favicon_path)
        return Response(status_code=204)

    @app.get("/{page}.html")
    async def serve_page(page: str):
        path = os.path.join(FRONTEND_DIR, f"{page}.html")
        if os.path.isfile(path):
            return FileResponse(path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
