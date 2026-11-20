import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='meal-test-'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_TMP / "test.db"}')
os.environ.setdefault('ARTIFACT_DIR', str(_TMP / 'artifacts'))
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/15')
os.environ.setdefault('SEED_DEMO', 'false')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
