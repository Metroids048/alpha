"""Shared candidate screening policy for production and offline pipelines.

Both CandidateGenerationService (production) and alpha_mining.offline.service
must route candidates through this module to ensure consistent rejection logic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from alpha_mining.domain.expression_ast import parse_expression, ExpressionSyntaxError
from alpha_mining.domain.expression_normalization import expression_identity


class RejectionReason(enum.Enum):
    NONE = "NONE"
    INVALID_SYNTAX = "INVALID_SYNTAX"
    UNKNOWN_OPERATOR = "UNKNOWN_OPERATOR"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    GROUP_RANK_DISABLED = "GROUP_RANK_DISABLED"
    EXACT_HASH_EXISTS = "EXACT_HASH_EXISTS"
    FIELD_SKELETON_ROUND_LIMIT = "FIELD_SKELETON_ROUND_LIMIT"
    FIELD_SKELETON_ACTIVE_REQUEST = "FIELD_SKELETON_ACTIVE_REQUEST"
    FAMILY_COOLDOWN = "FAMILY_COOLDOWN"
    INVALID_IDENTITY = "INVALID_IDENTITY"


def _has_group_rank(expression: str) -> bool:
    try:
        from alpha_mining.domain.expression_ast import AstNode

        def _walk(node: AstNode) -> bool:
            if node.kind == "call" and str(node.value).startswith("group_"):
                return True
            return any(_walk(child) for child in node.children)

        return _walk(parse_expression(expression))
    except Exception:
        return False


@dataclass
class CandidateScreeningPolicy:
    """Stateless screening policy; callers maintain per-round seen sets."""

    group_rank_enabled: bool = False
    max_field_skeleton_per_round: int = 1

    def screen_expression(
        self,
        expression: str,
        *,
        round_seen_hashes: set[str],
        round_seen_skeletons: set[str],
    ) -> RejectionReason | None:
        """Return a RejectionReason if the expression should be rejected, else RejectionReason.NONE.

        Callers should treat both None and RejectionReason.NONE as "passed".
        """
        # Syntax check
        try:
            parse_expression(expression)
        except ExpressionSyntaxError:
            return RejectionReason.INVALID_SYNTAX
        except Exception:
            return RejectionReason.INVALID_SYNTAX

        # group_rank gate
        if not self.group_rank_enabled and _has_group_rank(expression):
            return RejectionReason.GROUP_RANK_DISABLED

        # Compute identity
        try:
            identity = expression_identity(expression)
        except Exception:
            return RejectionReason.INVALID_IDENTITY

        if not identity.exact_hash or not identity.field_skeleton:
            return RejectionReason.INVALID_IDENTITY

        # Exact hash deduplication (across history — callers must include historical hashes)
        if identity.exact_hash in round_seen_hashes:
            return RejectionReason.EXACT_HASH_EXISTS

        # Field skeleton per-round limit
        if identity.field_skeleton in round_seen_skeletons:
            return RejectionReason.FIELD_SKELETON_ROUND_LIMIT

        return RejectionReason.NONE
