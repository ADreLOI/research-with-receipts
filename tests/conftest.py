import os
import sys
import tempfile
from pathlib import Path

# Tests always run offline: mock sandbox, mock policy, throwaway data dir.
os.environ["SANDBOX_BACKEND"] = "mock"
os.environ["FORCE_MOCK_LLM"] = "1"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="rwr-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
