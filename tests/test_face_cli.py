"""Module 3, part 1: flask download-face-models (network is faked)."""
import hashlib
import io
import os

import pytest

from app.services import face_engine


@pytest.fixture
def fake_models(app, tmp_path, monkeypatch):
    """Point the command at a temp folder and fake two small 'model' files."""
    contents = {name: f"model {name}".encode() for name in face_engine.MODEL_FILES}
    files = {
        name: {"url": f"https://example.test/{name}", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for name, data in contents.items()
    }
    monkeypatch.setattr(face_engine, "MODEL_FILES", files)
    monkeypatch.setitem(app.config, "FACE_MODEL_DIR", str(tmp_path))
    return tmp_path, contents


def _serve(monkeypatch, contents, fail=False, corrupt=False):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        if fail:
            raise OSError("network unreachable")
        name = url.rsplit("/", 1)[1]
        data = contents[name]
        return io.BytesIO(data + b"tampered" if corrupt else data)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_downloads_and_verifies(cli, fake_models, monkeypatch):
    folder, contents = fake_models
    _serve(monkeypatch, contents)
    result = cli.invoke(args=["download-face-models"])
    assert result.exit_code == 0, result.output
    for name, data in contents.items():
        assert (folder / name).read_bytes() == data
    assert "ready" in result.output


def test_second_run_skips_verified_files(cli, fake_models, monkeypatch):
    _, contents = fake_models
    _serve(monkeypatch, contents)
    cli.invoke(args=["download-face-models"])
    calls = _serve(monkeypatch, contents)
    result = cli.invoke(args=["download-face-models"])
    assert calls == [] and "already present" in result.output


def test_checksum_mismatch_discards_the_file(cli, fake_models, monkeypatch):
    folder, contents = fake_models
    _serve(monkeypatch, contents, corrupt=True)
    result = cli.invoke(args=["download-face-models"])
    assert result.exit_code != 0
    assert "official checksum" in result.output
    assert os.listdir(folder) == []  # no half-file or bad file left behind


def test_network_failure_gives_clear_message(cli, fake_models, monkeypatch):
    folder, contents = fake_models
    _serve(monkeypatch, contents, fail=True)
    result = cli.invoke(args=["download-face-models"])
    assert result.exit_code != 0
    assert "internet connection" in result.output
    assert os.listdir(folder) == []