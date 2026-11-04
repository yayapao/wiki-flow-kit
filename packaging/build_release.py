#!/usr/bin/env python3
"""Build the same portable kit ZIP locally and in GitHub Releases."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import zipfile

RUNTIME_FILES = ("VERSION", "README.md", "bin", "lib", "skills", "packaging")


def build_release(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    version = (source / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("VERSION must be a stable X.Y.Z version")
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / ("wiki-flow-kit-v" + version + ".zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in RUNTIME_FILES:
            entry = source / name
            if not entry.exists():
                raise ValueError("Missing runtime file: " + name)
            files = sorted(entry.rglob("*")) if entry.is_dir() else [entry]
            for file in files:
                if file.is_symlink():
                    raise ValueError("Symbolic links are not allowed in kit releases: " + str(file))
                if not file.is_file() or "__pycache__" in file.parts or file.suffix == ".pyc" or file.name == ".DS_Store":
                    continue
                archive.write(file, "wiki-flow/" + file.relative_to(source).as_posix())
    stable = output / "wiki-flow-kit.zip"
    shutil.copy2(archive_path, stable)
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text("".join(checksum + "  " + file.name + "\n" for file in (archive_path, stable)))
    return archive_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist")
    args = parser.parse_args()
    print(build_release(Path(__file__).resolve().parents[1], args.output))
