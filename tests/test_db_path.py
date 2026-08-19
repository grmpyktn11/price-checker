from pathlib import Path

import pytest

from backend.db import ROOT, resolve_url


# "sqlite:///./app.db" is relative to the working directory. launching uvicorn from anywhere
# but the repo root created a second, empty database, which looks exactly like every watched
# item, conversation and project having vanished
@pytest.mark.parametrize("url", ["sqlite:///./app.db", "sqlite:///app.db",
                                 "sqlite:///data/app.db"])
def test_a_relative_sqlite_path_is_anchored_to_the_repo(url):
    resolved = resolve_url(url)
    assert Path(resolved[len("sqlite:///"):]).is_absolute()
    assert str(ROOT) in resolved


# the same file whichever directory the server was started from
def test_the_default_resolves_to_one_file():
    assert resolve_url("sqlite:///./app.db") == resolve_url("sqlite:///app.db")


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:",
                                 "postgresql://host/db", "mysql://host/db"])
def test_non_file_urls_are_left_alone(url):
    assert resolve_url(url) == url


def test_an_absolute_path_is_left_alone():
    absolute = f"sqlite:///{Path('C:/tmp/other.db') if Path('C:/').exists() else Path('/tmp/other.db')}"
    assert resolve_url(absolute) == absolute
