from pathlib import Path
p=Path('tests/test_ui_app.py')
s=p.read_text(encoding='utf-8')
s=s.replace('    AgentCommandExecutionCompleted,\n', '    AgentCommandExecutionCompleted,\n    AgentMcpToolCallCompleted,\n')
needle='            assert "Output:\\ndone" in transcript.entries[0].content\n'
replacement=needle+'            pipeline.push(AgentMcpToolCallCompleted(\n                agent_id=agent_id, session_name=active.name, activity_id="call",\n                server="tools", tool="inspect", arguments={}, status="completed",\n            ))\n            for _ in range(3):\n                await pilot.pause()\n            assert len(transcript.entries) == 2\n            assert transcript.entries[1].metadata["type"] == "mcp_tool_call"\n'
s=s.replace(needle,replacement)
p.write_text(s,encoding='utf-8')
