from pathlib import Path

import pytest

from ai_dev_platform.application.init_service import initialize_project


@pytest.fixture
def initialized_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    initialize_project(root, "test-project")
    return root
