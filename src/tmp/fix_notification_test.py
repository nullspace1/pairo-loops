from pathlib import Path
p=Path('tests/test_codex_notifications.py')
s=p.read_text(encoding='utf-8').replace('        await pipeline.wait_until_idle()\n', '        while len(observed) < 4:\n            await asyncio.sleep(0)\n')
p.write_text(s,encoding='utf-8')
