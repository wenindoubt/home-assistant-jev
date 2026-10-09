"""The private deployment artifact carries our client and both source notices."""

import json
from zipfile import ZipFile

from scripts.package_integration import COMPONENT, package


def test_archive_carries_the_bundled_client_and_both_notices(tmp_path):
    output = tmp_path / "jev.zip"
    package(output)
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "custom_components/jev/client/client.py" in names
        assert "custom_components/jev/client/py.typed" in names
        assert "AboveColin" in archive.read("custom_components/jev/LICENSE").decode()
        assert "wenindoubt" in archive.read("custom_components/jev/LICENSE").decode()
        client_notice = archive.read("custom_components/jev/client/LICENSE").decode()
        assert "AboveColin" in client_notice
        manifest = json.loads(archive.read("custom_components/jev/manifest.json"))
        assert manifest["requirements"] == []
        assert all(name.startswith("custom_components/jev/") for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(".env" in name or name.startswith("tests/") for name in names)
        expected = {
            "custom_components/jev/" + path.relative_to(COMPONENT).as_posix()
            for path in COMPONENT.rglob("*.py")
        }
        assert expected <= names
    assert output.with_suffix(".zip.sha256").is_file()


def test_archive_is_reproducible(tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    package(first)
    package(second)
    assert first.read_bytes() == second.read_bytes()
