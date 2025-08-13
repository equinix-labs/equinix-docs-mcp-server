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
        logger.debug(f"Starting JQ transformation with {len(filters)} filters, input type: {type(data)}")
        
        for i, jq_filter in enumerate(filters):
            try:
                logger.debug(f"Applying JQ filter {i+1}/{len(filters)}: {jq_filter}")
                program = self._get_jq_program(jq_filter)
                if program is None:
                    logger.warning(f"JQ program is None for filter: {jq_filter}")
                    continue

                # Apply the JQ transformation using the correct API
                jq_result = program.input(result).all()
                logger.debug(f"JQ raw result type: {type(jq_result)}, value: {repr(jq_result)[:200] if jq_result else 'None'}")
                
                # JQ returns a list of results, get the first one if single result
                if isinstance(jq_result, list) and len(jq_result) == 1:
                    result = jq_result[0]
                    logger.debug(f"Extracted single result from list: {type(result)}")
                else:
                    result = jq_result
                    logger.debug(f"Using raw result: {type(result)}")
                
                logger.debug(f"After filter {i+1}, result type: {type(result)}, value: {repr(result)[:200] if result else 'None'}")

            except Exception as e:
                logger.error(f"JQ transformation failed with filter '{jq_filter}': {e}")
                logger.error(f"Input data type: {type(result)}")
                # Continue with the current result if transformation fails
                continue
                
        logger.debug(f"Final JQ result type: {type(result)}, value: {repr(result)[:200] if result else 'None'}")
        return result

    def _get_format_config(self, operation_id: str) -> Optional[Union[str, List[str], Dict[str, str]]]:
        """Get format configuration for an operation ID."""
        if "_" not in operation_id:
            return None

        api_name, original_operation_id = operation_id.split("_", 1)
        api_config = self.config.get_api_config(api_name)

        if not api_config or not api_config.format:
            return None

        return api_config.format.get(original_operation_id)

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
                filter_name = "default" if "default" in format_config else next(iter(format_config))
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
            return yaml.dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            logger.error(f"YAML serialization failed, falling back to JSON: {e}")
            try:
                return json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as json_error:
                logger.error(f"JSON serialization also failed: {json_error}")
                return str(data)
