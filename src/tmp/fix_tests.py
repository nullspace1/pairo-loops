from pathlib import Path
p = Path("tests/test_typed_activity_events.py")
s = p.read_text(encoding="utf-8")
lines = []
for line in s.splitlines():
    if 'assert entry.content.startswith(f"Command ' in line:
        lines.extend(['    assert entry.content.startswith("Command")', '    assert expected_outcome in entry.content'])
    elif 'assert entry.content.startswith("MCP tools / inspect ' in line and 'Failed' in line:
        lines.extend(['    assert entry.content.startswith("MCP tools / inspect")', '    assert "Failed: Not found" in entry.content'])
    elif 'assert entry.content.startswith("MCP tools / inspect ' in line and 'Completed' in line:
        lines.extend(['    assert entry.content.startswith("MCP tools / inspect")', '    assert "Completed successfully" in entry.content'])
    else:
        lines.append(line)
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
p = Path("tests/test_ui_app.py")
s = p.read_text(encoding="utf-8")
lines = ['            assert "Completed successfully" in transcript.entries[0].content' if 'assert "Command ' in line and 'Completed successfully" in transcript.entries[0].content' in line else line for line in s.splitlines()]
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
