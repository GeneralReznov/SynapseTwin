"""Graph routes — Neo4j knowledge-graph visualization data."""
import logging
from fastapi import APIRouter, Depends
from app.middleware.auth import require_auth
from app.services.neo4j_service import get_graph_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/data")
async def graph_data(current_user: dict = Depends(require_auth)):
    try:
        data = await get_graph_data(current_user["userId"])
        return data
    except Exception as exc:
        logger.warning(f"graph_data error: {exc}")
        return {"nodes": [], "links": [], "offline": True, "error": "Failed to load graph data"}
