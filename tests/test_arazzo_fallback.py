from types import SimpleNamespace

import pytest

from equinix_docs_mcp_server.arazzo_manager import ArazzoManager, WorkflowMeta


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


@pytest.mark.asyncio
async def test_workflow_summary_becomes_tool_title(tmp_path):
    """A workflow's Arazzo summary is its tool title; without one, none is set."""
    from fastmcp import Client, FastMCP

    spec = tmp_path / "workflows.yaml"
    spec.write_text(
        "arazzo: 1.0.1\n"
        "info: {title: Test, version: 1.0.0}\n"
        "workflows:\n"
        "  - workflowId: with_summary\n"
        "    summary: Retrieve things\n"
        "    description: Retrieve every thing, one page at a time.\n"
        "    steps: []\n"
        "  - workflowId: description_only\n"
        "    description: Retrieve other things\n"
        "    steps: []\n"
    )
    mgr = ArazzoManager(SimpleNamespace(arazzo={"specs": [str(spec)]}))
    await mgr.load()

    assert mgr.workflows["with_summary"].summary == "Retrieve things"
    assert mgr.workflows["description_only"].summary == ""

    mcp = FastMCP("test")
    await mgr.register_with_fastmcp(mcp)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    assert tools["workflow__with_summary"].title == "Retrieve things"
    assert tools["workflow__description_only"].description == "Retrieve other things"


def write_workflow(tmp_path, source_url):
    spec = tmp_path / "workflows.yaml"
    spec.write_text(
        "arazzo: 1.0.1\n"
        "info: {title: Test, version: 1.0.0}\n"
        "sourceDescriptions:\n"
        f"  - {{name: ne, url: '{source_url}', type: openapi}}\n"
        "workflows:\n"
        "  - workflowId: list_things\n"
        "    steps:\n"
        "      - {stepId: get, operationId: getThings}\n"
    )
    return spec


@pytest.mark.asyncio
async def test_runner_loads_yaml_source_regardless_of_content_type(tmp_path):
    """Sources are parsed as YAML (which covers JSON), not chosen by Content-Type."""
    spec = write_workflow(tmp_path, "https://docs.example.com/openapi.yaml")
    mgr = ArazzoManager(SimpleNamespace(arazzo={"specs": [str(spec)]}))
    await mgr.load()

    openapi = "openapi: 3.0.3\npaths:\n  /things:\n    get: {operationId: getThings}\n"
    real_read = mgr._read_spec

    async def read(path_or_url):
        if path_or_url.startswith("https://"):
            return openapi  # what an octet-stream YAML response body looks like
        return await real_read(path_or_url)

    mgr._read_spec = read
    runner = await mgr._get_runner(str(spec))

    assert runner.source_descriptions["ne"]["paths"]["/things"]["get"] == {
        "operationId": "getThings"
    }
    assert runner.arazzo_doc["workflows"][0]["workflowId"] == "list_things"


@pytest.mark.asyncio
async def test_runner_init_failure_is_reported(tmp_path):
    """A source that can't be loaded surfaces its error, not 'not installed'."""
    spec = write_workflow(tmp_path, str(tmp_path / "missing.yaml"))
    mgr = ArazzoManager(
        SimpleNamespace(
            arazzo={"specs": [str(spec)]}, resolve_path=lambda p: tmp_path / p
        )
    )
    await mgr.load()

    with pytest.raises(RuntimeError, match="Could not initialize the Arazzo runner"):
        await mgr._get_runner(str(spec))
