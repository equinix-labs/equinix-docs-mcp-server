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

Notes:
- Each workflow tool accepts explicit parameters (derived from JSON Schema
    `inputs.properties`) plus a catch‑all `inputs_json` (YAML/JSON object string).
- Explicit params override `inputs_json` keys.
- If `arazzo-runner` is missing, execution raises an instructive error but
    listing/description still works.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

try:
    import requests  # Used for ArazzoRunner HTTP client (ensures remote sourceDescriptions load)
except Exception:  # pragma: no cover
    requests = None  # type: ignore

logger = logging.getLogger(__name__)

try:  # Optional import; execution requires it.
    from arazzo_runner import ArazzoRunner  # type: ignore
except Exception:  # pragma: no cover
    ArazzoRunner = None  # type: ignore


@dataclass
class WorkflowMeta:
    workflow_id: str
    spec_path: str
    summary: str = ""
    description: str = ""
    input_properties: Optional[Dict[str, Dict[str, Any]]] = None
    required: Optional[List[str]] = None


class ArazzoManager:
    _spec_docs: Dict[str, Any]  # populated during __init__ / load

    def __init__(self, config: Any, auth_manager: Any | None = None):
        self.config = config
        self.auth_manager = auth_manager
        self._spec_paths: List[str] = []
        self.workflows: Dict[str, WorkflowMeta] = {}
        self._runners: Dict[str, Any] = {}
        # Store full spec docs (parsed YAML) for fallback execution features (e.g., forEach emulation)
        self._spec_docs = {}

    async def load(self) -> None:
        arazzo_cfg = getattr(self.config, "arazzo", {}) or {}
        specs: List[str] = (
            arazzo_cfg.get("specs", []) if isinstance(arazzo_cfg, dict) else []
        )
        self._spec_paths = specs
        # Bridge credentials: if AuthManager already has a Metal token expose it
        # using the environment variable name that arazzo-runner's default
        # credential resolver expects (SOURCE_SCHEMENAME upper‑cased). The Metal
        # OpenAPI spec defines the security scheme name 'x_auth_token' under the
        # sourceDescription named 'metal', so arazzo-runner looks for
        # METAL_X_AUTH_TOKEN. We reuse the existing EQUINIX_METAL_TOKEN value to
        # avoid asking the user to export a second var.
        try:  # pragma: no cover - defensive only
            import os

            if (
                self.auth_manager
                and getattr(self.auth_manager, "metal_token", None)
                and "METAL_X_AUTH_TOKEN" not in os.environ
            ):
                os.environ["METAL_X_AUTH_TOKEN"] = getattr(
                    self.auth_manager, "metal_token"
                )
                logger.debug(
                    "Bridged EQUINIX_METAL_TOKEN -> METAL_X_AUTH_TOKEN for Arazzo"
                )
        except Exception as cred_bridge_err:  # pragma: no cover
            logger.warning(
                "Failed to bridge Metal token env var for Arazzo: %s", cred_bridge_err
            )
        if not specs:
            logger.info("No Arazzo specs configured")
            return
        for spec_path in specs:
            try:
                content = await self._read_spec(spec_path)
                doc = yaml.safe_load(content)
                if isinstance(doc, dict):
                    # Normalize legacy mapping-form workflows -> list of workflow objects
                    wfs = doc.get("workflows")
                    if isinstance(wfs, dict):
                        doc["workflows"] = [
                            {"workflowId": k, **(v if isinstance(v, dict) else {})}
                            for k, v in wfs.items()
                        ]
                        logger.debug(
                            "Normalized mapping-form workflows in %s to list form (%d workflows)",
                            spec_path,
                            len(doc["workflows"]),
                        )
                        # Update content so metadata parser sees normalized version
                        content = yaml.safe_dump(doc)
                    # Normalize simplified step syntax (id->stepId, operation->operationId, params->parameters)
                    mutated = False
                    for wf in doc.get("workflows", []) or []:
                        if not isinstance(wf, dict):
                            continue
                        steps = wf.get("steps")
                        if isinstance(steps, list):
                            for st in steps:
                                if not isinstance(st, dict):
                                    continue
                                if "stepId" not in st and "id" in st:
                                    st["stepId"] = st.pop("id")
                                    mutated = True
                                if (
                                    not any(
                                        k in st
                                        for k in ("operationId", "operationPath")
                                    )
                                    and "operation" in st
                                ):
                                    st["operationId"] = st.pop("operation")
                                    mutated = True
                                if "parameters" not in st and isinstance(
                                    st.get("params"), dict
                                ):
                                    params_dict = st.pop("params")
                                    param_list = []
                                    for pname, pval in params_dict.items():
                                        param_list.append(
                                            {
                                                "name": pname,
                                                "in": "query",
                                                "value": pval,
                                            }
                                        )
                                    st["parameters"] = param_list
                                    mutated = True
                    if mutated:
                        content = yaml.safe_dump(doc)
                        logger.debug(
                            "Normalized simplified step syntax in %s", spec_path
                        )
                    self._spec_docs[spec_path] = doc
                self._parse_spec_metadata(content, spec_path)
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to parse Arazzo spec {spec_path}: {e}")

    async def register_with_fastmcp(self, mcp: Any) -> None:
        if not self.workflows:
            return

        # Utility tools
        @mcp.tool(
            name="arazzo_list_workflows",
            description="List registered Arazzo workflow IDs.",
        )
        async def list_workflows() -> List[str]:  # type: ignore
            return sorted(self.workflows.keys())

        @mcp.tool(
            name="arazzo_describe_workflow",
            description="Describe a specific Arazzo workflow (summary, inputs).",
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

            async def _exec_wrapper(
                merged_inputs: Dict[str, Any], _wf_id=wf_id, _meta=meta
            ):
                # Auto inject bearer token if expected.
                if (
                    "bearerToken" in (input_props.keys())
                    and "bearerToken" not in merged_inputs
                    and self.auth_manager
                ):
                    try:
                        token_header = await self.auth_manager._get_client_credentials_auth_header()  # type: ignore
                        bearer = token_header.get("Authorization", "").replace(
                            "Bearer ", ""
                        )
                        if bearer:
                            merged_inputs["bearerToken"] = bearer
                            logger.debug(f"Injected bearerToken for workflow {_wf_id}")
                    except Exception as e:  # pragma: no cover
                        logger.warning(f"Bearer token auto-inject failed: {e}")
                result = await self._execute_workflow_with_fallback(
                    _wf_id, _meta.spec_path, merged_inputs
                )
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
                        _wf_id,
                        simplify_err,
                    )
                    return result

            async def __execute_workflow(merged):  # type: ignore
                return await _exec_wrapper(merged)

            dynamic_func.__globals__["__execute_workflow"] = __execute_workflow

            mcp.tool(name=tool_name, description=description)(dynamic_func)  # type: ignore
            logger.info(
                f"Registered Arazzo workflow tool {tool_name} (params: {list(input_props.keys())})"
            )

    async def _get_runner(self, spec_path: str):
        if spec_path in self._runners:
            return self._runners[spec_path]
        if not ArazzoRunner:
            return None
        try:
            http_client = None
            if requests:
                # Provide HTTP client so remote specs load and inject auth headers.
                session = requests.Session()
                # If we have a Metal token, pre‑set the header so even if the
                # arazzo-runner credential lookup misses (e.g. user didn't set
                # METAL_X_AUTH_TOKEN) requests still authenticate.
                try:  # pragma: no cover - header injection is simple
                    if getattr(self.auth_manager, "metal_token", None):
                        session.headers.setdefault(
                            "X-Auth-Token", getattr(self.auth_manager, "metal_token")
                        )
                        logger.debug(
                            "Injected X-Auth-Token header into Arazzo HTTP session"
                        )
                    # Pre-inject bearer token (client credentials) for non-Metal APIs
                    if self.auth_manager:
                        import asyncio as _asyncio

                        async def _maybe_bearer():
                            try:
                                hdr = await self.auth_manager._get_client_credentials_auth_header()  # type: ignore
                                bearer_val = hdr.get("Authorization")
                                if bearer_val:
                                    session.headers.setdefault(
                                        "Authorization", bearer_val
                                    )
                                    logger.debug(
                                        "Injected Authorization header into Arazzo HTTP session"
                                    )
                            except Exception as be:  # pragma: no cover
                                logger.debug(
                                    "Skipping bearer injection at runner init: %s", be
                                )

                        try:
                            loop = _asyncio.get_running_loop()
                            loop.create_task(_maybe_bearer())
                        except RuntimeError:
                            _asyncio.run(_maybe_bearer())
                except Exception as hdr_err:  # pragma: no cover
                    logger.warning(
                        "Failed to inject Metal token into Arazzo session: %s", hdr_err
                    )
                http_client = session
            runner = ArazzoRunner.from_arazzo_path(spec_path, http_client=http_client)
            # Normalize runner.arazzo_doc workflows if still mapping-form
            try:  # pragma: no cover - defensive
                adoc = getattr(runner, "arazzo_doc", None)
                if isinstance(adoc, dict):
                    wfs = adoc.get("workflows")
                    if isinstance(wfs, dict):
                        adoc["workflows"] = [
                            {"workflowId": k, **(v if isinstance(v, dict) else {})}
                            for k, v in wfs.items()
                        ]
                        logger.debug(
                            "Runner spec %s had mapping-form workflows; normalized to list (%d workflows)",
                            spec_path,
                            len(adoc["workflows"]),
                        )
                    # Step-level normalization (id->stepId, operation->operationId, params->parameters)
                    mutated = False
                    for wf in adoc.get("workflows", []) or []:
                        if not isinstance(wf, dict):
                            continue
                        steps = wf.get("steps")
                        if isinstance(steps, list):
                            for st in steps:
                                if not isinstance(st, dict):
                                    continue
                                if "stepId" not in st and "id" in st:
                                    st["stepId"] = st.pop("id")
                                    mutated = True
                                if (
                                    not any(
                                        k in st
                                        for k in ("operationId", "operationPath")
                                    )
                                    and "operation" in st
                                ):
                                    st["operationId"] = st.pop("operation")
                                    mutated = True
                                if "parameters" not in st and isinstance(
                                    st.get("params"), dict
                                ):
                                    params_dict = st.pop("params")
                                    param_list = []
                                    for pname, pval in params_dict.items():
                                        param_list.append(
                                            {
                                                "name": pname,
                                                "in": "query",
                                                "value": pval,
                                            }
                                        )
                                    st["parameters"] = param_list
                                    mutated = True
                    if mutated:
                        logger.debug(
                            "Runner spec %s had simplified step syntax; normalized",
                            spec_path,
                        )
            except Exception as norm_err:
                logger.warning(
                    "Failed to normalize runner workflows for %s: %s",
                    spec_path,
                    norm_err,
                )
            self._runners[spec_path] = runner
            return runner
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to initialize ArazzoRunner for {spec_path}: {e}")
            return None

    async def _execute_workflow_with_fallback(
        self, workflow_id: str, spec_path: str, inputs: Dict[str, Any]
    ):
        """Execute workflow via ArazzoRunner; if it fails due to unsupported forEach, fallback to local loop executor.

        Returns WorkflowExecutionResult (opaque to caller) either from runner or synthesized.
        """
        runner = await self._get_runner(spec_path)
        if not runner:
            raise RuntimeError(
                "arazzo-runner package not installed. Install with 'pip install arazzo-runner'"
            )
        logger.info(
            f"Executing Arazzo workflow {workflow_id} with inputs: {list(inputs.keys())}"
        )
        # Fast path if no forEach present in definition
        if not self._workflow_contains_foreach(workflow_id, spec_path):
            return runner.execute_workflow(workflow_id, inputs)
        try:
            # Attempt native execution first (future versions may add support)
            return runner.execute_workflow(workflow_id, inputs)
        except Exception as e:
            msg = str(e)
            if "does not specify an operation or workflow" not in msg:
                raise
            logger.warning(
                f"Falling back to local forEach executor for workflow {workflow_id}: {msg}"
            )
            try:
                return await self._execute_with_foreach(
                    runner, workflow_id, spec_path, inputs
                )
            except Exception as fe:
                logger.error(f"Local forEach execution failed: {fe}")
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
        self, runner: Any, workflow_id: str, spec_path: str, inputs: Dict[str, Any]
    ):  # noqa: C901
        """Minimal execution engine adding support for `forEach` and `$item` substitution.

        Limitations:
        - Only handles one level of forEach (no nested forEach inside nested steps).
        - `$item` substitution done via simple string replace in parameter values and requestBody payload scalars.
        - Expressions like JS `.filter()` / `.map()` are NOT evaluated here (we rely on runner for those steps).
        - Outputs from loop aggregate lists per output key produced by final nested step for each item.
        """
        # Lazy import / fallback definitions for models (ensures static analysis passes even if dependency missing)
        try:  # pragma: no cover - executed only when dependency available
            from arazzo_runner.models import (  # type: ignore
                ExecutionState,
                WorkflowExecutionResult,
                WorkflowExecutionStatus,
            )
        except Exception:  # pragma: no cover

            @dataclass
            class _FallbackExecutionState:  # minimal stand‑in
                workflow_id: str
                step_outputs: Dict[str, Any] = field(default_factory=dict)

            class _FallbackStatus:
                WORKFLOW_COMPLETE = "WORKFLOW_COMPLETE"

            @dataclass
            class _FallbackResult:
                status: str
                workflow_id: str
                outputs: Dict[str, Any]
                step_outputs: Dict[str, Any]
                inputs: Dict[str, Any]
                error: Optional[str] = None

            WorkflowExecutionResult = _FallbackResult  # type: ignore
            WorkflowExecutionStatus = _FallbackStatus  # type: ignore
            ExecutionState = _FallbackExecutionState  # type: ignore

        doc = self._spec_docs.get(spec_path) or {}
        workflow_def = None
        for wf in doc.get("workflows", []):
            if isinstance(wf, dict) and wf.get("workflowId") == workflow_id:
                workflow_def = wf
                break
        if not workflow_def:
            raise ValueError(f"Workflow {workflow_id} not found in spec {spec_path}")

        steps = workflow_def.get("steps", [])
        step_outputs: Dict[str, Dict[str, Any]] = {}

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

        # Loop through steps
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("stepId", "")
            if "forEach" in step:
                collection_expr = step.get("forEach")
                coll = _resolve_simple(collection_expr)
                if coll is None:
                    coll = []
                aggregated: Dict[str, List[Any]] = {}
                nested_steps = step.get("steps", [])
                for item in coll:
                    nested_state = ExecutionState(
                        workflow_id=f"{workflow_id}::{step_id}"
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
                        if not any(
                            k in n_clone for k in ("operationId", "operationPath")
                        ):
                            logger.warning(
                                f"Nested step {n_clone.get('stepId')} inside forEach lacks operationId/operationPath; skipping"
                            )
                            continue
                        try:
                            result = runner.step_executor.execute_step(
                                n_clone, nested_state
                            )
                        except Exception as op_err:
                            logger.error(f"Nested step execution failed: {op_err}")
                            result = {"success": False, "outputs": {}}
                        # Record outputs
                        nested_outputs = (
                            result.get("outputs", {})
                            if isinstance(result, dict)
                            else {}
                        )
                        if isinstance(nested_outputs, dict):
                            nested_state.step_outputs[
                                n_clone.get("stepId", "nested")
                            ] = nested_outputs
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
            else:
                # Delegate normal step to runner (may include complex expressions we don't reimplement)
                try:
                    # Create a temporary execution by invoking runner for a tiny one-step workflow? Simpler: run full workflow via runner then break.
                    # Fallback approach: execute via runner and then extract that step's outputs from its state.
                    temp_res = runner.execute_workflow(workflow_id, inputs)
                    # If runner succeeded we can just return that result early (it handled everything)
                    return temp_res
                except Exception as e:
                    logger.debug(
                        f"Runner execution still failing mid-workflow (expected for forEach fallback): {e}"
                    )
                    break

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
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(path_or_url)
                resp.raise_for_status()
                return resp.text
        # Local file
        path = Path(path_or_url)
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
            summary = wf.get("summary", "") or wf.get("description", "")
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
