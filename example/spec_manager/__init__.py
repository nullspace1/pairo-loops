"""Specification manager MCP integration."""

from .spec_manager import (
    AcceptanceCriterion,
    AcceptanceEvidence,
    CorrectionResolution,
    SpecManager,
    SpecManagerInstaller,
    SpecificationChangeRequest,
    TestRunSummary,
    VerificationEvidence,
    VerificationCorrection,
    WorkerVerifierPair,
    install_spec_manager,
)

__all__ = [
    "AcceptanceCriterion",
    "AcceptanceEvidence",
    "CorrectionResolution",
    "SpecManager",
    "SpecManagerInstaller",
    "SpecificationChangeRequest",
    "TestRunSummary",
    "VerificationEvidence",
    "VerificationCorrection",
    "WorkerVerifierPair",
    "install_spec_manager",
]
