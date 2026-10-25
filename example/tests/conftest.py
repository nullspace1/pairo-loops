from pathlib import Path
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_ROOT))
