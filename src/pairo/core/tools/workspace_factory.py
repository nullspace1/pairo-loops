"""Compose an agent workspace from links to existing directories."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess

from pairo.core.agent.agent import AgentConfiguration
from pairo.core.agent.sandbox_permission import SandboxPermission


@dataclass(frozen=True)
class _LinkedWorkspace:
    """A source directory exposed through the factory workspace."""

    source: Path
    link: Path
    permission: SandboxPermission


class _DirectoryLinkCreator:
    """Create directory links without requiring Windows symlink privileges."""

    _WINDOWS_SYMLINK_PRIVILEGE_ERROR = 1314

    def create(self, source: Path, link: Path) -> None:
        try:
            link.symlink_to(source, target_is_directory=True)
        except OSError as error:
            if not self._requires_windows_junction(error):
                raise
            self._create_windows_junction(source, link)

    def _requires_windows_junction(self, error: OSError) -> bool:
        return (
            os.name == "nt"
            and error.winerror == self._WINDOWS_SYMLINK_PRIVILEGE_ERROR
        )

    def _create_windows_junction(self, source: Path, link: Path) -> None:
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(source)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise OSError(f"Unable to create directory junction: {detail}")


class WorkspaceFactory:
    """Build and apply a directory-link-based workspace configuration."""

    def __init__(
        self,
        root: str | Path,
        permission: SandboxPermission,
    ) -> None:
        self._root = Path(root).resolve()
        self._permission = permission
        self._workspaces: list[_LinkedWorkspace] = []
        self._link_creator = _DirectoryLinkCreator()
        self._create_root()

    @property
    def root(self) -> Path:
        return self._root

    def add_workspace(
        self,
        path: str | Path,
        permission: SandboxPermission,
    ) -> None:
        source = Path(path).resolve()
        self._validate_source(source)
        self._validate_permission(permission)

        link = self._root / source.name
        self._remove_existing_link(link)
        try:
            self._link_creator.create(source, link)
        except OSError as error:
            raise OSError(
                f"Unable to link workspace '{source}' at '{link}': {error}"
            ) from error

        self._workspaces.append(_LinkedWorkspace(source, link, permission))

    def configure(self, agent: AgentConfiguration) -> None:
        """Apply this workspace and its permissions to an agent."""
        self._validate_managed_paths()
        agent.set_workspace(self._root)
        agent.set_sandbox(self._permission)
        for workspace in self._workspaces:
            agent.add_folder_permission(workspace.source, workspace.permission)

    def _create_root(self) -> None:
        if self._root.exists() and not self._root.is_dir():
            raise ValueError(f"Workspace root '{self._root}' must be a directory.")
        self._root.mkdir(parents=True, exist_ok=True)

    def _validate_source(self, source: Path) -> None:
        if not source.is_dir():
            raise ValueError(f"Workspace source '{source}' must be an existing directory.")
        if not source.name:
            raise ValueError(f"Workspace source '{source}' must have a directory name.")

    def _validate_permission(self, permission: SandboxPermission) -> None:
        if permission is SandboxPermission.DANGER_FULL_ACCESS:
            raise ValueError(
                "DANGER_FULL_ACCESS cannot be assigned to an individual workspace."
            )

    def _remove_existing_link(self, link: Path) -> None:
        if not os.path.lexists(link):
            return
        if link.is_symlink():
            link.unlink()
            return
        if self._is_windows_junction(link):
            link.rmdir()
            return
        if link.exists():
            raise FileExistsError(f"Workspace link '{link}' already exists.")

    def _is_windows_junction(self, link: Path) -> bool:
        if os.name != "nt":
            return False
        attributes = getattr(link.lstat(), "st_file_attributes", 0)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        directory = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)
        return bool(attributes & reparse_point and attributes & directory)

    def _validate_managed_paths(self) -> None:
        if not self._root.is_dir():
            raise ValueError(f"Workspace root '{self._root}' is no longer available.")
        for workspace in self._workspaces:
            if not workspace.source.is_dir():
                raise ValueError(
                    f"Workspace source '{workspace.source}' is no longer available."
                )
            if not workspace.link.is_dir():
                raise ValueError(
                    f"Workspace link '{workspace.link}' is no longer available."
                )
            if workspace.link.resolve() != workspace.source:
                raise ValueError(
                    f"Workspace link '{workspace.link}' no longer targets "
                    f"'{workspace.source}'."
                )


__all__ = ["WorkspaceFactory"]
