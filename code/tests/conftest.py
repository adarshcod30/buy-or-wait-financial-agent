import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.config import PATHS  # noqa: E402
from buyorwait.data import load_dataset  # noqa: E402


@pytest.fixture(scope="session")
def ds():
    if not (PATHS.dataset / "requests.csv").is_file():
        pytest.skip("dataset not present")
    return load_dataset()
