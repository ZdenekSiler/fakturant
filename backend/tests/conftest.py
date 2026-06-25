import sys
from pathlib import Path

# Make backend root importable so "from main import app" and "from services.db import ..." work
sys.path.insert(0, str(Path(__file__).parent.parent))
