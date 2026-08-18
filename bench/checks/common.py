"""Shared bits for the gate scripts (criterion output reading)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

# criterion writes below the crate's target dir; override with CRITERION_DIR.
CRITERION_DIR = Path(
    os.environ.get("CRITERION_DIR", "crates/_channels_shm_native/target/criterion")
)


def read_criterion_mean(path: Path) -> float:
    """Mean estimate (seconds) from a criterion estimates.json file.

    criterion 0.8 renamed the JSON field: `point_estimate` supersedes `point`;
    accept both so the scripts work across schema versions.
    """
    data = cast(
        "dict[str, dict[str, float]]",
        json.loads(path.read_text()),
    )
    mean = data["mean"]
    return (mean["point_estimate"] if "point_estimate" in mean else mean["point"]) / 1e9
