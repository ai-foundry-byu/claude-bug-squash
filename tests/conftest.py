import sys
from pathlib import Path

# make src/ importable without an install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from bugsquash import config
from bugsquash.diff import Changeset, FileChange


@pytest.fixture
def posture(tmp_path):
    """Default-safe posture (shadow on, not armed) rooted at a tmp state dir."""
    return config.Posture(enabled=True, shadow=True, merge_armed=False,
                          max_merges_per_day=1, state_dir=tmp_path / "state")


@pytest.fixture
def live_posture(tmp_path):
    """LIVE posture: enabled, shadow off, armed, under the rate limit."""
    return config.Posture(enabled=True, shadow=False, merge_armed=True,
                          max_merges_per_day=5, state_dir=tmp_path / "state")


@pytest.fixture
def cs():
    """Factory for a Changeset from (path, added, deleted, status) tuples."""
    def _make(*files, repo="repo"):
        fcs = []
        for f in files:
            path = f[0]
            added = f[1] if len(f) > 1 else 1
            deleted = f[2] if len(f) > 2 else 0
            status = f[3] if len(f) > 3 else "M"
            fcs.append(FileChange(path=path, status=status, added=added, deleted=deleted))
        return Changeset(repo=repo, files=tuple(fcs))
    return _make
