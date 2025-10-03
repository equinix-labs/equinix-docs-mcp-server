"""OpenAPI overlay functionality for modifying OpenAPI specifications.

This package provides utilities for loading, creating, and applying overlays
to OpenAPI specifications. Overlays are used to modify or normalize API specs
before merging them into a unified specification.
"""

from .overlay_manager import OverlayManager

__all__ = ["OverlayManager"]
