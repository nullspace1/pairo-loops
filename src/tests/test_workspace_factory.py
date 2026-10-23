"""WorkspaceFactory filesystem and configuration contracts."""

from pathlib import Path
from uuid import uuid4

import pytest

from pairo.core.agent import AgentConfiguration, SandboxPermission, WorkspaceFactory


def make_agent(tmp_path: Path) -> AgentConfiguration:
    return AgentConfiguration(
        id=uuid4(),
        name="developer",
        sandbox=SandboxPermission.READ_ONLY,
        workspace=tmp_path,
        model="test",
    )


def test_constructor_creates_root_and_rejects_file(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "workspace"
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)

    assert factory.root == root.resolve()
    assert root.is_dir()

    file_root = tmp_path / "file"
    file_root.write_text("content", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        WorkspaceFactory(file_root, SandboxPermission.READ_ONLY)


def test_add_workspace_creates_named_directory_link(tmp_path: Path) -> None:
    root = tmp_path / "root"
    source = tmp_path / "project"
    source.mkdir()
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)

    factory.add_workspace(source, SandboxPermission.READ_ONLY)

    link = root / "project"
    assert link.is_dir()
    assert link.resolve() == source.resolve()


def test_add_workspace_replaces_existing_directory_link(tmp_path: Path) -> None:
    root = tmp_path / "root"
    first_source = tmp_path / "first" / "project"
    replacement_source = tmp_path / "replacement" / "project"
    first_source.mkdir(parents=True)
    replacement_source.mkdir(parents=True)

    WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE).add_workspace(
        first_source,
        SandboxPermission.READ_ONLY,
    )
    WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE).add_workspace(
        replacement_source,
        SandboxPermission.WORKSPACE_WRITE,
    )

    assert (root / "project").resolve() == replacement_source.resolve()


def test_add_workspace_rejects_invalid_inputs_and_collisions(tmp_path: Path) -> None:
    root = tmp_path / "root"
    source = tmp_path / "project"
    source.mkdir()
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)

    with pytest.raises(ValueError, match="existing directory"):
        factory.add_workspace(tmp_path / "missing", SandboxPermission.READ_ONLY)
    with pytest.raises(ValueError, match="cannot be assigned"):
        factory.add_workspace(source, SandboxPermission.DANGER_FULL_ACCESS)

    collision = root / source.name
    collision.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        factory.add_workspace(source, SandboxPermission.READ_ONLY)
    assert collision.read_text(encoding="utf-8") == "preserve"


def test_configure_applies_workspace_sandbox_and_merged_permissions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    readable = tmp_path / "readable"
    writable = tmp_path / "writable"
    unrelated = tmp_path / "unrelated"
    readable.mkdir()
    writable.mkdir()
    unrelated.mkdir()
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)
    factory.add_workspace(readable, SandboxPermission.READ_ONLY)
    factory.add_workspace(writable, SandboxPermission.WORKSPACE_WRITE)
    agent = make_agent(tmp_path)
    agent.add_folder_permission(unrelated, SandboxPermission.READ_ONLY)
    agent.add_folder_permission(readable, SandboxPermission.WORKSPACE_WRITE)

    factory.configure(agent)

    assert agent.workspace == root.resolve()
    assert agent.sandbox is SandboxPermission.WORKSPACE_WRITE
    assert root / "readable" not in agent.folder_permissions
    assert root / "writable" not in agent.folder_permissions
    assert dict(agent.folder_permissions) == {
        unrelated.resolve(): SandboxPermission.READ_ONLY,
        readable.resolve(): SandboxPermission.READ_ONLY,
        writable.resolve(): SandboxPermission.WORKSPACE_WRITE,
    }


def test_configure_validates_links_before_changing_agent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    source = tmp_path / "project"
    source.mkdir()
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)
    factory.add_workspace(source, SandboxPermission.WORKSPACE_WRITE)
    agent = make_agent(tmp_path)
    original_workspace = agent.workspace
    link = root / source.name
    link.unlink() if link.is_symlink() else link.rmdir()

    with pytest.raises(ValueError, match="link.*no longer available"):
        factory.configure(agent)

    assert agent.workspace == original_workspace
    assert agent.sandbox is SandboxPermission.READ_ONLY
    assert dict(agent.folder_permissions) == {}


def test_configure_rejects_missing_target_before_changing_agent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    source = tmp_path / "project"
    source.mkdir()
    factory = WorkspaceFactory(root, SandboxPermission.WORKSPACE_WRITE)
    factory.add_workspace(source, SandboxPermission.WORKSPACE_WRITE)
    agent = make_agent(tmp_path)
    source.rmdir()

    with pytest.raises(ValueError, match="source.*no longer available"):
        factory.configure(agent)

    assert agent.workspace == tmp_path.resolve()
    assert agent.sandbox is SandboxPermission.READ_ONLY
