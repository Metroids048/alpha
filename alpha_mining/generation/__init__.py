"""Candidate generation — offline utilities and production service."""

# Lazy imports to avoid circular dependency with offline.service
# (offline.service imports canonical_signature, canonical needs MetadataCache from offline)

def __getattr__(name: str):
    if name == "canonical_signature":
        from .canonical import canonical_signature
        return canonical_signature
    elif name == "canonical_skeleton":
        from .canonical import canonical_skeleton
        return canonical_skeleton
    elif name == "CandidateGenerationBatch":
        from .service import CandidateGenerationBatch
        return CandidateGenerationBatch
    elif name == "CandidateGenerationService":
        from .service import CandidateGenerationService
        return CandidateGenerationService
    elif name == "CandidateProposal":
        from .service import CandidateProposal
        return CandidateProposal
    elif name == "CandidateScreeningPolicy":
        from .screening import CandidateScreeningPolicy
        return CandidateScreeningPolicy
    elif name == "CandidateFeedbackStore":
        from .feedback import CandidateFeedbackStore
        return CandidateFeedbackStore
    elif name == "RejectionReason":
        from .screening import RejectionReason
        return RejectionReason
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "canonical_signature",
    "canonical_skeleton",
    "CandidateGenerationBatch",
    "CandidateGenerationService",
    "CandidateProposal",
    "CandidateScreeningPolicy",
    "CandidateFeedbackStore",
    "RejectionReason",
]
