import importlib
import logging

import pytest

from advisor import config
from advisor.logging_setup import setup_logging

SUBPACKAGES = ["data", "signals", "validation", "portfolio", "advisor", "journal", "alerts"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    assert importlib.import_module(f"advisor.{name}")


def test_config_paths_default_under_project():
    assert config.DATA_DIR.is_absolute()
    assert config.DB_PATH.parent == config.DATA_DIR


def test_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ADVISOR_X", str(tmp_path))
    assert config._path_from_env("ADVISOR_X", config.DATA_DIR) == tmp_path
    monkeypatch.setenv("ADVISOR_X", "rel/dir")
    assert config._path_from_env("ADVISOR_X", config.DATA_DIR) == config.PROJECT_ROOT / "rel" / "dir"
    monkeypatch.delenv("ADVISOR_X")
    assert config._path_from_env("ADVISOR_X", config.DATA_DIR) == config.DATA_DIR


def test_logging_setup_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    log = setup_logging("DEBUG")
    n = len(log.handlers)
    setup_logging("DEBUG")
    assert len(log.handlers) == n == 2
    log.info("hello")
    assert "hello" in (tmp_path / "advisor.log").read_text(encoding="utf-8")
    assert log.level == logging.DEBUG
    setup_logging("INFO", to_file=False)
    assert len(log.handlers) == 1
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
