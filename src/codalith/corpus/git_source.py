"""Read immutable Git objects without checking out or fetching source trees."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_VERSION = re.compile(r"(?:^|[^0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?)(?:[^0-9]|$)")
_MAX_BUILD_VERSION_BYTES = 65_536


class GitSourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GitFile:
    path: str
    blob_oid: str
    size: int
    mode: str


@dataclass(frozen=True, slots=True)
class GitTag:
    tag: str
    tag_oid: str
    commit: str
    annotated: bool
    engine_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=True)
        if not self.path.is_dir():
            raise GitSourceError("Repository path must be a directory")
        try:
            capability = subprocess.run(
                ["git", "--no-lazy-fetch", "--no-replace-objects", "--version"],
                stdin=subprocess.DEVNULL, capture_output=True,
                env=self._environment(), timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitSourceError("Git 2.45 or newer with --no-lazy-fetch is required") from exc
        if capability.returncode:
            raise GitSourceError("Git 2.45 or newer with --no-lazy-fetch is required")
        self.git_dir = Path(self._run("rev-parse", "--absolute-git-dir").decode().strip()).resolve()
        self.common_dir = Path(
            self._run("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
        ).resolve()

    def _run(self, *args: str, timeout: float = 60) -> bytes:
        try:
            result = subprocess.run(
                self._command(*args),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=self._environment(),
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitSourceError(f"Git object operation failed: {type(exc).__name__}") from exc
        if result.returncode:
            raise GitSourceError("Git object is missing, invalid, or unavailable locally")
        return result.stdout

    def _command(self, *args: str) -> list[str]:
        return [
            "git",
            "--no-replace-objects",
            "--no-lazy-fetch",
            "-c",
            "safe.directory=" + str(self.path),
            "-c",
            "protocol.allow=never",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-C",
            str(self.path),
            *args,
        ]

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = dict(os.environ)
        for name in list(environment):
            if name.startswith("GIT_"):
                del environment[name]
        environment.update(
            {
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_PROTOCOL_FROM_USER": "0",
                "LC_ALL": "C",
            }
        )
        return environment

    def tags(self) -> list[GitTag]:
        records = self._run(
            "for-each-ref",
            "--format=%(refname:strip=2)%00%(objectname)%00"
            "%(objecttype)%00%(*objectname)%00%(*objecttype)",
            "refs/tags/",
        )
        result: list[GitTag] = []
        for record in records.decode("utf-8").splitlines():
            tag, oid, kind, peeled, peeled_kind = record.split("\0")
            if kind != "commit" and peeled_kind != "commit":
                continue
            match = _VERSION.search(tag)
            result.append(
                GitTag(tag, oid, peeled or oid, kind == "tag", match.group(1) if match else None)
            )
        return result

    def resolve_tag(self, tag: str) -> GitTag:
        if not tag or any(ord(character) < 32 for character in tag):
            raise GitSourceError("Invalid tag name")
        ref = "refs/tags/" + tag
        self._run("check-ref-format", ref)
        tag_oid = self._run("rev-parse", "--verify", "--end-of-options", ref).decode().strip()
        commit = (
            self._run("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
            .decode()
            .strip()
        )
        self._validate_oid(tag_oid)
        self._validate_oid(commit)
        kind = self._run("cat-file", "-t", tag_oid).decode().strip()
        version = self.engine_version(commit)
        if version is None:
            match = _VERSION.search(tag)
            version = match.group(1) if match else None
        return GitTag(tag, tag_oid, commit, kind == "tag", version)

    def files(self, commit: str) -> list[GitFile]:
        self._validate_oid(commit)
        result: list[GitFile] = []
        for record in self._run("ls-tree", "-r", "-z", "-l", commit).split(b"\0"):
            if not record:
                continue
            header, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                continue
            path = raw_path.decode("utf-8", errors="strict")
            if (
                path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                raise GitSourceError("Git tree contains an unsafe source path")
            if not size.isdigit():
                raise GitSourceError(f"Git source blob is missing or unavailable locally: {path}")
            result.append(GitFile(path, oid.decode("ascii"), int(size), mode.decode("ascii")))
        return result

    def read_blob(self, oid: str) -> bytes:
        self._validate_oid(oid)
        data = self._run("cat-file", "blob", oid)
        self._verify_blob(oid, data)
        return data

    def read_blobs(self, oids: Iterable[str]) -> Iterator[tuple[str, bytes]]:
        process = subprocess.Popen(
            self._command("cat-file", "--batch"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._environment(),
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            for oid in oids:
                self._validate_oid(oid)
                process.stdin.write((oid + "\n").encode("ascii"))
                process.stdin.flush()
                header = process.stdout.readline(256).strip().split()
                if len(header) != 3 or header[1] != b"blob" or header[0].decode("ascii") != oid:
                    raise GitSourceError("Git blob is missing or unavailable locally")
                size = int(header[2])
                data = process.stdout.read(size)
                if len(data) != size or process.stdout.read(1) != b"\n":
                    raise GitSourceError("Truncated Git blob response")
                self._verify_blob(oid, data)
                yield oid, data
        finally:
            process.stdin.close()
            process.stdout.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)

    def engine_version(self, commit: str) -> str | None:
        self._validate_oid(commit)
        try:
            oid = (
                self._run(
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    commit + ":Engine/Build/Build.version",
                )
                .decode()
                .strip()
            )
            self._validate_oid(oid)
            size = int(self._run("cat-file", "-s", oid).decode().strip())
            if size > _MAX_BUILD_VERSION_BYTES:
                return None
            raw = self.read_blob(oid)
            data = json.loads(raw)
            parts = [data[name] for name in ("MajorVersion", "MinorVersion", "PatchVersion")]
            if all(
                isinstance(part, int) and not isinstance(part, bool) and part >= 0 for part in parts
            ):
                return ".".join(str(part) for part in parts)
        except (GitSourceError, ValueError, KeyError, TypeError):
            return None
        return None

    @staticmethod
    def _verify_blob(oid: str, data: bytes) -> None:
        hasher = hashlib.sha256() if len(oid) == 64 else hashlib.sha1()
        hasher.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
        hasher.update(data)
        if hasher.hexdigest() != oid:
            raise GitSourceError("Git source blob integrity mismatch")

    @staticmethod
    def _validate_oid(oid: str) -> None:
        if not _OID.fullmatch(oid):
            raise GitSourceError("Expected a complete Git object ID")
