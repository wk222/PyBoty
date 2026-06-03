from __future__ import annotations

import core
from core.systems.runtime.version import get_pybot_version


def test_core_version_matches_package_version():
    assert core.__version__ == get_pybot_version()
