"""OpenAPI Discovery module for indexing and searching API specifications."""

import logging
from typing import Any, Dict, List, Optional

from .config import Config
from .spec_manager import SpecManager

logger = logging.getLogger(__name__)

class DiscoveryManager:
    """Manages discovery of OpenAPI elements (tags, operations, paths)."""

    def __init__(self, config: Config):
        self.config = config
        self.spec_manager = SpecManager(config)
        self._index: Optional[Dict[str, Any]] = None

    async def ensure_index(self):
        """Ensure the search index is built."""
        if self._index is not None:
            return

        # Ensure specs are loaded
        if not self.spec_manager.has_all_cached_specs():
            await self.spec_manager.update_specs()
        
        merged_spec = self.spec_manager.get_merged_spec()
        self._build_index(merged_spec)

    def _build_index(self, spec: Dict[str, Any]):
        """Build search index from OpenAPI spec."""
        self._index = {
            "tags": {},
            "operations": {},
            "paths": {}
        }

        # Index Tags
        if "tags" in spec:
            for tag in spec["tags"]:
                name = tag.get("name")
                if name:
                    self._index["tags"][name] = tag

        # Index Paths and Operations
        if "paths" in spec:
            for path, path_item in spec["paths"].items():
                self._index["paths"][path] = path_item
                
                for method, operation in path_item.items():
                    if method in ["get", "put", "post", "delete", "patch", "head", "options", "trace"]:
                        op_id = operation.get("operationId")
                        if op_id:
                            # Store full operation context
                            self._index["operations"][op_id] = {
                                "method": method,
                                "path": path,
                                "details": operation
                            }
                        
                        # Also index tags from operations if not already present
                        for tag_name in operation.get("tags", []):
                            if tag_name not in self._index["tags"]:
                                self._index["tags"][tag_name] = {"name": tag_name}

    async def search_api(self, query: str, limit: int = 10) -> str:
        """Search API elements (tags, operations, paths)."""
        await self.ensure_index()
        
        query = query.lower()
        results = []

        # Search Tags
        for name, tag in self._index["tags"].items():
            if query in name.lower() or query in tag.get("description", "").lower():
                results.append(f"TAG: {name} - {tag.get('description', 'No description')}")

        # Search Operations
        for op_id, op_data in self._index["operations"].items():
            op = op_data["details"]
            if (query in op_id.lower() or 
                query in op.get("summary", "").lower() or 
                query in op.get("description", "").lower()):
                results.append(f"OP: {op_id} ({op_data['method'].upper()} {op_data['path']}) - {op.get('summary', '')}")

        # Search Paths
        for path in self._index["paths"].keys():
            if query in path.lower():
                results.append(f"PATH: {path}")

        # Limit results
        return "\n".join(results[:limit])

    async def fetch_api(self, target: str) -> str:
        """Fetch details for a specific API element (tag, operationId, or path)."""
        await self.ensure_index()
        
        import yaml

        # Check Operations
        if target in self._index["operations"]:
            return yaml.dump(self._index["operations"][target], sort_keys=False)

        # Check Tags
        if target in self._index["tags"]:
            return yaml.dump(self._index["tags"][target], sort_keys=False)

        # Check Paths
        if target in self._index["paths"]:
            return yaml.dump(self._index["paths"][target], sort_keys=False)

        return f"Target '{target}' not found in API index."
