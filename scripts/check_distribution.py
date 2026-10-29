"""Build an sdist, rebuild its wheel, and verify installed component resources."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{result.stderr}")
    return result.stdout


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to check distribution artifacts")
    resources = {
        "LICENSE": root / "src/codalith/semantic/LICENSE",
        "NOTICE": root / "src/codalith/semantic/NOTICE",
        "SKILL.md": root / "skills/codalith-source/SKILL.md",
    }
    expected = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in resources.items()}
    with tempfile.TemporaryDirectory(prefix="codalith-distribution-") as directory:
        temporary = Path(directory)
        run([uv, "build", "--sdist", "--no-build-logs", "--out-dir", str(temporary)], cwd=root)
        sdist, = temporary.glob("*.tar.gz")
        with tarfile.open(sdist) as archive:
            for name in resources:
                matches = [item for item in archive.getmembers() if item.name.endswith("/" + name)]
                if len(matches) != 1:
                    raise RuntimeError(f"Source distribution must include exactly one {name}")
                stream = archive.extractfile(matches[0])
                if stream is None or hashlib.sha256(stream.read()).hexdigest() != expected[name]:
                    raise RuntimeError(f"Source distribution changed {name}")
        run([uv, "build", str(sdist), "--wheel", "--no-build-logs", "--out-dir", str(temporary)], cwd=temporary)
        wheel, = temporary.glob("*.whl")
        with zipfile.ZipFile(wheel) as archive:
            for name in resources:
                matches = [item for item in archive.namelist() if item.endswith("/" + name)]
                if len(matches) != 1 or hashlib.sha256(archive.read(matches[0])).hexdigest() != expected[name]:
                    raise RuntimeError(f"Wheel is missing or changed {name}")
        environment = temporary / "consumer"
        run([uv, "venv", "--python", sys.executable, str(environment)], cwd=temporary)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], cwd=temporary)
        probe = (
            "import hashlib,json; from importlib.resources import files; "
            "from codalith.install.core import skill_bytes; "
            "data={name:files('codalith.semantic').joinpath(name).read_bytes() for name in ('LICENSE','NOTICE')}; "
            "data['SKILL.md']=skill_bytes(); "
            "print(json.dumps({name:hashlib.sha256(value).hexdigest() for name,value in data.items()}))"
        )
        actual = json.loads(run([str(python), "-I", "-c", probe], cwd=temporary))
        if actual != expected:
            raise RuntimeError("Installed resource bytes differ from the source checkout")
        print(f"Verified {sdist.name} -> {wheel.name} -> isolated install: LICENSE, NOTICE, SKILL.md")


if __name__ == "__main__":
    main()
