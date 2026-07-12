"""Memory routes — history, goals, weekly insights."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.middleware.auth import require_auth
from app.services.neo4j_service import get_user_history, get_weekly_insights, upsert_goal

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/history")
async def history(limit: int = 10, current_user: dict = Depends(require_auth)):
    lim = min(limit, 50)
    data = await get_user_history(current_user["userId"], lim)
    return {"history": data}


class GoalBody(BaseModel):
    title: str
    category: Optional[str] = "general"
    targetDate: Optional[str] = None


@router.post("/goal")
async def save_goal(body: GoalBody, current_user: dict = Depends(require_auth)):
    if not body.title:
        raise HTTPException(status_code=400, detail="title is required")
    await upsert_goal(current_user["userId"], body.title, body.category or "general", body.targetDate)
    return {"success": True}


@router.get("/insights")
async def insights(current_user: dict = Depends(require_auth)):
    data = await get_weekly_insights(current_user["userId"])
    return data
