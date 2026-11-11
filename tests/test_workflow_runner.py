"""Tests for running workflows: thread offload, HTTP behaviour, failures,
the time limit and read-only registration."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import yaml
from arazzo_runner.models import ExecutionState, WorkflowExecutionStatus
from fastmcp import Client, FastMCP
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from equinix_docs_mcp_server import api_notices
from equinix_docs_mcp_server import arazzo_manager as am
from equinix_docs_mcp_server.arazzo_manager import (
    ArazzoManager,
    workflow_write_reasons,
)
from equinix_docs_mcp_server.workflow_http import (
    WorkflowCancelled,
    WorkflowDeadlineExceeded,
    WorkflowSession,
)

BASE = "https://api.example.test"


class FakeAPI(BaseAdapter):
    """A requests adapter that answers from ``handler(request)``."""

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.requests: list[requests.PreparedRequest] = []
        self.timeouts: list = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.timeouts.append(kwargs.get("timeout"))
        status, headers, body = self.handler(request)
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(
            {"Content-Type": "application/json", **(headers or {})}
        )
        response._content = json.dumps(body if body is not None else {}).encode()
        response.encoding = "utf-8"
        response.request = request
        response.url = request.url
        return response

    def close(self):
        pass


def replies(*answers):
    """A handler that gives the answers in order, then repeats the last."""
    queue = list(answers)

    def handler(request):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handler


def make_session(api: FakeAPI, **kwargs) -> WorkflowSession:
    waits: list[float] = []
    session = WorkflowSession(
        sleep=kwargs.pop("sleep", waits.append),
        **{"max_retries": 5, "max_wait": 15, "timeout": 60, **kwargs},
    )
    session.waits = waits  # type: ignore[attr-defined]
    session.mount("https://", api)
    return session


class FakeAuth:
    """An async AuthManager that hands out t1, then t2 after a cache clear."""

    def __init__(self):
        self.generation = 1
        self.cleared = 0
        self.loop_threads: list[str] = []

    async def _get_client_credentials_auth_header(self):
        self.loop_threads.append(threading.current_thread().name)
        return {"Authorization": f"Bearer t{self.generation}"}

    def clear_token_cache(self):
        self.cleared += 1
        self.generation += 1


# HTTP behaviour


@pytest.mark.asyncio
async def test_401_clears_the_token_and_retries_once():
    auth = FakeAuth()

    def handler(request):
        ok = request.headers.get("Authorization") == "Bearer t2"
        return (200, {}, {"ok": True}) if ok else (401, {}, {})

    api = FakeAPI(handler)
    session = make_session(api, loop=asyncio.get_running_loop(), auth_manager=auth)

    response = await asyncio.to_thread(session.get, f"{BASE}/things")

    assert response.status_code == 200
    assert [r.headers["Authorization"] for r in api.requests] == [
        "Bearer t1",
        "Bearer t2",
    ]
    assert auth.cleared == 1
    # The AuthManager ran on the event loop, not in the worker thread
    assert threading.current_thread().name in auth.loop_threads


@pytest.mark.asyncio
async def test_callers_own_authorization_is_kept():
    """A bearerToken the caller passed is sent as is and not renewed."""
    auth = FakeAuth()
    api = FakeAPI(replies((401, {}, {})))
    session = make_session(api, loop=asyncio.get_running_loop(), auth_manager=auth)

    response = await asyncio.to_thread(
        session.get, f"{BASE}/things", headers={"Authorization": "Bearer mine"}
    )

    assert response.status_code == 401
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer mine"]
    assert auth.cleared == 0


@pytest.mark.asyncio
async def test_injected_bearer_token_is_renewed_after_401():
    """A bearerToken the server filled in is replaced like its own header."""
    auth = FakeAuth()

    def handler(request):
        ok = request.headers.get("Authorization") == "Bearer t2"
        return (200, {}, {}) if ok else (401, {}, {})

    api = FakeAPI(handler)
    session = make_session(
        api,
        loop=asyncio.get_running_loop(),
        auth_manager=auth,
        managed_tokens=["t0"],
    )

    response = await asyncio.to_thread(
        session.get, f"{BASE}/things", headers={"Authorization": "Bearer t0"}
    )

    assert response.status_code == 200
    assert api.requests[0].headers["Authorization"] == "Bearer t1"


def test_429_waits_for_retry_after_then_succeeds():
    api = FakeAPI(replies((429, {"Retry-After": "2"}, {}), (200, {}, {"ok": True})))
    session = make_session(api)

    response = session.get(f"{BASE}/things")

    assert response.status_code == 200
    assert session.waits == [2.0]  # type: ignore[attr-defined]
    assert len(api.requests) == 2


def test_429_beyond_the_wait_budget_is_returned():
    api = FakeAPI(replies((429, {"Retry-After": "2"}, {})))
    session = make_session(api, max_wait=3)

    response = session.post(f"{BASE}/things", json={})

    assert response.status_code == 429
    # One 2s wait fits the 3s budget; a second would not
    assert session.waits == [2.0]  # type: ignore[attr-defined]
    assert len(api.requests) == 2


def test_503_is_not_resent_for_post():
    api = FakeAPI(replies((503, {}, {})))
    session = make_session(api)

    assert session.post(f"{BASE}/things", json={}).status_code == 503
    assert len(api.requests) == 1


def test_requests_get_a_timeout():
    api = FakeAPI(replies((200, {}, {})))
    session = make_session(api, request_timeout=7)

    session.get(f"{BASE}/things")

    assert api.timeouts == [7]


def test_deprecation_headers_are_recorded_for_the_tool_call():
    api = FakeAPI(
        replies((200, {"Deprecation": "@1700000000", "Sunset": "@1900000000"}, {}))
    )
    session = make_session(api)
    notices: list = []
    token = api_notices._notices.set(notices)
    try:
        session.get(f"{BASE}/things?page=2")
    finally:
        api_notices._notices.reset(token)

    assert len(notices) == 1
    assert notices[0].deprecated
    assert notices[0].url == f"{BASE}/things"


def test_session_refuses_requests_after_the_deadline():
    now = [0.0]
    api = FakeAPI(replies((200, {}, {})))
    session = make_session(api, timeout=10, clock=lambda: now[0])

    session.get(f"{BASE}/things")
    now[0] = 11.0
    with pytest.raises(WorkflowDeadlineExceeded):
        session.get(f"{BASE}/things")
    assert len(api.requests) == 1


def test_request_timeout_is_capped_by_the_deadline():
    now = [0.0]
    api = FakeAPI(replies((200, {}, {})))
    session = make_session(api, timeout=10, request_timeout=30, clock=lambda: now[0])
    now[0] = 6.0

    session.get(f"{BASE}/things")

    assert api.timeouts == [4.0]


def test_cancelled_session_refuses_requests():
    api = FakeAPI(replies((200, {}, {})))
    session = make_session(api)
    session.cancel()

    with pytest.raises(WorkflowCancelled):
        session.get(f"{BASE}/things")
    assert api.requests == []


# Running workflows

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Things", "version": "1"},
    "servers": [{"url": BASE}],
    "paths": {
        "/things": {
            "get": {"operationId": "getThings", "responses": {"200": {}}},
            "post": {"operationId": "createThing", "responses": {"201": {}}},
        },
        "/things/search": {
            "post": {"operationId": "searchThings", "responses": {"200": {}}}
        },
        "/things/{id}": {
            "get": {"operationId": "getThing", "responses": {"200": {}}},
            "delete": {"operationId": "deleteThing", "responses": {"204": {}}},
        },
    },
}

GET_THINGS = {
    "stepId": "get_things",
    "operationId": "getThings",
    "successCriteria": [{"condition": "$statusCode == 200"}],
    "outputs": {"things": "$response.body#/data"},
}
GET_THING = {
    "stepId": "get_thing",
    "operationId": "getThing",
    "parameters": [{"name": "id", "in": "path", "value": "1"}],
    "successCriteria": [{"condition": "$statusCode == 200"}],
}


def write_spec(tmp_path: Path, workflows: list) -> Path:
    (tmp_path / "openapi.yaml").write_text(yaml.safe_dump(OPENAPI))
    spec = tmp_path / "workflows.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "arazzo": "1.0.1",
                "info": {"title": "Test", "version": "1.0.0"},
                "sourceDescriptions": [
                    {
                        "name": "things",
                        "url": str(tmp_path / "openapi.yaml"),
                        "type": "openapi",
                    }
                ],
                "workflows": workflows,
            }
        )
    )
    return spec


async def load_manager(spec: Path) -> ArazzoManager:
    mgr = ArazzoManager(SimpleNamespace(arazzo={"specs": [str(spec)]}))
    await mgr.load()
    return mgr


@pytest.fixture
def fake_api(monkeypatch):
    """Route the sessions the workflow tools create to a FakeAPI."""
    holder: dict = {}

    class Session(WorkflowSession):
        def __init__(self, **kwargs):
            super().__init__(sleep=lambda s: None, **kwargs)
            self.mount("https://", holder["api"])

    monkeypatch.setattr(am, "WorkflowSession", Session)

    def install(handler):
        holder["api"] = FakeAPI(handler)
        return holder["api"]

    return install


async def call(mgr: ArazzoManager, tool: str, arguments=None):
    mcp = FastMCP("test")
    await mgr.register_with_fastmcp(mcp)
    async with Client(mcp) as client:
        return await client.call_tool(tool, arguments or {}, raise_on_error=False)


def text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


@pytest.mark.asyncio
async def test_workflow_returns_its_outputs(tmp_path, fake_api):
    spec = write_spec(
        tmp_path,
        [
            {
                "workflowId": "list_things",
                "steps": [GET_THINGS],
                "outputs": {"things": "$steps.get_things.outputs.things"},
            }
        ],
    )
    api = fake_api(replies((200, {}, {"data": [1, 2]})))
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__list_things")

    assert not result.is_error
    assert result.structured_content == {"things": [1, 2]}
    assert [r.url for r in api.requests] == [f"{BASE}/things"]


@pytest.mark.asyncio
async def test_failed_success_criteria_is_a_tool_error(tmp_path, fake_api):
    spec = write_spec(
        tmp_path,
        [{"workflowId": "two_steps", "steps": [GET_THINGS, GET_THING]}],
    )
    api = fake_api(replies((500, {}, {"error": "boom"})))
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__two_steps")

    assert result.is_error
    message = text(result)
    assert "failed at step 'get_things'" in message
    assert "successCriteria were not met" in message
    assert "GET /things, returned HTTP 500" in message
    # The second step never ran
    assert len(api.requests) == 1


@pytest.mark.asyncio
async def test_step_request_error_is_a_tool_error(tmp_path, fake_api):
    """arazzo-runner's execute_workflow would skip past a step that raises."""
    spec = write_spec(
        tmp_path,
        [{"workflowId": "two_steps", "steps": [GET_THINGS, GET_THING]}],
    )

    def handler(request):
        raise requests.ConnectionError("connection refused")

    api = fake_api(handler)
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__two_steps")

    assert result.is_error
    assert "failed at step 'get_things'" in text(result)
    assert "connection refused" in text(result)
    assert len(api.requests) == 1


def retrying_step(limit: int) -> dict:
    return {
        **GET_THINGS,
        "onFailure": [
            {"name": "again", "type": "retry", "retryAfter": 0, "retryLimit": limit}
        ],
    }


@pytest.mark.asyncio
async def test_retry_action_reruns_the_step(tmp_path, fake_api):
    spec = write_spec(
        tmp_path,
        [
            {
                "workflowId": "poll",
                "steps": [retrying_step(2)],
                "outputs": {"things": "$steps.get_things.outputs.things"},
            }
        ],
    )
    api = fake_api(replies((500, {}, {}), (500, {}, {}), (200, {}, {"data": [1]})))
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__poll")

    assert not result.is_error, text(result)
    assert result.structured_content == {"things": [1]}
    assert len(api.requests) == 3


@pytest.mark.asyncio
async def test_retry_action_gives_up_after_its_limit(tmp_path, fake_api):
    spec = write_spec(tmp_path, [{"workflowId": "poll", "steps": [retrying_step(2)]}])
    api = fake_api(replies((500, {}, {})))
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__poll")

    assert result.is_error
    assert "still not met after 2 retries" in text(result)
    assert len(api.requests) == 3


@pytest.mark.asyncio
async def test_time_limit_stops_the_run_and_names_the_step(
    tmp_path, fake_api, monkeypatch
):
    monkeypatch.setenv("EQUINIX_MCP_WORKFLOW_TIMEOUT", "0.2")
    spec = write_spec(
        tmp_path,
        [{"workflowId": "two_steps", "steps": [GET_THINGS, GET_THING]}],
    )

    def slow(request):
        threading.Event().wait(0.3)
        return 200, {}, {"data": []}

    api = fake_api(slow)
    mgr = await load_manager(spec)

    result = await call(mgr, "workflow__two_steps")

    assert result.is_error
    message = text(result)
    assert "stopped at step 'get_thing'" in message
    assert "0.2s time limit" in message
    assert "may already have been applied" in message
    assert len(api.requests) == 1


@pytest.mark.asyncio
async def test_workflow_runs_off_the_event_loop():
    """While a workflow runs, the event loop keeps serving other work."""
    release = threading.Event()
    ran_in: list[str] = []

    class SlowRunner:
        arazzo_doc = {"workflows": [{"workflowId": "slow", "steps": []}]}

        def __init__(self):
            self.execution_states = {}

        def start_workflow(self, workflow_id, inputs):
            self.execution_states["run"] = ExecutionState(workflow_id=workflow_id)
            return "run"

        def execute_next_step(self, execution_id):
            ran_in.append(threading.current_thread().name)
            # Blocks for 5s unless the event loop is free to release it
            release.wait(5)
            return {"status": WorkflowExecutionStatus.WORKFLOW_COMPLETE}

    mgr = ArazzoManager(SimpleNamespace(arazzo={"specs": []}))

    async def get_runner(spec_path, http_client=None):
        return SlowRunner()

    mgr._get_runner = get_runner  # type: ignore[method-assign]
    task = asyncio.create_task(mgr._execute_workflow_with_fallback("slow", "x", {}))
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    result = await asyncio.wait_for(task, 1)

    assert result.status == WorkflowExecutionStatus.WORKFLOW_COMPLETE
    assert ran_in and ran_in[0] != threading.current_thread().name


@pytest.mark.asyncio
async def test_foreach_nested_step_failure_is_an_error():
    class Executor:
        def execute_step(self, step_def, state):
            return {"success": False, "outputs": {}}

    mgr = ArazzoManager(SimpleNamespace(arazzo={"specs": []}))
    mgr._spec_docs["loop.yaml"] = {
        "workflows": [
            {
                "workflowId": "loop",
                "steps": [
                    {
                        "stepId": "each",
                        "forEach": "$inputs.items",
                        "steps": [{"stepId": "echo", "operationId": "echo"}],
                    }
                ],
            }
        ]
    }

    with pytest.raises(am.WorkflowStepFailed, match="'echo'"):
        await mgr._execute_with_foreach(
            SimpleNamespace(step_executor=Executor()),
            "loop",
            "loop.yaml",
            {"items": [{"v": 1}]},
        )


# Read-only classification


def doc_with(*steps, extra_workflows=()) -> dict:
    return {"workflows": [{"workflowId": "wf", "steps": list(steps)}, *extra_workflows]}


SOURCES = {"things": OPENAPI}


def test_get_and_search_post_steps_are_read_only():
    doc = doc_with(
        {"stepId": "a", "operationId": "getThings"},
        {"stepId": "b", "operationId": "$sourceDescriptions.things.searchThings"},
        {
            "stepId": "c",
            "operationPath": "{$sourceDescriptions.things.url}#/paths/~1things~1{id}/get",
        },
    )
    assert workflow_write_reasons("wf", doc, SOURCES) == []


def test_write_steps_are_reported():
    doc = doc_with(
        {"stepId": "a", "operationId": "getThings"},
        {"stepId": "b", "operationId": "things.createThing"},
        {
            "stepId": "c",
            "operationPath": "{$sourceDescriptions.things.url}#/paths/~1things~1{id}/delete",
        },
    )
    assert workflow_write_reasons("wf", doc, SOURCES) == [
        "step 'b' calls POST /things (createThing)",
        "step 'c' calls DELETE /things/{id} (deleteThing)",
    ]


def test_foreach_and_nested_workflow_steps_are_checked():
    doc = doc_with(
        {
            "stepId": "loop",
            "forEach": "$inputs.items",
            "steps": [{"stepId": "inner", "operationId": "deleteThing"}],
        },
        {"stepId": "sub", "workflowId": "other"},
        extra_workflows=[
            {
                "workflowId": "other",
                "steps": [{"stepId": "make", "operationId": "createThing"}],
            }
        ],
    )
    assert workflow_write_reasons("wf", doc, SOURCES) == [
        "step 'inner' calls DELETE /things/{id} (deleteThing)",
        "step 'make' calls POST /things (createThing)",
    ]


def test_unresolved_operation_is_not_read_only():
    doc = doc_with({"stepId": "a", "operationId": "nowhere"})
    assert workflow_write_reasons("wf", doc, SOURCES) == [
        "step 'a': operation 'nowhere' was not found"
    ]


def test_bundled_workflow_is_read_only():
    bundled = (
        Path(am.__file__).parent / "data" / "config" / "workflows.yaml"
    ).read_text()
    ne = {"paths": {"/ne/v1/metros": {"get": {"operationId": "getMetros"}}}}
    assert (
        workflow_write_reasons("list_ne_metros", yaml.safe_load(bundled), {"ne": ne})
        == []
    )


WRITE_WORKFLOWS = [
    {"workflowId": "list_things", "steps": [GET_THINGS]},
    {
        "workflowId": "make_thing",
        "steps": [{"stepId": "make", "operationId": "createThing"}],
    },
]


@pytest.mark.asyncio
async def test_write_workflows_are_skipped_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("EQUINIX_MCP_ALLOW_WRITE_WORKFLOWS", raising=False)
    mgr = await load_manager(write_spec(tmp_path, WRITE_WORKFLOWS))
    mcp = FastMCP("test")
    await mgr.register_with_fastmcp(mcp)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    assert "workflow__make_thing" not in tools
    assert mgr.skipped_workflows == {
        "make_thing": ["step 'make' calls POST /things (createThing)"]
    }
    hints = tools["workflow__list_things"].annotations
    assert hints.read_only_hint is True
    assert hints.open_world_hint is True
    assert hints.destructive_hint is False


@pytest.mark.asyncio
async def test_write_workflows_can_be_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINIX_MCP_ALLOW_WRITE_WORKFLOWS", "1")
    mgr = await load_manager(write_spec(tmp_path, WRITE_WORKFLOWS))
    mcp = FastMCP("test")
    await mgr.register_with_fastmcp(mcp)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    hints = tools["workflow__make_thing"].annotations
    assert hints.read_only_hint is False
    assert hints.open_world_hint is True
    assert mgr.skipped_workflows == {}


@pytest.mark.asyncio
async def test_workflow_with_unloadable_sources_is_skipped(tmp_path):
    spec = tmp_path / "workflows.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "arazzo": "1.0.1",
                "sourceDescriptions": [
                    {"name": "x", "url": str(tmp_path / "missing.yaml")}
                ],
                "workflows": [{"workflowId": "wf", "steps": [GET_THINGS]}],
            }
        )
    )
    mgr = ArazzoManager(
        SimpleNamespace(
            arazzo={"specs": [str(spec)]}, resolve_path=lambda p: tmp_path / p
        )
    )
    await mgr.load()
    await mgr.register_with_fastmcp(FastMCP("test"))

    assert mgr.workflows == {}
    assert "could not be loaded" in mgr.skipped_workflows["wf"][0]
