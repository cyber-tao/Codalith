from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
_SHELL = shutil.which("sh") if os.name != "nt" else None
_WINDOWS_POWERSHELL = (
    Path(os.environ.get("WINDIR", ""))
    / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
)


@pytest.fixture
def installer_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "源码 checkout with spaces"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "src" / "codalith" / "install").mkdir(parents=True)
    for entrypoint in ("install.sh", "install.ps1", "scripts/install_agents.py"):
        shutil.copyfile(_ROOT / entrypoint, checkout / entrypoint)
    (checkout / "src" / "codalith" / "__init__.py").write_text("", encoding="utf-8")
    (checkout / "src" / "codalith" / "install" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (checkout / "src" / "codalith" / "install" / "__main__.py").write_text(
        "import json, os, sys\n"
        "def main():\n"
        "    print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}, ensure_ascii=False))\n"
        "    return 37 if '--fail' in sys.argv else 0\n",
        encoding="utf-8",
    )
    return checkout


def _run_entrypoint(
    checkout: Path, entrypoint: str, arguments: list[str], *, fallback: bool = False,
    powershell: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["UV_PYTHON_DOWNLOADS"] = "never"
    environment.pop("PYTHONPATH", None)
    if fallback:
        environment["PATH"] = str(Path(sys.executable).parent)
        if os.name != "nt":
            environment["PATH"] += os.pathsep + "/usr/bin:/bin"
    if entrypoint == "install.ps1":
        assert powershell is not None or _POWERSHELL is not None
        command = [powershell or _POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(checkout / entrypoint)]
    elif entrypoint == "install.sh":
        assert _SHELL is not None
        command = [_SHELL, str(checkout / entrypoint)]
    else:
        command = [sys.executable, "-X", "utf8", str(checkout / entrypoint)]
    return subprocess.run(
        [*command, *arguments],
        cwd=checkout.parent,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    "entrypoint",
    [
        "scripts/install_agents.py",
        pytest.param("install.ps1", marks=pytest.mark.skipif(not _POWERSHELL, reason="PowerShell is unavailable")),
        pytest.param("install.sh", marks=pytest.mark.skipif(not _SHELL, reason="POSIX shell is unavailable")),
    ],
)
def test_install_entrypoints_preserve_arguments_and_find_their_checkout(
    installer_checkout: Path, entrypoint: str
) -> None:
    arguments = [
        "--agent", "codex", "--scope", "project", "--project", "项目 🌐 with spaces",
        "--endpoint", "http://127.0.0.1:8765/mcp?one=1&two=2", "--dry-run",
        "literal-$(never-execute)-`never-execute`",
    ]
    completed = _run_entrypoint(installer_checkout, entrypoint, arguments)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["args"] == arguments
    assert Path(payload["cwd"]) == installer_checkout.parent


@pytest.mark.parametrize(
    "entrypoint",
    [
        pytest.param("install.ps1", marks=pytest.mark.skipif(not _POWERSHELL, reason="PowerShell is unavailable")),
        pytest.param("install.sh", marks=pytest.mark.skipif(not _SHELL, reason="POSIX shell is unavailable")),
    ],
)
def test_install_entrypoints_fall_back_to_python_and_propagate_failures(
    installer_checkout: Path, entrypoint: str
) -> None:
    completed = _run_entrypoint(installer_checkout, entrypoint, ["--fail"], fallback=True)
    assert completed.returncode == 37, completed.stderr
    assert json.loads(completed.stdout)["args"] == ["--fail"]


@pytest.mark.skipif(
    os.name != "nt" or not _WINDOWS_POWERSHELL.is_file(),
    reason="Windows PowerShell 5.1 is unavailable",
)
@pytest.mark.parametrize("fallback", [False, True])
def test_windows_powershell_51_preserves_unicode_output_and_arguments(
    installer_checkout: Path, fallback: bool
) -> None:
    arguments = ["--project", "项目 🌐 with spaces"]
    completed = _run_entrypoint(
        installer_checkout, "install.ps1", arguments,
        fallback=fallback, powershell=str(_WINDOWS_POWERSHELL),
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["args"] == arguments
