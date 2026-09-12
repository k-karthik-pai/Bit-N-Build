"""Public interface for the circularity matching agent."""

from .agent import (
    CircularityError,
    DataValidationError,
    RequestValidationError,
    find_candidates,
    run_circularity,
    run_from_files,
)
from .knowledge_graph import (
    GraphEdge,
    GraphNode,
    MaterialBuyerGraph,
    MaterialBuyerPath,
    build_material_buyer_graph,
)

__all__ = [
    "CircularityError",
    "DataValidationError",
    "RequestValidationError",
    "find_candidates",
    "run_circularity",
    "run_from_files",
    "GraphEdge",
    "GraphNode",
    "MaterialBuyerGraph",
    "MaterialBuyerPath",
    "build_material_buyer_graph",
]
