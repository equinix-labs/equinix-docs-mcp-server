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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

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
    def __init__(self, config: Any, auth_manager: Any | None = None):
        self.config = config
        self.auth_manager = auth_manager
        self._spec_paths: List[str] = []
        self.workflows: Dict[str, WorkflowMeta] = {}
        self._runners: Dict[str, Any] = {}

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
                runner = await self._get_runner(_meta.spec_path)
                if not runner:
                    raise RuntimeError(
                        "arazzo-runner package not installed. Install with 'pip install arazzo-runner'"
                    )
                logger.info(
                    f"Executing Arazzo workflow {_wf_id} with inputs: {list(merged_inputs.keys())}"
                )
                return runner.execute_workflow(_wf_id, merged_inputs)

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
            runner = ArazzoRunner.from_arazzo_path(spec_path)
            self._runners[spec_path] = runner
            return runner
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to initialize ArazzoRunner for {spec_path}: {e}")
            return None

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
