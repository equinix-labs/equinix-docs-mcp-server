"""Response formatting utilities using JQ transformations and YAML serialization."""

import json
import logging
from typing import Any, Dict, List, Optional, Union

import yaml

from .config import Config

logger = logging.getLogger(__name__)


class ResponseFormatter:
    """Formats API responses using JQ transformations and serializes as YAML."""

    def __init__(self, config: Config):
        self.config = config
        self.jq_cache: Dict[str, Any] = {}
        self._current_operation_id: Optional[str] = None

    def set_operation_context(self, operation_id: Optional[str]) -> None:
        """Set the current operation context for formatting decisions."""
        self._current_operation_id = operation_id

    def _get_jq_program(self, jq_filter: str) -> Any:
        """Get compiled JQ program from cache or compile it."""
        if jq_filter in self.jq_cache:
            return self.jq_cache[jq_filter]

        try:
            import jq

            program = jq.compile(jq_filter)
            self.jq_cache[jq_filter] = program
            return program
        except ImportError:
            logger.warning("JQ library not available, response formatting disabled")
            return None
        except Exception as e:
            logger.error(f"Failed to compile JQ filter '{jq_filter}': {e}")
            return None

    def _apply_jq_filters(self, data: Any, filters: List[str]) -> Any:
        """Apply a list of JQ filters in sequence."""
        result = data
        logger.debug(
            f"Starting JQ transformation with {len(filters)} filters, input type: {type(data)}"
        )

        for i, jq_filter in enumerate(filters):
            try:
                logger.debug(f"Applying JQ filter {i+1}/{len(filters)}: {jq_filter}")
                program = self._get_jq_program(jq_filter)
                if program is None:
                    logger.warning(f"JQ program is None for filter: {jq_filter}")
                    continue

                # Apply the JQ transformation using the correct API
                jq_result = program.input(result).all()
                logger.debug(
                    f"JQ raw result type: {type(jq_result)}, value: {repr(jq_result)[:200] if jq_result else 'None'}"
                )

                # JQ returns a list of results, get the first one if single result
                if isinstance(jq_result, list) and len(jq_result) == 1:
                    result = jq_result[0]
                    logger.debug(f"Extracted single result from list: {type(result)}")
                else:
                    result = jq_result
                    logger.debug(f"Using raw result: {type(result)}")

                logger.debug(
                    f"After filter {i+1}, result type: {type(result)}, value: {repr(result)[:200] if result else 'None'}"
                )

            except Exception as e:
                logger.error(f"JQ transformation failed with filter '{jq_filter}': {e}")
                logger.error(f"Input data type: {type(result)}")
                # Continue with the current result if transformation fails
                continue

        logger.debug(
            f"Final JQ result type: {type(result)}, value: {repr(result)[:200] if result else 'None'}"
        )
        return result

    def _get_format_config(
        self, operation_id: str
    ) -> Optional[Union[str, List[str], Dict[str, str]]]:
        """Get format configuration for an operation ID."""
        # Operation IDs generated from OpenAPI tools are typically prefixed
        # with the API name, e.g. "metal_findMetros" or "fabric_searchConnections".
        # However, some prefixes may contain hyphens ("network-edge") or be
        # normalized differently by the tool generator (e.g. "network_edge",
        # "ne", etc.). Instead of relying on a single split, try to find the
        # best-matching API config by scanning configured API names and
        # attempting several common normalizations.

        # Prefer double-underscore separator ("{api}__{operation}") but
        # fall back to single underscore for backward compatibility.
        if not operation_id or ("__" not in operation_id and "_" not in operation_id):
            return None

        sep = "__" if "__" in operation_id else "_"
        logger.debug(f"Using separator '{sep}' to parse operation_id={operation_id}")

        # Candidate suffix after the separator is the original operation id
        _, original_operation_id = operation_id.split(sep, 1)

        # Try to find a matching API config key. Prefer exact prefix match,
        # then try normalized variants (replace '-' <-> '_', remove dashes,
        # or match by last token).
        api_config = None

        # Normalize operation prefix for reliable matching: take the part before sep
        prefix = operation_id.split(sep, 1)[0]

        def normalize_name(name: str) -> str:
            # Lowercase, replace hyphens with underscores, collapse non-alnum to underscore
            import re

            s = name.lower().replace("-", "_")
            s = re.sub(r"[^a-z0-9_]+", "_", s)
            return s

        norm_prefix = normalize_name(prefix)

        for candidate in self.config.get_api_names():
            norm_candidate = normalize_name(candidate)

            # Direct match
            if norm_prefix == norm_candidate:
                api_config = self.config.get_api_config(candidate)
                break

            # Try compact alphanumeric candidate match
            compact = "".join([c for c in candidate if c.isalnum()]).lower()
            if norm_prefix == compact:
                api_config = self.config.get_api_config(candidate)
                break

            # Try last token of candidate (e.g., network-edge -> edge)
            parts = candidate.replace("_", "-").split("-")
            if parts and normalize_name(parts[-1]) == norm_prefix:
                api_config = self.config.get_api_config(candidate)
                break

        if not api_config or not api_config.format:
            logger.debug(f"No format config found for operation_id={operation_id}")
            return None

        fmt = api_config.format.get(original_operation_id)
        logger.debug(
            f"Matched format for operation_id={operation_id} -> api={api_config.name} op={original_operation_id} fmt_exists={fmt is not None}"
        )
        return fmt

    def format_response(self, operation_id: str, response_data: Any) -> Any:
        """Format response data using configured JQ transformation."""
        format_config = self._get_format_config(operation_id)

        if not format_config:
            return response_data

        logger.info(f"Applying format configuration to {operation_id}")

        try:
            if isinstance(format_config, str):
                # Single JQ filter
                return self._apply_jq_filters(response_data, [format_config])

            elif isinstance(format_config, list):
                # List of JQ filters to apply in sequence
                return self._apply_jq_filters(response_data, format_config)

            elif isinstance(format_config, dict):
                # Named formats - use 'default' if available, otherwise first key
                filter_name = (
                    "default"
                    if "default" in format_config
                    else next(iter(format_config))
                )
                jq_filter = format_config[filter_name]
                logger.info(f"Using format '{filter_name}' for {operation_id}")

                if isinstance(jq_filter, str):
                    return self._apply_jq_filters(response_data, [jq_filter])
                elif isinstance(jq_filter, list):
                    return self._apply_jq_filters(response_data, jq_filter)

            return response_data

        except Exception as e:
            logger.error(f"Format transformation failed for {operation_id}: {e}")
            # Return original data if transformation fails
            return response_data

    def __call__(self, data: Any) -> str:
        """FastMCP serializer interface - formats and serializes response data."""
        # Apply JQ formatting if we have operation context
        if self._current_operation_id:
            data = self.format_response(self._current_operation_id, data)

        # Serialize as YAML for better LLM readability
        try:
            return yaml.dump(
                data, sort_keys=False, default_flow_style=False, allow_unicode=True
            )
        except Exception as e:
            logger.error(f"YAML serialization failed, falling back to JSON: {e}")
            try:
                return json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as json_error:
                logger.error(f"JSON serialization also failed: {json_error}")
                return str(data)
