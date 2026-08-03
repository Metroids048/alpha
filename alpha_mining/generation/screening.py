"""Shared candidate screening policy for production and offline pipelines.

Both CandidateGenerationService (production) and alpha_mining.offline.service
must route candidates through this module to ensure consistent rejection logic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from alpha_mining.domain.expression_ast import parse_expression, ExpressionSyntaxError
from alpha_mining.domain.expression_normalization import expression_identity
from alpha_mining.generation.validation import ExpressionCatalog


class RejectionReason(enum.Enum):
    NONE = "NONE"
    INVALID_SYNTAX = "INVALID_SYNTAX"
    UNKNOWN_OPERATOR = "UNKNOWN_OPERATOR"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_ARITY = "INVALID_ARITY"
    FIELD_DATASET_MISMATCH = "FIELD_DATASET_MISMATCH"
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
    CATALOG_STALE = "CATALOG_STALE"
    CATALOG_CONTEXT_MISMATCH = "CATALOG_CONTEXT_MISMATCH"
    KNOWLEDGE_MISSING = "KNOWLEDGE_MISSING"
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
    catalog: ExpressionCatalog | None = None
    expected_dataset_id: str | None = None
    region: str | None = None
    universe: str | None = None
    delay: int | str | None = None

    def screen_expression(
        self,
        expression: str,
        *,
        round_seen_hashes: set[str],
        round_seen_skeletons: set[str],
        expected_dataset_id: str | None = None,
        region: str | None = None,
        universe: str | None = None,
        delay: int | str | None = None,
    ) -> RejectionReason | None:
        """Return a RejectionReason if the expression should be rejected, else RejectionReason.NONE.

        Callers should treat both None and RejectionReason.NONE as "passed".
        """
        # Catalog validation is deliberately first: an untrusted expression must
        # never reach identity creation, request claiming, or a platform gateway.
        dataset_id = expected_dataset_id or self.expected_dataset_id
        context_required = any(
            value is not None
            for value in (dataset_id, region or self.region, universe or self.universe, delay if delay is not None else self.delay)
        )
        if self.catalog is None:
            # Standalone identity tools retain their legacy syntax-only use.
            # The production service always provides dataset/context and thus
            # takes this fail-closed path when no read-only catalog is present.
            if context_required:
                return RejectionReason.CATALOG_UNAVAILABLE
        else:
            try:
                issues = self.catalog.validate(
                    expression,
                    expected_dataset_id=dataset_id,
                    region=region or self.region,
                    universe=universe or self.universe,
                    delay=delay if delay is not None else self.delay,
                )
            except Exception:
                return RejectionReason.CATALOG_UNAVAILABLE
            if issues:
                code = str(issues[0].code)
                if code == "FASTPLUS":
                    code = "INVALID_SYNTAX"
                try:
                    return RejectionReason(code)
                except ValueError:
                    return RejectionReason.CATALOG_UNAVAILABLE

        # Syntax check remains explicit for catalog implementations that only
        # validate metadata and do not parse expressions themselves.
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
