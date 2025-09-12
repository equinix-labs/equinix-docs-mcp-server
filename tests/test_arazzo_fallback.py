from types import SimpleNamespace

import pytest

from equinix_mcp_server.arazzo_manager import ArazzoManager, WorkflowMeta


class DummyStepExecutor:
    def execute_step(self, step_def, state):  # pragma: no cover - simple logic
        # Extract substituted value
        params = step_def.get("parameters", [])
        val = None
        if params and isinstance(params, list):
            v = params[0].get("value")
            try:
                val = int(v)
            except Exception:
                val = v
        # Return plus one output to test aggregation
        if isinstance(val, int):
            return {"success": True, "outputs": {"valPlusOne": val + 1}}
        return {"success": True, "outputs": {"valPlusOne": val}}


class DummyRunner:
    def __init__(self):
        self.step_executor = DummyStepExecutor()


def make_manager():
    config = SimpleNamespace(arazzo={"specs": []})
    mgr = ArazzoManager(config)
    # Inject synthetic spec doc with simple forEach workflow
    mgr._spec_docs["dummy.yaml"] = {
        "workflows": [
            {
                "workflowId": "loopTest",
                "steps": [
                    {
                        "stepId": "loop",
                        "forEach": "$inputs.items",
                        "steps": [
                            {
                                "stepId": "echo",
                                "operationId": "Dummy.echo",
                                "parameters": [
                                    {
                                        "name": "itemVal",
                                        "in": "query",
                                        "value": "$item.val",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "outputs": {"results": "$steps.loop.outputs.valPlusOne"},
            }
        ]
    }
    # Register minimal metadata so wrapper path works (skip load parsing)
    if "loopTest" not in mgr.workflows:
        mgr.workflows["loopTest"] = WorkflowMeta(
            workflow_id="loopTest",
            spec_path="dummy.yaml",
            summary="",
            description="",
            input_properties={"items": {"type": "array"}},
            required=[],
        )
    return mgr


@pytest.mark.asyncio
async def test_foreach_fallback_aggregation():
    mgr = make_manager()
    fake_runner = DummyRunner()
    inputs = {"items": [{"val": 1}, {"val": 2}, {"val": 5}]}
    # Force fallback path directly
    result = await mgr._execute_with_foreach(
        fake_runner, "loopTest", "dummy.yaml", inputs
    )
    assert result.outputs["results"] == [2, 3, 6]
    # Ensure step_outputs captured aggregated list
    assert "loop" in result.step_outputs
    assert result.step_outputs["loop"]["valPlusOne"] == [2, 3, 6]
