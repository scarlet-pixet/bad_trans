"""Live tests require the project and endpoints selected by the isolated runner."""
import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get("BAD_TRANS_PROJECT", "").startswith("bad_trans_test_"):
        return
    skip = pytest.mark.skip(reason="Run the live suite through run.ps1 -Action Test")
    for item in items:
        if item.path.parent == Path(__file__).parent:
            item.add_marker(skip)
