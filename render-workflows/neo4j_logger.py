"""Writes each Render Workflow pipeline run into the same Neo4j AuraDB
knowledge graph the main SynapseTwin app uses, so workflow runs become
part of the user's connected graph — not just an isolated log line.
"""
from __future__ import annotations
import os
import logging
from datetime import datetime, timezone

from neo4j import AsyncGraphDatabase

logger = logging.getLogger("synapsetwin.workflow.neo4j")

NEO4J_URI      = os.getenv("NEO4J_URI", "")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")


async def log_pipeline_run_async(user_id: str, run_id: str, stages: list[dict], summary: str) -> dict:
    """Creates a (:PipelineRun) node connected to (:User), with one (:PipelineStage)
    node per completed stage — mirroring the graph-first modeling the rest of the
    app uses, so this workflow's output is queryable alongside everything else."""
    if not (NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD):
        logger.warning("Neo4j credentials not configured — skipping graph write")
        return {"logged": False, "reason": "neo4j not configured"}

    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with driver.session() as session:
            await session.run(
                """MERGE (u:User {id:$userId})
                   CREATE (r:PipelineRun {
                       id: $runId, summary: $summary,
                       source: 'render_workflow', createdAt: $now
                   })
                   CREATE (u)-[:RAN_WORKFLOW]->(r)""",
                {"userId": user_id, "runId": run_id, "summary": summary, "now": now},
            )
            for stage in stages:
                await session.run(
                    """MATCH (r:PipelineRun {id:$runId})
                       CREATE (s:PipelineStage {
                           name: $name, status: $status, ms: $ms, createdAt: $now
                       })
                       CREATE (r)-[:HAS_STAGE]->(s)""",
                    {
                        "runId": run_id, "name": stage["name"],
                        "status": stage["status"], "ms": stage.get("ms", 0), "now": now,
                    },
                )
        return {"logged": True, "runId": run_id, "stagesLogged": len(stages)}
    except Exception as exc:
        logger.error(f"Neo4j pipeline run log failed: {exc}")
        return {"logged": False, "reason": str(exc)}
    finally:
        await driver.close()
