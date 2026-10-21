"""Specification manager MCP integration."""

from .spec_manager import (
    AcceptanceEvidence,
    SpecManager,
    SpecManagerInstaller,
    TestRunSummary,
    VerificationEvidence,
    WorkerVerifierPair,
    install_spec_manager,
)

__all__ = [
    "AcceptanceEvidence",
    "SpecManager",
    "SpecManagerInstaller",
    "TestRunSummary",
    "VerificationEvidence",
    "WorkerVerifierPair",
    "install_spec_manager",
]
