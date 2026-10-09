"""Build a credential-free manual-install archive; never deploy to Home Assistant."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "jev"
SUFFIXES = {".py", ".json", ".yaml", ".png", ".typed"}


def package(output: Path) -> None:
    """Include source/assets/licence only, with reproducible archive metadata."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(COMPONENT.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise ValueError(f"Refusing symlink: {path}")
            if not path.is_file() or (
                path.suffix not in SUFFIXES and path.name != "LICENSE"
            ):
                continue
            info = ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
        # Carry both source notices in the deployable artifact.
        info = ZipInfo("custom_components/jev/LICENSE", (2026, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (ROOT / "LICENSE").read_bytes())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n"
    )
    print(f"{output}\nSHA256: {checksum}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "jev.zip")
    package(parser.parse_args().output)
