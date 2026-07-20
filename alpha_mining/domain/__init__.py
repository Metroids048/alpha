"""Domain primitives shared by legacy and consultant pipelines."""

from .expression_normalization import (
    behavior_signature,
    normalized_expression,
    structure_signature,
)

__all__ = ["behavior_signature", "normalized_expression", "structure_signature"]
