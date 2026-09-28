"""Manages the lifecycle of OpenAPI specifications.

This module is responsible for fetching, caching, merging, and applying
overlays to OpenAPI specifications as defined in the configuration. It
handles multiple spec sources and combines them into a single, coherent
specification.
"""

import asyncio
import collections.abc
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx2
import yaml
from openapi_spec_validator import validate

from .config import APIConfig, Config, cache_root
from .openapi_overlays.overlay_manager import OverlayManager
from .swagger2openapi.converter import Swagger2OpenAPIConverter

logger = logging.getLogger(__name__)


def deep_merge(d: Dict[str, Any], u: collections.abc.Mapping) -> Dict[str, Any]:
    """
    Merge two dictionaries recursively.

    :param d: The dictionary to merge into.
    :param u: The dictionary to merge from.
    :return: The merged dictionary.
    """
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = deep_merge(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def _update_references(obj: Any) -> Any:
    """Recursively update '$ref' values in the spec."""
    # This function is currently not used, but is kept for future reference.
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith("#/components/schemas/")
            ):
                new_ref = f"#/$defs/{value.split('/')[-1]}"
                obj[key] = new_ref
            else:
                _update_references(value)
    elif isinstance(obj, list):
        for item in obj:
            _update_references(item)
    return obj


class SpecManager:
    """Manages fetching, caching, and merging of OpenAPI specifications."""

    def __init__(self, config: Config, cache_dir: Optional[str] = None):
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else cache_root() / "specs"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.overlay_manager = OverlayManager(config)
        # Spec URLs on docs.equinix.com move behind 301s as APIs are
        # reorganized (e.g. the smartview family), so follow redirects.
        self.http_client = httpx2.AsyncClient(follow_redirects=True)
        self.converter = Swagger2OpenAPIConverter()

    async def update_specs(self) -> None:
        """Update all API specifications based on the configuration."""
        tasks = []
        for api_name, api_config in self.config.apis.items():
            if api_config.enabled:
                tasks.append(self._process_api_group(api_name, api_config))
        await asyncio.gather(*tasks)

    async def _process_api_group(self, api_name: str, api_config: APIConfig) -> None:
        """Process a single group of API specifications."""
        logger.info(f"Processing API group: {api_name}")
        base_spec: Optional[Dict[str, Any]] = None
        for i, spec_source in enumerate(api_config.specs):
            spec = await self._fetch_and_cache_spec(f"{api_name}_{i}", spec_source.url)
            if not spec:
                continue

            # Extract base path from server URL
            base_path = ""
            if "servers" in spec and spec["servers"]:
                server_url = spec["servers"][0].get("url", "")
                if server_url:
                    from urllib.parse import urlparse

                    parsed_url = urlparse(server_url)
                    base_path = parsed_url.path
                    # remove trailing slash if present
                    if base_path.endswith("/"):
                        base_path = base_path[:-1]

            # Prepend base_path to all paths
            if base_path and "paths" in spec:
                new_paths = {}
                for path, path_item in spec["paths"].items():
                    new_paths[f"{base_path}{path}"] = path_item
                spec["paths"] = new_paths

            if base_spec is None:
                base_spec = spec
            else:
                # Merge subsequent specs into the base spec
                base_spec.setdefault("paths", {}).update(spec.get("paths", {}))
                if "components" in spec:
                    logger.debug(
                        f"Merging components for {api_name}: {spec.get('components')}"
                    )
                    deep_merge(
                        base_spec.setdefault("components", {}), spec["components"]
                    )
                if "$defs" in spec:
                    logger.debug(f"Merging $defs for {api_name}: {spec.get('$defs')}")
                    deep_merge(base_spec.setdefault("$defs", {}), spec["$defs"])

        if base_spec is None:
            return

        merged_spec = self._merge_api_spec(base_spec, api_config)

        # Some specs (e.g. workvisit) omit operationIds; synthesize them so
        # every operation can become a tool.
        try:
            self._apply_autogen_operation_ids(merged_spec)
        except Exception as e:
            logger.debug(f"Could not apply autogen operationIds: {e}")

        # Repair recurring authoring bugs that make otherwise-valid specs
        # unparseable (found across the api-catalog by scripts/vet_specs.py).
        self._sanitize_schema_quirks(merged_spec)

        # Catalog search transforms only index per-tool text (name,
        # description, parameters), so family-level context from info.title
        # would otherwise be invisible to search.
        self._inject_family_context(merged_spec, api_name, api_config)

        # Ensure configured security scheme exists BEFORE normalization,
        # so operations can reference the right scheme.
        merged_spec.setdefault("components", {})
        comps = merged_spec["components"]
        sec_schemes = comps.setdefault("securitySchemes", {})
        if api_config.auth_type == "client_credentials":
            # Always ensure ClientCredentials scheme exists (merge, don't overwrite)
            sec_schemes.setdefault(
                "ClientCredentials",
                {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": self.config.auth.client_credentials.get(
                                "token_url",
                                "https://api.equinix.com/oauth2/v1/token",
                            ),
                            "scopes": {},
                        }
                    },
                },
            )
        elif api_config.auth_type == "metal_token":
            sec_schemes.setdefault(
                "MetalToken",
                {
                    "type": "apiKey",
                    "in": "header",
                    "name": self.config.auth.metal_token.get(
                        "header_name", "X-Auth-Token"
                    ),
                },
            )

        # Normalize auth: promote Authorization header parameters to security requirements
        try:
            self._promote_auth_header_to_security(merged_spec)
        except Exception as e:
            logger.warning(
                f"Auth normalization skipped for {api_name} due to error: {e}"
            )

        # Ensure a sensible default top-level security requirement if missing
        if not merged_spec.get("security"):
            if api_config.auth_type == "client_credentials":
                merged_spec["security"] = [{"ClientCredentials": []}]
            elif api_config.auth_type == "metal_token":
                merged_spec["security"] = [{"MetalToken": []}]

        self.save_merged_spec(api_name, merged_spec)

    _HTTP_METHODS = (
        "get",
        "put",
        "post",
        "delete",
        "options",
        "head",
        "patch",
        "trace",
    )

    def _inject_family_context(
        self, spec: Dict[str, Any], api_name: str, api_config: APIConfig
    ) -> None:
        """Append family-level context to every operation description.

        FastMCP derives each tool's description from the operation's
        description (falling back to summary), and the catalog search
        transforms index only that per-tool text. The spec's info block —
        often the best statement of what the API family does (e.g. the
        "diloa" family is titled "Digital LOA API") — never reaches the
        index unless it is folded into each operation.
        """
        info = spec.get("info") or {}
        title = str(info.get("title") or "").strip()
        family_desc = (api_config.description or "").strip()
        if not family_desc:
            family_desc = self._summarize_info_description(
                str(info.get("description") or "")
            )

        label = re.sub(r"\s+APIs?$", "", title) or api_name
        parts = [f"Part of the Equinix {label} API family ({api_name})."]
        if family_desc and family_desc.rstrip(".") != title.rstrip("."):
            parts.append(family_desc.rstrip(".") + ".")
        context = " ".join(parts)

        for path_item in (spec.get("paths") or {}).values():
            if not isinstance(path_item, dict):
                continue
            for method in self._HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                existing = str(
                    operation.get("description") or operation.get("summary") or ""
                ).strip()
                if context in existing:
                    continue
                operation["description"] = (
                    f"{existing}\n\n{context}" if existing else context
                )

    @staticmethod
    def _summarize_info_description(text: str, max_len: int = 300) -> str:
        """Collapse an info.description to a short single-line summary."""
        collapsed = " ".join(text.split())
        if len(collapsed) <= max_len:
            return collapsed
        cut = collapsed[:max_len].rsplit(" ", 1)[0]
        return cut + "…"

    async def _fetch_and_cache_spec(
        self, spec_key: str, url: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a spec from a URL, validate, and cache it."""
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            spec_content = response.text

            # The fabric spec has a weird tag that pyyaml doesn't like.
            # It's a bug in their spec. We'll just remove it.
            spec_content = spec_content.replace("!!value", "")
            spec_content = spec_content.replace("example: =", "example: ''")
            spec_content = spec_content.replace("operator: =", "operator: ''")
            spec_content = spec_content.replace("- =", "- ''")

            # Use FullLoader to handle more complex YAML tags.
            spec = yaml.load(spec_content, Loader=yaml.FullLoader)

            if spec.get("swagger") == "2.0":
                logger.info(f"Converting Swagger 2.0 spec to OpenAPI 3.x for {url}")
                spec = self.converter.convert(spec)

            # We are disabling validation for now, as it is too strict for the current specs.
            # validate(spec)
            self.save_cached_spec(spec_key, spec)
            logger.info(f"Successfully fetched and validated spec from {url}")
            return spec
        except httpx2.HTTPStatusError as e:
            logger.error(f"HTTP error fetching spec from {url}: {e}")
        except yaml.YAMLError as e:
            logger.error(f"YAML parsing error for spec from {url}: {e}")
            return None  # Return None to prevent malformed data from proceeding
        except Exception as e:
            logger.error(f"Failed to fetch or validate spec from {url}: {e}")
            return None

        # If all fails, try to load from cache as a last resort.
        return self.load_cached_spec(spec_key)

    def _merge_api_spec(
        self, spec: Dict[str, Any], api_config: APIConfig
    ) -> Dict[str, Any]:
        """Apply overlays and transformations to a specification."""
        if not api_config.name:
            return spec

        for spec_source in api_config.specs:
            if spec_source.overlay:
                # Overlay paths resolve against the config's base directory
                overlay_path = self.config.resolve_path(spec_source.overlay)
                if overlay_path.exists():
                    with open(overlay_path, "r") as f:
                        overlay = yaml.safe_load(f)
                        spec = self.overlay_manager.apply(
                            spec, api_config.name, overlay
                        )
        return spec

    def _promote_auth_header_to_security(self, spec: Dict[str, Any]) -> None:
        """Normalize auth using OAS3 security with a root default.

        Strategy:
        - Remove Authorization header parameters (global or inline) from operations.
        - Assume a root-level default security is present (e.g., BearerAuth) via overlay or config.
        - For operations that DID have an Authorization header: do NOT set per-op security;
            they inherit the root default.
        - For operations that did NOT have an Authorization header and don't already specify
            security, explicitly set `security: []` to opt-out of the root default.
        - If no bearer scheme exists at all, create a minimal BearerAuth scheme to keep spec valid.
        """
        if not isinstance(spec, dict):
            return

        components = spec.get("components", {}) or {}
        comp_params: Dict[str, Any] = components.get("parameters", {}) or {}

        # Identify candidate global Authorization header parameters
        auth_param_keys: set[str] = set()
        for key, param in comp_params.items():
            if not isinstance(param, dict):
                continue
            loc = param.get("in")
            name = str(param.get("name", ""))
            x_prefix = param.get("x-prefix") or param.get("x_prefix")
            if (
                loc == "header"
                and name.lower() == "authorization"
                and (
                    x_prefix is None
                    or str(x_prefix).strip().lower().startswith("bearer")
                )
            ):
                auth_param_keys.add(key)

        # Ensure at least one bearer scheme exists to validate references if needed.
        sec_schemes: Dict[str, Any] = components.get("securitySchemes", {}) or {}
        if not any(
            isinstance(v, dict)
            and v.get("type") == "http"
            and v.get("scheme") == "bearer"
            for v in sec_schemes.values()
        ):
            sec_schemes.setdefault(
                "BearerAuth",
                {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            )
            components.setdefault("securitySchemes", sec_schemes)
            spec.setdefault("components", components)

        paths: Dict[str, Any] = spec.get("paths", {}) or {}
        if not paths:
            return

        # Helper to test if a parameter dict is an Authorization header
        def _is_inline_auth_param(p: Dict[str, Any]) -> bool:
            if not isinstance(p, dict):
                return False
            if p.get("in") != "header":
                return False
            name = str(p.get("name", ""))
            if name.lower() != "authorization":
                return False
            xp = p.get("x-prefix") or p.get("x_prefix")
            # Accept either explicit bearer prefix or any Authorization header
            return xp is None or str(xp).strip().lower().startswith("bearer")

        # Iterate operations
        methods = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, op in list(path_item.items()):
                if method.lower() not in methods or not isinstance(op, dict):
                    continue

                params = op.get("parameters", []) or []
                new_params = []
                found_auth = False
                for p in params:
                    if (
                        isinstance(p, dict)
                        and "$ref" in p
                        and isinstance(p["$ref"], str)
                    ):
                        ref = p["$ref"]
                        # Normalize possible refs to components.parameters
                        if ref.startswith("#/components/parameters/"):
                            key = ref.split("/")[-1]
                            if key in auth_param_keys:
                                found_auth = True
                                continue  # drop this param
                    # Check inline param shape
                    if isinstance(p, dict) and _is_inline_auth_param(p):
                        found_auth = True
                        continue  # drop inline auth param
                    new_params.append(p)

                if found_auth:
                    # Remove the auth param; allow operation to inherit root-level default
                    if new_params:
                        op["parameters"] = new_params
                    else:
                        op.pop("parameters", None)
                else:
                    # No auth header existed originally; explicitly opt-out of default
                    # unless operation already specifies security
                    if "security" not in op:
                        op["security"] = []

        # If we removed all uses of a global auth parameter, we can optionally keep it for docs
        # but no need to delete it here.

    def get_merged_spec(self) -> Dict[str, Any]:
        """Merge all individual API specs into a single spec."""
        all_specs = self.get_all_merged_specs()

        # Start with a base structure for the merged spec
        merged_spec: Dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {
                "title": "Equinix MCP Combined API",
                "version": "1.0.0",
                "description": "A merged API of all enabled Equinix services.",
            },
            "servers": [{"url": "https://api.equinix.com"}],
            "paths": {},
            "components": {},
            "security": [],
            "$defs": {},  # for schema components
        }

        all_security_schemes = []

        if not all_specs:
            # Clean up empty keys before returning
            if not merged_spec["components"]:
                del merged_spec["components"]
            if not merged_spec["$defs"]:
                del merged_spec["$defs"]
            return merged_spec

        # Merge all specs
        for api_name, spec in all_specs.items():
            if not spec:
                continue

            # Collect security requirements
            if "security" in spec:
                all_security_schemes.extend(spec["security"])

            # Prefix all operationIds with the API name and filter paths
            if "paths" in spec:
                # First, apply include/exclude filtering
                filtered_paths = self._filter_paths_by_operation_id(
                    api_name, spec["paths"]
                )

                # Then prefix operationIds
                for path, path_item in filtered_paths.items():
                    for method, operation in path_item.items():
                        if isinstance(operation, dict) and "operationId" in operation:
                            operation["operationId"] = (
                                f"{api_name}_{operation['operationId']}"
                            )

                merged_spec.setdefault("paths", {}).update(filtered_paths)

            if "components" in spec:
                # Schemas go into $defs
                if "schemas" in spec["components"]:
                    deep_merge(
                        merged_spec.setdefault("$defs", {}),
                        spec["components"]["schemas"],
                    )

                # Other components go into components
                for component_type, components in spec["components"].items():
                    if component_type != "schemas":
                        deep_merge(
                            merged_spec.setdefault("components", {}).setdefault(
                                component_type, {}
                            ),
                            components,
                        )

            if "$defs" in spec:
                deep_merge(merged_spec.setdefault("$defs", {}), spec["$defs"])

        # Add unique security schemes to the merged spec
        if all_security_schemes:
            unique_schemes = []
            seen = set()
            for scheme in all_security_schemes:
                # Convert dict to a hashable tuple of tuples
                scheme_tuple = tuple(sorted((k, tuple(v)) for k, v in scheme.items()))
                if scheme_tuple not in seen:
                    unique_schemes.append(scheme)
                    seen.add(scheme_tuple)
            merged_spec["security"] = unique_schemes

        # Update all schema references to point to $defs
        merged_spec = _update_references(merged_spec)

        # Clean up empty keys before returning
        if not merged_spec.get("components"):
            merged_spec.pop("components", None)
        if not merged_spec.get("$defs"):
            merged_spec.pop("$defs", None)

        return merged_spec

    def _apply_autogen_operation_ids(self, spec: Dict[str, Any]) -> None:
        """Generate operationId values for operations that lack them.

        Rules:
        - GET on a path ending in a parameter -> get{CamelCase(basename)},
          otherwise list{CamelCase(basename)}.
        - Other methods -> {verb}{CamelCase(basename)}.
        - Basename is the last non-parameter path segment
          (e.g. /a/b/{id} -> b).
        - Uniqueness is ensured by appending numeric suffixes.
        """
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            return

        def camel(s: str) -> str:
            parts = re.split(r"[^A-Za-z0-9]+", s)
            return "".join(p.capitalize() for p in parts if p)

        def basename_and_param_flag(p: str) -> tuple[str, bool]:
            segs = [seg for seg in p.split("/") if seg]
            if not segs:
                return ("Root", False)
            last = segs[-1]
            is_param = bool(re.match(r"^{.+}$", last))
            if is_param:
                base = segs[-2] if len(segs) >= 2 else "Root"
            else:
                base = last
            return (base, is_param)

        methods_allowed = {"get", "put", "post", "delete", "patch", "options", "head"}

        existing = set()
        for methods in paths.values():
            if not isinstance(methods, dict):
                continue
            for op in methods.values():
                if isinstance(op, dict) and op.get("operationId"):
                    existing.add(op["operationId"])

        for p, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() not in methods_allowed or not isinstance(op, dict):
                    continue
                if op.get("operationId"):
                    continue

                base, ends_with_param = basename_and_param_flag(p)
                if method.lower() == "get":
                    candidate = (
                        f"get{camel(base)}" if ends_with_param else f"list{camel(base)}"
                    )
                else:
                    candidate = f"{method.lower()}{camel(base)}"

                if candidate in existing:
                    i = 2
                    while f"{candidate}{i}" in existing:
                        i += 1
                    candidate = f"{candidate}{i}"

                op["operationId"] = candidate
                existing.add(candidate)

    # ECMAScript named group syntax "(?<name>" that Python's regex validator
    # rejects; must not touch lookbehinds "(?<=" / "(?<!".
    _JS_NAMED_GROUP = re.compile(r"\(\?<(?![=!])")

    def _sanitize_schema_quirks(self, node: Any) -> None:
        """Fix common spec authoring bugs in place.

        Found across the api-catalog by scripts/vet_specs.py:
        - `type: file` is Swagger 2.0 syntax; several catalog specs declare
          openapi 3.0 but still use it (billingv1, reports). OpenAPI 3
          expresses binary payloads as string/binary.
        - `pattern` regexes with ECMAScript named groups `(?<name>...)`
          (access): JSON Schema validators built on Python's re require
          `(?P<name>...)`.
        - Boolean `exclusiveMaximum`/`exclusiveMinimum` (attachments) are
          JSON Schema draft-4 syntax; tool schemas are validated as 2020-12
          where they must be numeric. Converting to the numeric form would
          break the OpenAPI 3.0 document parse (a catch-22 between the two
          dialects), so drop the boolean and keep the inclusive bound.
        - Swagger 2.0 `collectionFormat` left inside OpenAPI 3 array schemas
          (assets) is not a JSON Schema keyword and would leak into tool
          input schemas; OpenAPI 3 expresses it with parameter `style`.
        """
        if isinstance(node, dict):
            if node.get("type") == "file":
                node["type"] = "string"
                node["format"] = "binary"

            pattern = node.get("pattern")
            if isinstance(pattern, str) and "(?<" in pattern:
                node["pattern"] = self._JS_NAMED_GROUP.sub("(?P<", pattern)

            for exclusive in ("exclusiveMaximum", "exclusiveMinimum"):
                if isinstance(node.get(exclusive), bool):
                    node.pop(exclusive)

            if isinstance(node.get("collectionFormat"), str) and "items" in node:
                node.pop("collectionFormat")

            for value in node.values():
                self._sanitize_schema_quirks(value)
        elif isinstance(node, list):
            for item in node:
                self._sanitize_schema_quirks(item)

    def get_provider_spec(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Load one API family's spec, ready for provider registration.

        Applies the configured include/exclude operationId filters but leaves
        operationIds and component schemas untouched: per-family namespacing
        is handled at provider registration, not by rewriting the spec.
        """
        spec = self.load_merged_spec(api_name)
        if not spec:
            return None
        if "paths" in spec:
            spec["paths"] = self._filter_paths_by_operation_id(api_name, spec["paths"])
        return spec

    def get_merged_spec_path(self, api_name: str) -> Path:
        """Get the path to the merged spec file."""
        return self.cache_dir / f"{api_name}-merged.yaml"

    def save_merged_spec(self, api_name: str, spec: Dict[str, Any]) -> None:
        """Save a merged specification to the cache."""
        path = self.get_merged_spec_path(api_name)
        with open(path, "w") as f:
            yaml.dump(spec, f, sort_keys=False)
        logger.info(f"Saved merged spec for {api_name} to {path}")

    def load_merged_spec(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Load a merged specification from the cache."""
        path = self.get_merged_spec_path(api_name)
        if path.exists():
            with open(path, "r") as f:
                return yaml.safe_load(f)
        return None

    def get_cached_spec_path(self, spec_key: str) -> Path:
        """Get the path to a cached specification file."""
        return self.cache_dir / f"{spec_key}.yaml"

    def save_cached_spec(self, spec_key: str, spec: Dict[str, Any]) -> None:
        """Save a fetched specification to the cache."""
        path = self.get_cached_spec_path(spec_key)
        with open(path, "w") as f:
            yaml.dump(spec, f, sort_keys=False)
        logger.info(f"Cached spec {spec_key} to {path}")

    def load_cached_spec(self, spec_key: str) -> Optional[Dict[str, Any]]:
        """Load a cached specification."""
        path = self.get_cached_spec_path(spec_key)
        if path.exists():
            logger.warning(f"Using cached spec for {spec_key} from {path}")
            with open(path, "r") as f:
                return yaml.safe_load(f)
        logger.error(f"No cached spec found for {spec_key}")
        return None

    def get_all_merged_specs(self) -> Dict[str, Dict[str, Any]]:
        """Load all merged specifications from the cache."""
        specs = {}
        for api_name in self.config.apis.keys():
            spec = self.load_merged_spec(api_name)
            if spec:
                specs[api_name] = spec
        return specs

    def _filter_paths_by_operation_id(
        self, api_name: str, paths: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Filter paths based on include/exclude patterns for operation IDs."""
        api_config = self.config.get_api_config(api_name)
        if not api_config or (not api_config.include and not api_config.exclude):
            return paths

        import re

        filtered_paths = {}

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            filtered_path_item = {}
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or "operationId" not in operation:
                    filtered_path_item[method] = operation
                    continue

                operation_id = operation["operationId"]

                # Check include patterns (if any)
                if api_config.include:
                    included = False
                    for pattern in api_config.include:
                        if re.search(pattern, operation_id, re.IGNORECASE):
                            included = True
                            break
                    if not included:
                        continue

                # Check exclude patterns (if any)
                if api_config.exclude:
                    excluded = False
                    for pattern in api_config.exclude:
                        if re.search(pattern, operation_id, re.IGNORECASE):
                            excluded = True
                            break
                    if excluded:
                        continue

                # Operation passed all filters
                filtered_path_item[method] = operation

            # Only include path if it has at least one operations
            if filtered_path_item:
                filtered_paths[path] = filtered_path_item

        return filtered_paths

    def has_all_cached_specs(self) -> bool:
        """Check if all enabled APIs have cached merged specifications."""
        for api_name, api_config in self.config.apis.items():
            if api_config.enabled:
                merged_spec_path = self.get_merged_spec_path(api_name)
                if not merged_spec_path.exists():
                    return False
        return True
