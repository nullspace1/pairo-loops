"""Specification manager MCP integration."""

from .spec_manager import (
    SpecManager,
    SpecManagerInstaller,
    WorkerVerifierPair,
    install_spec_manager,
)

__all__ = [
    "SpecManager",
    "SpecManagerInstaller",
    "WorkerVerifierPair",
    "install_spec_manager",
]
