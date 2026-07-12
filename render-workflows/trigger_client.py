"""Example: trigger a run of the deployed Render Workflow from another
service — e.g. the main SynapseTwin API — and wait for its result.

Requires:
  pip install render_sdk
  export RENDER_API_KEY=rnd_xxx        # from Render Dashboard → Account Settings → API Keys

Replace "synapsetwin-workflow" below with your actual workflow service slug,
visible on the workflow's page in the Render Dashboard.
"""
import asyncio
import time

from render_sdk import RenderAsync, Render

TASK_SLUG = "synapsetwin-workflow/run_digital_twin_pipeline"


async def trigger_async(text: str, user_id: str) -> dict:
    """Use this from an async app (e.g. FastAPI, like the main SynapseTwin API)."""
    render = RenderAsync()
    started_run = await render.workflows.start_task(TASK_SLUG, [text, user_id])
    print(f"Task run started: {started_run.id} (status: {started_run.status})")

    finished_run = await started_run
    print(f"Task run completed: {finished_run.id} (status: {finished_run.status})")
    return finished_run.output


def trigger_sync(text: str, user_id: str) -> dict:
    """Use this from a synchronous app (e.g. a script, Flask, Django)."""
    render = Render()
    started_run = render.workflows.start_task(TASK_SLUG, [text, user_id])
    print(f"Task run started: {started_run.id} (status: {started_run.status})")

    while True:
        run = started_run.get()
        if run.status in ("succeeded", "failed", "canceled"):
            print(f"Task run finished: {run.id} (status: {run.status})")
            return run.output
        time.sleep(2)


if __name__ == "__main__":
    # Example FastAPI-style integration for the main app:
    #
    #   from trigger_client import trigger_async
    #
    #   @router.post("/api/agent/pipeline-run")
    #   async def run_via_render_workflow(body: PipelineBody, current_user=Depends(require_auth)):
    #       result = await trigger_async(body.text, current_user["userId"])
    #       return {"success": True, **result}
    #
    output = asyncio.run(trigger_async(
        "I slept 5 hours and had 3 back-to-back meetings today, feeling drained.",
        "demo-user-001",
    ))
    print("Pipeline output:", output)
