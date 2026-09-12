"""Paths and tunable constants. Everything here is overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(os.environ.get("BOW_REPO_ROOT", _CODE_DIR.parent))


@dataclass(frozen=True)
class Paths:
    repo_root: Path = REPO_ROOT
    dataset: Path = REPO_ROOT / "dataset"
    images: Path = REPO_ROOT / "dataset" / "media" / "images"
    output: Path = REPO_ROOT / "output.csv"
    runs: Path = REPO_ROOT / "runs"
    cache: Path = REPO_ROOT / ".cache"


PATHS = Paths()

# Forecast horizon mandated by the problem statement ("90-Day Safety Check").
FORECAST_DAYS = int(os.environ.get("BOW_FORECAST_DAYS", "90"))

# Money is compared at two decimals throughout.
MONEY_EPS = 0.005
