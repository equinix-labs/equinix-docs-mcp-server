"""Arazzo workflow specification manager.

Integrates with the `arazzo-runner` engine to expose workflows as FastMCP tools.

Responsibilities:
1. Load one or more Arazzo spec files listed under `config.arazzo.specs`.
2. Parse workflow metadata (list form with `workflowId` or mapping form).
3. Register per-workflow tools (`workflow__<workflowId>`) with dynamically
     generated parameters based on the workflow input schema.
4. Provide utility tools: `arazzo_list_workflows`, `arazzo_describe_workflow`.
5. Auto-inject OAuth bearer tokens using `AuthManager` when a workflow expects
     an input named `bearerToken` and the caller did not supply one.
6. Register only read-only workflows unless `EQUINIX_MCP_ALLOW_WRITE_WORKFLOWS`
     is set.
7. Run each workflow in a worker thread, so it doesn't hold up the event
     loop, with a `WorkflowSession` that gives its requests the API tools'
     auth refresh, retries and notices, and a time limit for the whole run.

Notes:
- Each workflow tool accepts explicit parameters (derived from JSON Schema
    `inputs.properties`) plus a catch‑all `inputs_json` (YAML/JSON object string).
- Explicit params override `inputs_json` keys.
- A failed step, or a run that passes its time limit, is a tool error that
    names the step.
- If `arazzo-runner` is missing, execution raises an instructive error but
    listing/description still works.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from fastmcp.exceptions import ToolError
from mcp_types import ToolAnnotations

from .openapi_annotations import is_read_operation_name
from .workflow_http import WORKFLOW_TIMEOUT_ENV, WorkflowSession, WorkflowStopped

logger = logging.getLogger(__name__)

try:  # Optional import; execution requires it.
    from arazzo_runner import ArazzoRunner  # type: ignore
    from arazzo_runner.models import (  # type: ignore
        ExecutionState,
        WorkflowExecutionResult,
        WorkflowExecutionStatus,
    )
except Exception:  # pragma: no cover
    ArazzoRunner = None  # type: ignore

ALLOW_WRITE_WORKFLOWS_ENV = "EQUINIX_MCP_ALLOW_WRITE_WORKFLOWS"
READ_METHODS = {"get", "head", "options"}
HTTP_METHODS = {"get", "head", "options", "post", "put", "patch", "delete", "trace"}
_SOURCE_REF = re.compile(r"\$sourceDescriptions\.([^.}#]+)")


def allow_write_workflows() -> bool:
    """Whether workflows with write steps may be registered."""
    return os.getenv(ALLOW_WRITE_WORKFLOWS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class WorkflowMeta:
    workflow_id: str
    spec_path: str
    summary: str = ""
    description: str = ""
    input_properties: Optional[Dict[str, Dict[str, Any]]] = None
    required: Optional[List[str]] = None
    # Set at registration: whether every step only reads
    read_only: Optional[bool] = None


class WorkflowStepFailed(Exception):
    """A step failed: its successCriteria were not met or its request failed."""

    def __init__(
        self,
        step_id: str,
        reason: str,
        last_response: Optional[tuple[str, str, int]] = None,
    ):
        super().__init__(f"Step '{step_id}' failed: {reason}")
        self.step_id = step_id
        self.reason = reason
        self.last_response = last_response


class WorkflowTimedOut(Exception):
    """The run stopped at a step: it passed its time limit or was cancelled."""

    def __init__(self, step_id: str, reason: str):
        super().__init__(f"Stopped at step '{step_id}': {reason}")
        self.step_id = step_id
        self.reason = reason


# Read-only classification


def _find_operation(
    sources: Dict[str, Any], names: List[str], match: Any
) -> Optional[tuple[str, str, str]]:
    for name in names:
        source = sources.get(name)
        paths = source.get("paths") if isinstance(source, dict) else None
        for path, item in (paths or {}).items():
            for method, op in (item or {}).items():
                if (
                    isinstance(method, str)
                    and method.lower() in HTTP_METHODS
                    and isinstance(op, dict)
                    and match(path, method.lower(), op)
                ):
                    return method.lower(), path, op.get("operationId") or ""
    return None


def resolve_operation(
    step: Dict[str, Any], sources: Dict[str, Any]
) -> Optional[tuple[str, str, str]]:
    """(method, path, operationId) of a step's operation, or None.

    ``operationId`` may name its source (``$sourceDescriptions.ne.getMetros``
    or ``ne.getMetros``); ``operationPath`` is a JSON pointer such as
    ``{$sourceDescriptions.ne.url}#/paths/~1metros/get``.
    """
    operation_id = step.get("operationId")
    if isinstance(operation_id, str) and operation_id:
        names = list(sources)
        ref = _SOURCE_REF.match(operation_id)
        if ref:
            names = [ref.group(1)]
            operation_id = operation_id[ref.end() :].lstrip(".")
        elif "." in operation_id and operation_id.split(".", 1)[0] in sources:
            name, operation_id = operation_id.split(".", 1)
            names = [name]
        return _find_operation(
            sources, names, lambda p, m, op: op.get("operationId") == operation_id
        )

    operation_path = step.get("operationPath")
    if isinstance(operation_path, str) and "#" in operation_path:
        prefix, _, pointer = operation_path.partition("#")
        tokens = [
            t.replace("~1", "/").replace("~0", "~")
            for t in pointer.lstrip("/").split("/")
        ]
        if len(tokens) != 3 or tokens[0] != "paths":
            return None
        _, want_path, want_method = tokens
        ref = _SOURCE_REF.search(prefix)
        names = [ref.group(1)] if ref else list(sources)
        return _find_operation(
            sources,
            names,
            lambda p, m, op: p == want_path and m == want_method.lower(),
        )
    return None


def workflow_write_reasons(
    workflow_id: str,
    arazzo_doc: Dict[str, Any],
    sources: Dict[str, Any],
    _seen: Optional[set] = None,
) -> List[str]:
    """Why a workflow is not read-only; empty when every step only reads.

    A step reads when its operation is a GET, HEAD or OPTIONS, or a POST
    whose name marks it as a search (``is_read_operation_name``). Steps in
    ``forEach`` loops, and workflows run as steps, are checked too. A step
    whose operation can't be found counts against the workflow.
    """
    seen = _seen if _seen is not None else set()
    if workflow_id in seen:
        return []
    seen.add(workflow_id)
    workflow = next(
        (
            wf
            for wf in arazzo_doc.get("workflows") or []
            if isinstance(wf, dict) and wf.get("workflowId") == workflow_id
        ),
        None,
    )
    if workflow is None:
        return [f"workflow '{workflow_id}' was not found"]

    reasons: List[str] = []

    def check(steps: Any) -> None:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            step_id = step.get("stepId") or step.get("id") or "?"
            if "forEach" in step:
                check(step.get("steps"))
                continue
            if "workflowId" in step:
                target = step["workflowId"]
                if not isinstance(target, str) or target.startswith("$"):
                    reasons.append(
                        f"step '{step_id}' runs workflow {target!r}, which "
                        "can't be checked"
                    )
                else:
                    reasons.extend(
                        workflow_write_reasons(target, arazzo_doc, sources, seen)
                    )
                continue
            resolved = resolve_operation(step, sources)
            if resolved is None:
                ref = step.get("operationId") or step.get("operationPath")
                reasons.append(f"step '{step_id}': operation {ref!r} was not found")
                continue
            method, path, op_id = resolved
            if method in READ_METHODS or is_read_operation_name(method, path, op_id):
                continue
            reasons.append(
                f"step '{step_id}' calls {method.upper()} {path}"
                + (f" ({op_id})" if op_id else "")
            )

    check(workflow.get("steps"))
    return reasons


# Spec normalization


def _normalize_workflows(doc: Dict[str, Any]) -> bool:
    """Turn mapping-form workflows into the list form. True if changed."""
    wfs = doc.get("workflows")
    if isinstance(wfs, dict):
        doc["workflows"] = [
            {"workflowId": k, **(v if isinstance(v, dict) else {})}
            for k, v in wfs.items()
        ]
        return True
    return False


def _normalize_steps(doc: Dict[str, Any]) -> bool:
    """Accept simplified step syntax (id, operation, params). True if changed."""
    mutated = False
    for wf in doc.get("workflows", []) or []:
        if not isinstance(wf, dict):
            continue
        steps = wf.get("steps")
        if not isinstance(steps, list):
            continue
        for st in steps:
            if not isinstance(st, dict):
                continue
            if "stepId" not in st and "id" in st:
                st["stepId"] = st.pop("id")
                mutated = True
            if (
                not any(k in st for k in ("operationId", "operationPath"))
                and "operation" in st
            ):
                st["operationId"] = st.pop("operation")
                mutated = True
            if "parameters" not in st and isinstance(st.get("params"), dict):
                params_dict = st.pop("params")
                st["parameters"] = [
                    {"name": pname, "in": "query", "value": pval}
                    for pname, pval in params_dict.items()
                ]
                mutated = True
    return mutated


# Execution


def _step_failed(
    step_id: str, error: Any, session: Optional[WorkflowSession]
) -> Exception:
    """The exception for a failed step: a timeout if the session stopped it."""
    if session is not None and session.stopped is not None:
        return WorkflowTimedOut(step_id, str(session.stopped))
    reason = str(error or "the step failed")
    if reason == "Step failed success criteria":
        reason = "its successCriteria were not met"
    return WorkflowStepFailed(
        step_id, reason, session.last_response if session is not None else None
    )


def run_workflow(
    runner: Any,
    workflow_id: str,
    inputs: Dict[str, Any],
    session: Optional[WorkflowSession] = None,
) -> Any:
    """Run a workflow step by step, treating any failed step as a failure.

    ``ArazzoRunner.execute_workflow`` carries on after a step raises (it
    gets STEP_ERROR and moves to the next step), and its ``retry`` action
    neither waits nor reruns the step: the next call goes on to the
    following step. This loop drives ``execute_next_step`` itself, stops at
    a failed step, and runs ``retry`` actions as written (wait
    ``retryAfter`` seconds, rerun up to ``retryLimit`` times) within the
    time limit. Runs in a worker thread.
    """
    execution_id = runner.start_workflow(workflow_id, inputs)
    state = runner.execution_states[execution_id]
    workflow = next(
        (
            wf
            for wf in (runner.arazzo_doc or {}).get("workflows", [])
            if isinstance(wf, dict) and wf.get("workflowId") == workflow_id
        ),
        {},
    )
    steps = {
        st.get("stepId"): st for st in workflow.get("steps", []) if isinstance(st, dict)
    }
    retries: Dict[str, int] = {}
    while True:
        # Once the time limit passes the session refuses the next step's
        # request, and the step fails as stopped
        result = runner.execute_next_step(execution_id)
        status = result.get("status")
        step_id = result.get("step_id") or state.current_step_id or "?"
        if status == WorkflowExecutionStatus.WORKFLOW_COMPLETE:
            return WorkflowExecutionResult(
                status=status,
                workflow_id=workflow_id,
                outputs=result.get("outputs") or state.workflow_outputs or {},
                step_outputs=state.step_outputs or None,
                inputs=inputs,
            )
        if status in (
            WorkflowExecutionStatus.ERROR,
            WorkflowExecutionStatus.STEP_ERROR,
        ):
            raise _step_failed(step_id, result.get("error"), session)
        if status == WorkflowExecutionStatus.GOTO_WORKFLOW:
            raise WorkflowStepFailed(
                step_id, "going to another workflow is not supported"
            )
        if status == WorkflowExecutionStatus.RETRY:
            action = runner.step_executor.determine_next_action(
                steps.get(step_id, {}), False, state
            )
            limit = int(action.get("retry_limit") or 1)
            retries[step_id] = retries.get(step_id, 0) + 1
            if retries[step_id] > limit:
                times = "retry" if limit == 1 else "retries"
                raise _step_failed(
                    step_id,
                    f"its successCriteria were still not met after {limit} {times}",
                    session,
                )
            wait = float(result.get("retry_after") or 0)
            if session is not None:
                try:
                    session.pause(wait)
                except WorkflowStopped as e:
                    raise WorkflowTimedOut(step_id, str(e)) from e
            state.goto_step_id = step_id
        # STEP_COMPLETE and GOTO_STEP: carry on with the next step


def _describe_failure(error: Exception, workflow_id: str, read_only: bool) -> str:
    """The tool error text for a failed or stopped run."""
    if isinstance(error, WorkflowTimedOut):
        return (
            f"Workflow '{workflow_id}' stopped at step '{error.step_id}': "
            f"{error.reason} (set {WORKFLOW_TIMEOUT_ENV} to change it). "
            "Requests sent by earlier steps may already have been applied; "
            "check the resources' current state before running it again."
        )
    assert isinstance(error, WorkflowStepFailed)
    text = f"Workflow '{workflow_id}' failed at step '{error.step_id}': {error.reason}."
    if error.last_response:
        method, path, status = error.last_response
        text += f" The last request, {method} {path}, returned HTTP {status}."
    if not read_only:
        text += (
            " Earlier steps may already have made changes; check the "
            "resources' current state before running it again."
        )
    return text


class ArazzoManager:
    _spec_docs: Dict[str, Any]  # populated during __init__ / load

    def __init__(self, config: Any, auth_manager: Any | None = None):
        self.config = config
        self.auth_manager = auth_manager
        self._spec_paths: List[str] = []
        self.workflows: Dict[str, WorkflowMeta] = {}
        # Loaded sourceDescriptions per Arazzo spec, by source name
        self._sources: Dict[str, Dict[str, Any]] = {}
        # Workflows left unregistered, with the reasons
        self.skipped_workflows: Dict[str, List[str]] = {}
        # Store full spec docs (parsed YAML) for fallback execution features (e.g., forEach emulation)
        self._spec_docs = {}

    async def load(self) -> None:
        arazzo_cfg = getattr(self.config, "arazzo", {}) or {}
        specs: List[str] = (
            arazzo_cfg.get("specs", []) if isinstance(arazzo_cfg, dict) else []
        )
        self._spec_paths = specs
        if not specs:
            logger.info("No Arazzo specs configured")
            return
        for spec_path in specs:
            try:
                content = await self._read_spec(spec_path)
                doc = yaml.safe_load(content)
                if isinstance(doc, dict):
                    # Normalize legacy mapping-form workflows and simplified
                    # step syntax (id->stepId, operation->operationId,
                    # params->parameters)
                    changed = _normalize_workflows(doc)
                    if _normalize_steps(doc) or changed:
                        content = yaml.safe_dump(doc)
                        logger.debug(f"Normalized workflow syntax in {spec_path}")
                    self._spec_docs[spec_path] = doc
                self._parse_spec_metadata(content, spec_path)
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to parse Arazzo spec {spec_path}: {e}")

    async def _classify(
        self, meta: WorkflowMeta, source_errors: Dict[str, str]
    ) -> List[str]:
        """Why a workflow isn't read-only (empty if it is)."""
        doc = self._spec_docs.get(meta.spec_path)
        if not isinstance(doc, dict):
            return [f"its spec {meta.spec_path} could not be parsed"]
        if meta.spec_path not in source_errors:
            try:
                sources = await self._get_sources(meta.spec_path)
                return workflow_write_reasons(meta.workflow_id, doc, sources)
            except Exception as e:
                # Try each spec's sources once, not once per workflow
                source_errors[meta.spec_path] = str(e).splitlines()[0]
        return [
            "its sourceDescriptions could not be loaded: "
            f"{source_errors[meta.spec_path]}"
        ]

    async def _select_workflows(self) -> None:
        """Drop workflows that aren't read-only, unless writes are allowed."""
        allow_writes = allow_write_workflows()
        source_errors: Dict[str, str] = {}
        for wf_id, meta in list(self.workflows.items()):
            reasons = await self._classify(meta, source_errors)
            meta.read_only = not reasons
            if not reasons:
                continue
            if allow_writes:
                logger.warning(
                    f"Registering workflow {wf_id}, which is not read-only "
                    f"({ALLOW_WRITE_WORKFLOWS_ENV} is set): {'; '.join(reasons)}"
                )
                continue
            self.skipped_workflows[wf_id] = reasons
            del self.workflows[wf_id]
            logger.warning(
                f"Skipping workflow {wf_id}: it could not be confirmed read-only "
                f"({'; '.join(reasons)}). Set {ALLOW_WRITE_WORKFLOWS_ENV}=1 "
                "to register it anyway."
            )

    async def register_with_fastmcp(self, mcp: Any) -> None:
        await self._select_workflows()
        if not self.workflows:
            return

        # Utility tools
        @mcp.tool(
            name="arazzo_list_workflows",
            title="List Workflows",
            description="List registered Arazzo workflow IDs.",
            tags={"workflows"},
        )
        async def list_workflows() -> List[str]:  # type: ignore
            return sorted(self.workflows.keys())

        @mcp.tool(
            name="arazzo_describe_workflow",
            title="Describe Workflow",
            description="Describe a specific Arazzo workflow (summary, inputs).",
            tags={"workflows"},
        )
        async def describe_workflow(workflow_id: str) -> Dict[str, Any]:  # type: ignore
            meta = self.workflows.get(workflow_id)
            if not meta:
                raise ValueError(f"Unknown workflow_id '{workflow_id}'")
            return {
                "workflow_id": meta.workflow_id,
                "spec_path": meta.spec_path,
                "summary": meta.summary,
                "description": meta.description,
                "inputs": (
                    list(meta.input_properties.keys()) if meta.input_properties else []
                ),
                "read_only": bool(meta.read_only),
            }

        # Dynamic execution tools per workflow
        for wf_id, meta in self.workflows.items():
            tool_name = f"workflow__{wf_id}"
            description = meta.summary or meta.description or f"Arazzo workflow {wf_id}"
            input_props = meta.input_properties or {}
            required = set(meta.required or [])

            # Build dynamic async function source
            param_defs: List[str] = []
            doc_lines: List[str] = [
                f"Execute Arazzo workflow '{wf_id}'.",
                "",
                "Parameters:",
            ]
            for name, schema in input_props.items():
                if not isinstance(name, str):
                    continue
                py_name = name if name.isidentifier() else name.replace("-", "_")
                json_type = schema.get("type") if isinstance(schema, dict) else None
                hint = "str"
                if json_type == "integer":
                    hint = "int | str"
                elif json_type == "number":
                    hint = "float | int | str"
                elif json_type == "boolean":
                    hint = "bool | str"
                default_part = "None"
                param_defs.append(f"{py_name}: {hint} = {default_part}")
                desc = schema.get("description") if isinstance(schema, dict) else ""
                doc_lines.append(
                    f"- {py_name}: {desc} {'(required)' if name in required else '(optional)'}"
                )
            param_defs.append("inputs_json: str | None = None")
            doc_lines.append(
                "- inputs_json: JSON object string merged with explicit params (optional)"
            )
            doc = "\n".join(doc_lines)
            params_signature = ", ".join(param_defs)
            func_name = f"run_{wf_id}".replace("-", "_")
            src = (
                f"async def {func_name}({params_signature}):\n"
                f'    """{doc}\n    """\n'
                f"    merged = {{}}\n"
                f"    import yaml as _yaml\n"
                f"    if inputs_json:\n"
                f"        try:\n"
                f"            _data = _yaml.safe_load(inputs_json) or {{}}\n"
                f"            if isinstance(_data, dict): merged.update(_data)\n"
                f"        except Exception: pass\n"
            )
            for name in input_props.keys():
                py_name = name if name.isidentifier() else name.replace("-", "_")
                src += f"    if {py_name} is not None: merged['{name}'] = {py_name}\n"
            src += "    return await __execute_workflow(merged)\n"

            local_ns: Dict[str, Any] = {}
            exec(src, {}, local_ns)
            dynamic_func = local_ns[func_name]

            async def __execute_workflow(  # type: ignore
                merged, _wf_id=wf_id, _meta=meta, _props=input_props
            ):
                return await self._run_tool(_wf_id, _meta, _props, merged)

            dynamic_func.__globals__["__execute_workflow"] = __execute_workflow

            if meta.read_only:
                annotations = ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=True,
                )
            else:
                annotations = ToolAnnotations(
                    read_only_hint=False, open_world_hint=True
                )

            # A workflow's Arazzo summary is its title, like an OpenAPI
            # operation's summary.
            mcp.tool(
                name=tool_name,
                title=meta.summary or None,
                description=description,
                tags={"workflows"},
                annotations=annotations,
            )(dynamic_func)  # type: ignore
            logger.info(
                f"Registered Arazzo workflow tool {tool_name} (params: {list(input_props.keys())})"
            )

    async def _run_tool(
        self,
        wf_id: str,
        meta: WorkflowMeta,
        input_props: Dict[str, Any],
        merged_inputs: Dict[str, Any],
    ) -> Any:
        """Run a workflow for its tool and simplify the result for MCP."""
        injected: List[str] = []
        # Auto inject bearer token if expected.
        if (
            "bearerToken" in input_props
            and "bearerToken" not in merged_inputs
            and self.auth_manager
        ):
            try:
                token_header = (
                    await self.auth_manager._get_client_credentials_auth_header()
                )  # type: ignore
                bearer = token_header.get("Authorization", "").replace("Bearer ", "")
                if bearer:
                    merged_inputs["bearerToken"] = bearer
                    injected.append(bearer)
                    logger.debug(f"Injected bearerToken for workflow {wf_id}")
            except Exception as e:  # pragma: no cover
                logger.warning(f"Bearer token auto-inject failed: {e}")
        session = WorkflowSession(
            loop=asyncio.get_running_loop(),
            auth_manager=self.auth_manager,
            managed_tokens=injected,
        )
        try:
            result = await self._execute_workflow_with_fallback(
                wf_id, meta.spec_path, merged_inputs, session=session
            )
        except (WorkflowStepFailed, WorkflowTimedOut) as e:
            raise ToolError(_describe_failure(e, wf_id, bool(meta.read_only))) from e
        # Simplify for MCP: return outputs or last step outputs instead of raw WorkflowExecutionResult
        try:
            outputs = getattr(result, "outputs", None)
            step_outputs = getattr(result, "step_outputs", None)
            if isinstance(outputs, dict) and outputs:
                return outputs
            if isinstance(step_outputs, dict) and step_outputs:
                last_step_id = list(step_outputs.keys())[-1]
                last_vals = step_outputs.get(last_step_id)
                if isinstance(last_vals, dict) and last_vals:
                    return last_vals
            # Fallback: return outputs even if empty (so caller sees structure)
            if isinstance(outputs, dict):
                return outputs
            return result
        except Exception as simplify_err:  # pragma: no cover
            logger.debug(
                "Failed to simplify workflow result for %s: %s",
                wf_id,
                simplify_err,
            )
            return result

    async def _load_source_descriptions(
        self, arazzo_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Load each sourceDescription of an Arazzo document by name.

        yaml.safe_load parses both YAML and JSON, so the response
        Content-Type does not matter.
        """
        sources: Dict[str, Any] = {}
        for source in arazzo_doc.get("sourceDescriptions") or []:
            name, url = source.get("name"), source.get("url")
            if name and url:
                sources[name] = yaml.safe_load(await self._read_spec(url))
        return sources

    async def _spec_doc(self, spec_path: str) -> Dict[str, Any]:
        doc = self._spec_docs.get(spec_path)
        if doc is None:
            doc = yaml.safe_load(await self._read_spec(spec_path))
            if isinstance(doc, dict):
                _normalize_workflows(doc)
                _normalize_steps(doc)
            self._spec_docs[spec_path] = doc
        return doc

    async def _get_sources(self, spec_path: str) -> Dict[str, Any]:
        """The spec's sourceDescriptions, loaded once."""
        if spec_path not in self._sources:
            doc = await self._spec_doc(spec_path)
            self._sources[spec_path] = await self._load_source_descriptions(doc)
        return self._sources[spec_path]

    async def _get_runner(self, spec_path: str, http_client: Any = None):
        """A new runner for one run (runners keep per-run state and aren't
        thread-safe), sharing the loaded source descriptions."""
        if not ArazzoRunner:
            return None
        try:
            # Build the runner from documents we load ourselves:
            # from_arazzo_path() gets the unresolved packaged path, and it
            # parses remote specs as JSON unless the Content-Type says YAML
            # (docs.equinix.com serves YAML as application/octet-stream).
            arazzo_doc = copy.deepcopy(await self._spec_doc(spec_path))
            _normalize_workflows(arazzo_doc)
            _normalize_steps(arazzo_doc)
            return ArazzoRunner(
                arazzo_doc=arazzo_doc,
                source_descriptions=await self._get_sources(spec_path),
                http_client=http_client or WorkflowSession(),
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to initialize ArazzoRunner for {spec_path}: {e}")
            raise RuntimeError(
                f"Could not initialize the Arazzo runner for {spec_path}: {e}"
            ) from e

    async def _execute_workflow_with_fallback(
        self,
        workflow_id: str,
        spec_path: str,
        inputs: Dict[str, Any],
        session: Optional[WorkflowSession] = None,
    ):
        """Run a workflow in a worker thread and return its result.

        arazzo-runner doesn't support ``forEach``, so workflows that use it
        run on the local forEach executor instead. Raises WorkflowStepFailed
        or WorkflowTimedOut when a step fails or the time limit passes.
        """
        if session is None:
            session = WorkflowSession(
                loop=asyncio.get_running_loop(), auth_manager=self.auth_manager
            )
        runner = await self._get_runner(spec_path, session)
        if not runner:
            raise RuntimeError(
                "arazzo-runner package not installed. Install with 'pip install arazzo-runner'"
            )
        logger.info(
            f"Executing Arazzo workflow {workflow_id} with inputs: {list(inputs.keys())}"
        )
        if self._workflow_contains_foreach(workflow_id, spec_path):
            return await self._execute_with_foreach(
                runner, workflow_id, spec_path, inputs, session
            )
        return await self._in_thread(
            session, run_workflow, runner, workflow_id, inputs, session
        )

    @staticmethod
    async def _in_thread(session: Optional[WorkflowSession], func, *args):
        """Run ``func`` in a worker thread; if the tool call is cancelled,
        stop the run from sending further requests."""
        try:
            return await asyncio.to_thread(func, *args)
        except asyncio.CancelledError:
            if session is not None:
                session.cancel()
            raise

    def _workflow_contains_foreach(self, workflow_id: str, spec_path: str) -> bool:
        doc = self._spec_docs.get(spec_path) or {}
        wfs = doc.get("workflows", [])
        for wf in wfs:
            if isinstance(wf, dict) and wf.get("workflowId") == workflow_id:
                steps = wf.get("steps", [])
                for st in steps:
                    if isinstance(st, dict) and "forEach" in st:
                        return True
        return False

    async def _execute_with_foreach(
        self,
        runner: Any,
        workflow_id: str,
        spec_path: str,
        inputs: Dict[str, Any],
        session: Optional[WorkflowSession] = None,
    ):
        """Run a forEach workflow on the local executor in a worker thread."""
        return await self._in_thread(
            session,
            self._run_foreach,
            runner,
            workflow_id,
            spec_path,
            inputs,
            session,
        )

    def _run_foreach(
        self,
        runner: Any,
        workflow_id: str,
        spec_path: str,
        inputs: Dict[str, Any],
        session: Optional[WorkflowSession] = None,
    ):  # noqa: C901
        """Minimal execution engine adding support for `forEach` and `$item` substitution.

        Limitations:
        - Steps run in order; onSuccess/onFailure actions are not followed.
        - Only handles one level of forEach (no nested forEach inside nested steps).
        - `$item` substitution done via simple string replace in parameter values and requestBody payload scalars.
        - Outputs from loop aggregate lists per output key produced by final nested step for each item.

        A failed step, top-level or nested, raises WorkflowStepFailed.
        """
        doc = self._spec_docs.get(spec_path) or {}
        workflow_def = None
        for wf in doc.get("workflows", []):
            if isinstance(wf, dict) and wf.get("workflowId") == workflow_id:
                workflow_def = wf
                break
        if not workflow_def:
            raise ValueError(f"Workflow {workflow_id} not found in spec {spec_path}")

        steps = workflow_def.get("steps", [])
        state = ExecutionState(workflow_id=workflow_id, inputs=inputs)
        step_outputs: Dict[str, Dict[str, Any]] = state.step_outputs

        # Helper evaluators (very constrained)
        def _resolve_simple(expr: Any):
            if not isinstance(expr, str):
                return expr
            if expr.startswith("$inputs."):
                path = expr[len("$inputs.") :].split(".")
                cur: Any = inputs
                for p in path:
                    if isinstance(cur, dict):
                        cur = cur.get(p)
                    else:
                        return None
                return cur
            if expr.startswith("$steps."):
                parts = expr.split(".")
                if len(parts) >= 4 and parts[2] == "outputs":
                    st = parts[1]
                    out = parts[3]
                    val = step_outputs.get(st, {}).get(out)
                    # support deeper field access
                    for extra in parts[4:]:
                        if isinstance(val, dict):
                            val = val.get(extra)
                    return val
            return expr

        def _subst_item(value: Any, item: Any):
            if isinstance(value, str) and "$item." in value and isinstance(item, dict):
                # naive replacement of $item.field tokens
                for k, v in item.items():
                    token = f"$item.{k}"
                    if token in value:
                        value = value.replace(token, str(v))
            return value

        def _execute(step_def: Dict[str, Any], step_state: Any) -> Dict[str, Any]:
            step_id = step_def.get("stepId", "?")
            if session is not None:
                try:
                    session.check()
                except WorkflowStopped as e:
                    raise WorkflowTimedOut(step_id, str(e)) from e
            try:
                result = runner.step_executor.execute_step(step_def, step_state)
            except Exception as op_err:
                raise _step_failed(step_id, op_err, session) from op_err
            if not isinstance(result, dict) or not result.get("success"):
                raise _step_failed(step_id, "its successCriteria were not met", session)
            outputs = result.get("outputs", {})
            return outputs if isinstance(outputs, dict) else {}

        # Loop through steps
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("stepId", "")
            if "forEach" not in step:
                step_outputs[step_id] = _execute(step, state)
                continue
            collection_expr = step.get("forEach")
            coll = _resolve_simple(collection_expr)
            if coll is None:
                coll = []
            aggregated: Dict[str, List[Any]] = {}
            nested_steps = step.get("steps", [])
            for item in coll:
                nested_state = ExecutionState(
                    workflow_id=f"{workflow_id}::{step_id}",
                    inputs=inputs,
                    step_outputs=dict(step_outputs),
                )
                # Merge loop item into inputs for substitution convenience
                for nested in nested_steps:
                    if not isinstance(nested, dict):
                        continue
                    n_clone = json.loads(json.dumps(nested))  # shallow deep copy
                    # Substitute $item tokens in parameters & requestBody
                    params = n_clone.get("parameters", [])
                    for p in params:
                        if isinstance(p, dict) and "value" in p:
                            p["value"] = _subst_item(p["value"], item)
                    if "requestBody" in n_clone and isinstance(
                        n_clone["requestBody"], dict
                    ):
                        payload = n_clone["requestBody"].get("payload")

                        def walk(obj):
                            if isinstance(obj, dict):
                                return {
                                    k: walk(_subst_item(v, item))
                                    for k, v in obj.items()
                                }
                            elif isinstance(obj, list):
                                return [walk(x) for x in obj]
                            else:
                                return _subst_item(obj, item)

                        n_clone["requestBody"]["payload"] = walk(payload)
                    # Execute operation or workflow (only operation supported in nested for now)
                    if not any(k in n_clone for k in ("operationId", "operationPath")):
                        logger.warning(
                            f"Nested step {n_clone.get('stepId')} inside forEach lacks operationId/operationPath; skipping"
                        )
                        continue
                    nested_state.step_outputs[n_clone.get("stepId", "nested")] = (
                        _execute(n_clone, nested_state)
                    )
                # After nested steps, pick outputs from last nested step
                if nested_steps:
                    last_id = (
                        nested_steps[-1].get("stepId")
                        if isinstance(nested_steps[-1], dict)
                        else None
                    )
                    if last_id and last_id in nested_state.step_outputs:
                        final_out = nested_state.step_outputs[last_id]
                        for k, v in final_out.items():
                            aggregated.setdefault(k, []).append(v)
            step_outputs[step_id] = aggregated

        # Build final outputs per workflow outputs mapping (simple references only)
        final_mapping = workflow_def.get("outputs", {})
        final_outputs: Dict[str, Any] = {}
        for out_name, expr in (final_mapping or {}).items():
            if isinstance(expr, str) and expr.startswith("$steps."):
                val = _resolve_simple(expr)
                final_outputs[out_name] = val
            else:
                final_outputs[out_name] = expr

        return WorkflowExecutionResult(
            status=WorkflowExecutionStatus.WORKFLOW_COMPLETE,
            workflow_id=workflow_id,
            outputs=final_outputs,
            step_outputs=step_outputs,
            inputs=inputs,
            error=None,
        )

    async def _read_spec(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            import httpx2

            async with httpx2.AsyncClient(timeout=30.0) as client:
                resp = await client.get(path_or_url)
                resp.raise_for_status()
                return resp.text
        # Local file; relative paths resolve against the config's base
        # directory so packaged workflow specs are found from any cwd
        path = Path(path_or_url)
        if not path.exists():
            path = self.config.resolve_path(path_or_url)
        text = path.read_text()
        return text

    def _parse_spec_metadata(self, content: str, source: str) -> None:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            logger.warning(f"Arazzo spec {source} not a mapping; skipping")
            return
        workflows: Union[List[Any], Dict[str, Any]] = data.get("workflows", [])
        if isinstance(workflows, dict):  # legacy mapping form
            iterable = [
                {"workflowId": k, **(v if isinstance(v, dict) else {})}
                for k, v in workflows.items()
            ]
        else:
            iterable = workflows if isinstance(workflows, list) else []
        for wf in iterable:
            if not isinstance(wf, dict):
                continue
            wf_id = wf.get("workflowId") or wf.get("id")
            if not wf_id:
                continue
            summary = wf.get("summary", "")
            description = wf.get("description", "")
            # Input schema may be nested under 'inputs' with JSON Schema structure
            input_schema = wf.get("inputs")
            properties = {}
            required: List[str] = []
            if isinstance(input_schema, dict):
                props = input_schema.get("properties")
                if isinstance(props, dict):
                    properties = props  # raw
                req = input_schema.get("required")
                if isinstance(req, list):
                    required = [r for r in req if isinstance(r, str)]
            self.workflows[wf_id] = WorkflowMeta(
                workflow_id=wf_id,
                spec_path=source,
                summary=summary,
                description=description,
                input_properties=properties,
                required=required,
            )
            logger.info(
                f"Discovered Arazzo workflow {wf_id} (inputs: {len(properties)}) from {source}"
            )
