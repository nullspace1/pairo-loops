"""Standalone developer configuration tests."""

from unittest.mock import Mock, patch

from agents.isolated_developer.isolated_developer import get_isolated_developer


def test_isolated_developer_is_configured_without_coordination_tools() -> None:
    pipeline = Mock()
    repository = Mock()
    configured_agent = Mock()
    configuration = Mock()

    with (
        patch(
            "agents.isolated_developer.isolated_developer.TextualPermissionRequester"
        ) as permission_requester,
        patch(
            "agents.isolated_developer.isolated_developer.WorkspaceFactory"
        ) as workspace_factory,
        patch(
            "agents.isolated_developer.isolated_developer.AgentConfiguration",
            return_value=configuration,
        ) as configuration_type,
        patch(
            "agents.isolated_developer.isolated_developer.CodexAgent",
            return_value=configured_agent,
        ) as agent_type,
    ):
        result = get_isolated_developer(pipeline, repository)

    configuration_arguments = configuration_type.call_args.kwargs
    assert result is configured_agent
    assert configuration_arguments["name"] == "isolated_developer"
    assert "mcp_servers" not in configuration_arguments
    assert "complete authoritative assignment" in configuration_arguments[
        "base_instructions"
    ]
    assert "Do not create, inspect, assign" in configuration_arguments[
        "base_instructions"
    ]
    permission_requester.assert_called_once_with(pipeline, repository)
    assert workspace_factory.call_count == 1
    assert workspace_factory.return_value.add_workspace.call_count == 2
    workspace_factory.return_value.configure.assert_called_once_with(configuration)
