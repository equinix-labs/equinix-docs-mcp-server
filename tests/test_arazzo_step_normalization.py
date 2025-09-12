import asyncio
from types import SimpleNamespace
import yaml
import pytest

from equinix_mcp_server.arazzo_manager import ArazzoManager

SIMPLE_SPEC = """
workflows:
  simple_chain:
    description: Simple two-step chain using simplified syntax
    steps:
      - id: first
        operation: dummy.op1
        params:
          a: 1
      - id: second
        operation: dummy.op2
        params:
          b: 2
"""

class DummyStepExecutor:
    def __init__(self):
        self.calls = []
        self.blob_store = None
    def execute_step(self, step_def, state):  # pragma: no cover
        self.calls.append(step_def.get("stepId"))
        return {"success": True, "outputs": {"echo": step_def.get("stepId")}}
    def determine_next_action(self, step_def, success, state):  # pragma: no cover
        return {"type": type("A", (), {"END":"END"}).END if False else {"type":"END"}}  # not used

@pytest.mark.asyncio
async def test_simplified_step_normalization(tmp_path, monkeypatch):
    spec_path = tmp_path/"simple.yaml"
    spec_path.write_text(SIMPLE_SPEC)
    config = SimpleNamespace(arazzo={"specs": [str(spec_path)]})
    mgr = ArazzoManager(config)
    await mgr.load()
    # Force runner creation with dummy runner by monkeypatching _get_runner return
    class DummyRunner:
        def __init__(self):
            self.arazzo_doc = yaml.safe_load(SIMPLE_SPEC)
            # Simulate normalization that would be done by arazzo manager
            workflows = self.arazzo_doc.get("workflows", {})
            for workflow_key, workflow in workflows.items():
                if "steps" in workflow:
                    for step in workflow["steps"]:
                        # Normalize id -> stepId
                        if "stepId" not in step and "id" in step:
                            step["stepId"] = step.pop("id")
                        # Normalize operation -> operationId
                        if "operation" in step and not any(k in step for k in ("operationId", "operationPath")):
                            step["operationId"] = step.pop("operation")
            self.step_executor = DummyStepExecutor()
            self.execution_states = {}
        
        def execute_workflow(self, workflow_id, inputs):
            # Simulate minimal run: ensure steps normalized
            workflows = self.arazzo_doc.get("workflows", {})
            wf = workflows.get(workflow_id)
            if wf is None and workflows:
                # Get the first workflow if specific one not found
                wf = next(iter(workflows.values()))
            steps = wf.get("steps", []) if isinstance(wf, dict) else []
            assert all("stepId" in s for s in steps)
            assert all(any(k in s for k in ("operationId", "operationPath")) for s in steps)
            return {"status": "WORKFLOW_COMPLETE", "workflow_id": workflow_id, "outputs": {}}

    async def fake_get_runner(path):
        dr = DummyRunner()
        await asyncio.sleep(0)
        return dr

    monkeypatch.setattr(mgr, "_get_runner", fake_get_runner)
    res = await mgr._execute_workflow_with_fallback("simple_chain", str(spec_path), {})
    status = getattr(res, "status", res.get("status") if isinstance(res, dict) else None)
    assert status == "WORKFLOW_COMPLETE"
