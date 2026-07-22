"""Fail-closed Alpha description production subsystem."""

from .eligibility import EligibilityDecision, EligibilityStatus, classify_alpha
from .models import DescriptionStatus

__all__ = [
    "DescriptionStatus",
    "EligibilityDecision",
    "EligibilityStatus",
    "classify_alpha",
]
