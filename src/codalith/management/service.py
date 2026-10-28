"""Local database catalog and immutable, version-pinned import plans."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import IO, Any

from codalith.corpus.generations import IndexManifest
from codalith.corpus.git_source import GitRepository
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.errors import CodalithError
from codalith.indexing.builder import build_lock
from codalith.languages import create_adapter
from codalith.languages.catalog import ADAPTERS
from codalith.management.catalog import Catalog, timestamp
from codalith.management.config import ManagementConfig

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "partial"}
_ACTIVE = {"queued", "running", "validating", "publishing", "cancel_requested"}
logger = logging.getLogger(__name__)
_HEARTBEAT_SECONDS = 2.0
_CANCEL_GRACE_SECONDS = 10.0


def _identifier(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


class ManagementService:
    def __init__(
        self, registry: CorpusRegistry, policy: SourcePolicy, config: ManagementConfig
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.config = config
        if config.enabled and (len(config.admin_token) < 16 or not config.source_roots):
            raise ValueError(
                "Enabled management requires allowed roots and a strong administrator token"
            )
        if any(not root.is_dir() for root in config.source_roots):
            raise ValueError("All configured source roots must be existing directories")
        self.profiles = dict(registry.corpora)
        for item in config.profiles:
            identifier = str(item.get("id", ""))
            adapter = str(item.get("adapter", ""))
            provider = str(item.get("embedding_provider", ""))
            fields = {
                "id",
                "label",
                "adapter",
                "embedding_provider",
                "embedding_model",
                "include_extensions",
                "exclude_globs",
            }
            if (
                not identifier
                or len(identifier) > 64
                or identifier in self.profiles
                or set(item) - fields
                or adapter not in ADAPTERS
                or provider not in {"fake", "openai", "fastembed"}
            ):
                raise ValueError("Invalid or duplicate management profile")
            extensions = item.get("include_extensions", [])
            excludes = item.get("exclude_globs", [".git/**"])
            if (
                not isinstance(extensions, list)
                or not extensions
                or not isinstance(excludes, list)
                or any(not isinstance(value, str) for value in [*extensions, *excludes])
            ):
                raise ValueError(
                    "Management profile requires include_extensions and valid exclude_globs"
                )
            self.profiles[identifier] = Corpus(
                identifier,
                str(item.get("label", identifier)),
                "Management import profile",
                "unbound",
                config.storage_root,
                config.storage_root,
                None,
                adapter,
                provider,
                str(item.get("embedding_model", "")),
                tuple(extensions),
                tuple(excludes),
            )
        self.catalog = Catalog(config.storage_root)
        self.on_publish: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._worker_lease: AbstractContextManager[None] | None = None
        self.worker_error: str | None = None

    def public_config(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "storage_root": str(self.config.storage_root),
            "source_roots": self.source_roots(),
            "profiles": [
                {
                    "profile_id": item.corpus_id,
                    "label": item.display_name,
                    "adapter": item.adapter,
                    "embedding_provider": item.embedding_provider,
                    "embedding_model": item.embedding_model,
                }
                for item in self.profiles.values()
            ],
            "max_selected_tags": self.config.max_selected_tags,
            "worker_error": self.worker_error,
            "legacy_generations": self.legacy_generations(),
        }

    def safe_error(self, error: BaseException) -> str:
        message = f"{type(error).__name__}: {error}"
        secrets = [self.config.admin_token]
        secrets.extend(
            value
            for name, value in os.environ.items()
            if any(key in name.upper() for key in ("TOKEN", "PASSWORD", "SECRET", "API_KEY"))
        )
        for value in secrets:
            if value and len(value) >= 6:
                message = message.replace(value, "[redacted]")
        return message[:4000]

    def space(self, database_id: str | None = None) -> dict[str, Any]:
        from codalith.indexing.shared import SharedPool

        if database_id is not None:
            self.catalog.get("database", database_id)
        pool = SharedPool(self.config.storage_root / "shared", self.policy)
        values = pool.storage_stats()
        structure_bytes = mapping_bytes = None
        sqlite_index_bytes = 0
        try:
            with sqlite3.connect(f"{pool.catalog.as_uri()}?mode=ro", uri=True) as connection:
                sizes = connection.execute(
                    "SELECT d.name,m.type,sum(d.pgsize) FROM dbstat d "
                    "LEFT JOIN sqlite_master m ON m.name=d.name GROUP BY d.name,m.type"
                ).fetchall()
            structure_bytes = mapping_bytes = 0
            for name, kind, size in sizes:
                if kind == "index" or "symbol_search" in name:
                    sqlite_index_bytes += size
                elif name.startswith("facts_") or name.startswith("extraction_"):
                    structure_bytes += size
                else:
                    mapping_bytes += size
        except sqlite3.OperationalError:
            pass
        return {
            **values,
            "logical_source_bytes": values["source_all_versions_logical_bytes"],
            "physical_source_bytes": values["source_physical_bytes"],
            "vector_count": values["unique_vectors"],
            "shared_bytes": values["physical_bytes"],
            "breakdown": {
                "source_bytes": values["source_physical_bytes"],
                "structure_bytes": structure_bytes,
                "vector_bytes": max(0, values["semantic_physical_bytes"] - values["index_physical_bytes"]),
                "index_bytes": values["index_physical_bytes"] + sqlite_index_bytes,
                "mapping_bytes": mapping_bytes,
                "total_bytes": values["physical_bytes"],
            },
            "notes": [
                "Physical bytes describe the shared pool across all databases; "
                "logical source bytes sum all published versions",
                "SQLite categories use database page allocation; WAL and free-page overhead are included only in the physical total. Search indexes include retained historical indexes.",
            ],
        }

    def source_roots(self) -> list[dict[str, str]]:
        return [
            {"root_id": str(index), "path": str(path), "label": path.name or str(path)}
            for index, path in enumerate(self.config.source_roots)
        ]

    def source_path(self, raw: str, root_id: str | None = None) -> Path:
        if root_id is not None:
            try:
                root = self.config.source_roots[int(root_id)]
                if int(root_id) < 0:
                    raise IndexError
            except (ValueError, IndexError) as exc:
                raise ValueError("Unknown source root") from exc
            candidate = Path(raw)
            lexical = Path(
                os.path.abspath(candidate if candidate.is_absolute() else root / candidate)
            )
            if not lexical.is_relative_to(Path(os.path.abspath(root))):
                raise ValueError("Source path is outside the allowed root")
            path = lexical.resolve(strict=True)
            if not path.is_relative_to(root.resolve(strict=True)):
                raise ValueError("Source path is outside the allowed root")
        else:
            candidate = Path(raw)
            if not candidate.is_absolute():
                raise ValueError("A relative path requires root_id")
            lexical = Path(os.path.abspath(candidate))
            if not any(
                lexical.is_relative_to(Path(os.path.abspath(root)))
                for root in self.config.source_roots
            ):
                raise ValueError("Source path is outside the configured source roots")
            path = lexical.resolve(strict=True)
        if not path.is_dir() or not any(
            path.is_relative_to(root.resolve(strict=True)) for root in self.config.source_roots
        ):
            raise ValueError("Source path is outside the configured source roots")
        return path

    def directories(self, root_id: str, path: str = "") -> dict[str, Any]:
        directory = self.source_path(path, root_id)
        root = self.config.source_roots[int(root_id)].resolve(strict=True)
        items = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                safe = self.source_path(str(child), root_id)
            except (ValueError, OSError):
                continue
            items.append(
                {
                    "name": child.name,
                    "path": safe.relative_to(root).as_posix(),
                    "is_repository": (safe / ".git").exists()
                    or ((safe / "HEAD").is_file() and (safe / "objects").is_dir()),
                }
            )
        return {"root_id": root_id, "path": directory.relative_to(root).as_posix(), "items": items}

    def add_repository(self, raw: str, root_id: str | None = None) -> dict[str, Any]:
        path = self.source_path(raw, root_id)
        self._validate_repository(GitRepository(path))
        repo_id = "repo-" + hashlib.sha256(str(path).encode()).hexdigest()[:20]
        return self.catalog.put(
            "repository",
            repo_id,
            {
                "repo_id": repo_id,
                "path": str(path),
                "name": path.name,
                "created_at": timestamp(),
            },
        )

    def repository(self, repo_id: str) -> GitRepository:
        item = self.catalog.get("repository", repo_id)
        repository = GitRepository(self.source_path(item["path"]))
        self._validate_repository(repository)
        return repository

    def _validate_repository(self, repository: GitRepository) -> None:
        for directory in (repository.git_dir, repository.common_dir):
            self.source_path(str(directory))
        alternates = repository.common_dir / "objects" / "info" / "alternates"
        if alternates.is_file() and alternates.read_bytes().strip():
            raise ValueError("Repository alternates are not allowed for managed imports")

    def legacy_generations(self) -> list[dict[str, Any]]:
        items = []
        for corpus in self.registry.corpora.values():
            root = corpus.index_root / "generations"
            if not root.is_dir():
                continue
            for directory in sorted(root.iterdir()):
                if (
                    not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", directory.name)
                    or directory.is_symlink()
                    or not directory.is_dir()
                ):
                    continue
                if not directory.resolve().is_relative_to(root.resolve()):
                    continue
                try:
                    manifest = IndexManifest.load(directory / "manifest.json")
                except CodalithError:
                    continue
                if (
                    manifest.corpus_id != corpus.corpus_id
                    or manifest.generation_id != directory.name
                    or not manifest.semantic_available
                ):
                    continue
                items.append(
                    {
                        "generation_id": manifest.generation_id,
                        "corpus_id": corpus.corpus_id,
                        "path": str(directory.resolve()),
                        "revision": manifest.source_revision,
                        "semantic_engine_version": manifest.semantic_engine_version,
                        "embedding_model": manifest.embedding_model,
                        "embedding_dimension": manifest.embedding_dimension,
                    }
                )
        return items

    def tags(
        self, repo_id: str, *, q: str = "", cursor: str = "0", limit: int = 100
    ) -> dict[str, Any]:
        offset = int(cursor or "0")
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid tag pagination")
        items = [
            tag.to_dict()
            for tag in self.repository(repo_id).tags()
            if q.casefold() in tag.tag.casefold()
        ]
        end = offset + limit
        return {
            "items": items[offset:end],
            "next_cursor": str(end) if end < len(items) else None,
            "total": len(items),
        }

    def create_database(
        self, name: str, profile_id: str, repo_id: str | None = None
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 128:
            raise ValueError("Database name must contain 1 to 128 characters")
        if profile_id not in self.profiles:
            raise ValueError("Unknown import profile")
        if repo_id is not None:
            self.repository(repo_id)
        database_id = _identifier("db")
        return self.catalog.put(
            "database",
            database_id,
            {
                "database_id": database_id,
                "name": name.strip(),
                "profile_id": profile_id,
                "repo_id": repo_id,
                "created_at": timestamp(),
            },
        )

    def create_plan(
        self,
        database_id: str,
        tags: list[str],
        repo_id: str | None = None,
        legacy_generation: str | None = None,
    ) -> dict[str, Any]:
        database = self.catalog.get("database", database_id)
        repo_id = repo_id or database.get("repo_id")
        if not repo_id:
            raise ValueError("Database or plan must select a repository")
        if not tags or len(tags) > self.config.max_selected_tags or len(tags) != len(set(tags)):
            raise ValueError("Select a nonempty, unique tag list within max_selected_tags")
        repository = self.repository(repo_id)
        profile = self.profiles[database["profile_id"]]
        items = []
        unique_blobs: dict[str, int] = {}
        logical_bytes = 0
        file_occurrences = 0
        adapter = create_adapter(profile.adapter)
        for name in tags:
            tag = repository.resolve_tag(name)
            target_id = database_id + "-" + tag.commit[:12]
            files = [
                item
                for item in repository.files(tag.commit)
                if (
                    not profile.include_extensions
                    or any(item.path.lower().endswith(ext) for ext in profile.include_extensions)
                )
                and not profile.excludes(item.path)
                and not self.policy.is_denied(item.path)
                and item.size <= self.policy.max_file_bytes
                and adapter.supports(Path(item.path))
            ]
            if not files:
                raise ValueError(f"Tag {name} contains no allowed source files for this profile")
            size = sum(item.size for item in files)
            for source_file in files:
                unique_blobs[source_file.blob_oid] = source_file.size
            logical_bytes += size
            file_occurrences += len(files)
            items.append(
                {
                    **tag.to_dict(),
                    "target_id": target_id,
                    "source_file_count": len(files),
                    "logical_source_bytes": size,
                }
            )
        profile_data = {
            "profile_id": profile.corpus_id,
            "adapter": profile.adapter,
            "adapter_version": ADAPTERS[profile.adapter].version,
            "embedding_provider": profile.embedding_provider,
            "embedding_model": profile.embedding_model,
            "include_extensions": list(profile.include_extensions),
            "exclude_globs": list(profile.exclude_globs),
        }
        legacy_path = None
        if legacy_generation is not None:
            legacy = next(
                (
                    item
                    for item in self.legacy_generations()
                    if item["generation_id"] == legacy_generation
                ),
                None,
            )
            if legacy is None:
                raise ValueError("Legacy generation must be selected from the registered list")
            legacy_path = legacy["path"]
        policy_data = {
            "default_max_lines": self.policy.default_max_lines,
            "hard_max_lines": self.policy.hard_max_lines,
            "max_file_bytes": self.policy.max_file_bytes,
            "deny_globs": list(self.policy.deny_globs),
        }
        contract = hashlib.sha256(
            json.dumps({"profile": profile_data, "policy": policy_data}, sort_keys=True).encode()
        ).hexdigest()[:8]
        for item in items:
            item["target_id"] += "-" + contract
        identity = {
            "database_id": database_id,
            "repo_id": repo_id,
            "items": items,
            "profile": profile_data,
            "policy": policy_data,
            "legacy_generation": legacy_generation,
            "legacy_generation_path": legacy_path,
            "summary": {
                "version_count": len(items),
                "file_occurrences": file_occurrences,
                "logical_source_bytes": logical_bytes,
                "unique_source_bytes": sum(unique_blobs.values()),
                "shared_file_occurrences": file_occurrences - len(unique_blobs),
                "embedding_estimate": None,
                "notes": [
                    "Source totals are exact for this plan; embedding calls depend on chunk and vector cache matches"
                ],
            },
        }
        plan_id = (
            "plan-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        )
        try:
            return self.catalog.get("plan", plan_id)
        except KeyError:
            return self.catalog.put(
                "plan", plan_id, {**identity, "plan_id": plan_id, "created_at": timestamp()}
            )

    def create_job(self, plan_id: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        plan = self.catalog.get("plan", plan_id)
        now = timestamp()
        job = {
            "job_id": _identifier("job"),
            "database_id": plan["database_id"],
            "repo_id": plan["repo_id"],
            "plan_id": plan_id,
            "status": "queued",
            "phase": "queued",
            "completed_units": 0,
            "total_units": len(plan["items"]),
            "unit": "tags",
            "completed_tags": 0,
            "total_tags": len(plan["items"]),
            "created_at": now,
            "updated_at": now,
            "error": None,
            "attempt": 1,
            "items": [
                {
                    **item,
                    "status": "queued",
                    "phase": "queued",
                    "snapshot_id": None,
                    "completed_units": 0,
                    "total_units": None,
                    "unit": "files",
                    "error": None,
                }
                for item in plan["items"]
            ],
        }
        return self.catalog.create_job(job, idempotency_key)

    def cancel(self, job_id: str) -> dict[str, Any]:
        def update(job: dict[str, Any]) -> None:
            if job["status"] not in _TERMINAL:
                job["status"] = "cancelled" if job["status"] == "queued" else "cancel_requested"
                job["phase"] = job["status"]
                if job["status"] == "cancelled":
                    for item in job["items"]:
                        if item["status"] == "queued":
                            item.update(status="cancelled", phase="cancelled")

        return self.catalog.update_job(job_id, update, event="cancel_requested")

    def resume(self, job_id: str) -> dict[str, Any]:
        def update(job: dict[str, Any]) -> None:
            if job["status"] not in {"interrupted", "failed", "cancelled", "partial"}:
                raise ValueError("Only interrupted or unsuccessful jobs can be resumed")
            job.update(status="queued", phase="queued", error=None, attempt=job["attempt"] + 1)
            for item in job["items"]:
                if item["status"] != "succeeded":
                    item.update(
                        status="queued",
                        phase="queued",
                        error=None,
                        completed_units=0,
                        total_units=None,
                    )

        return self.catalog.update_job(job_id, update, event="resumed")

    def recover(self) -> None:
        from codalith.indexing.shared import SharedPool

        snapshots = SharedPool(self.config.storage_root / "shared", self.policy).snapshots()
        published = {(item["target_id"], item["commit"]): item for item in snapshots}
        for job in self.catalog.list("job"):
            if job["status"] in _ACTIVE:

                def update(value: dict[str, Any]) -> None:
                    value.update(status="interrupted", phase="interrupted")
                    for item in value["items"]:
                        snapshot = published.get((item["target_id"], item["commit"]))
                        if snapshot is not None:
                            item.update(
                                status="succeeded",
                                phase="published",
                                snapshot_id=snapshot["snapshot_id"],
                                generation_id=snapshot["generation_id"],
                                counts=snapshot["counts"],
                                statistics=snapshot.get("statistics", {}),
                                reused_vectors=snapshot["reused_vectors"],
                                new_vectors=snapshot["new_vectors"],
                                completed_units=1,
                                total_units=1,
                                unit="snapshots",
                            )
                        elif item["status"] in _ACTIVE:
                            item.update(status="interrupted", phase="interrupted")
                    completed = sum(item["status"] == "succeeded" for item in value["items"])
                    value.update(completed_tags=completed, completed_units=completed)
                    if completed == len(value["items"]):
                        value.update(status="succeeded", phase="succeeded")

                self.catalog.update_job(job["job_id"], update, event="interrupted")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        lease = build_lock(self.config.storage_root / "worker")
        lease.__enter__()
        self._worker_lease = lease
        try:
            self.recover()
        except BaseException:
            lease.__exit__(*sys.exc_info())
            self._worker_lease = None
            raise
        self._thread = threading.Thread(target=self._loop, name="codalith-management", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if process is not None and process.poll() is None:
                _terminate_worker(process)
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Management worker did not stop; writer lock retained")
            self._thread = None
        if self._worker_lease is not None:
            self._worker_lease.__exit__(None, None, None)
            self._worker_lease = None

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._run_pending_once():
                    self._stop.wait(0.5)
        except Exception as exc:
            self.worker_error = f"Scheduler stopped: {type(exc).__name__}"
            logger.exception("Management scheduler stopped")
            self.recover()

    def run_pending_once(self) -> bool:
        with build_lock(self.config.storage_root / "worker"):
            return self._run_pending_once()

    def _run_pending_once(self) -> bool:
        jobs = sorted(self.catalog.list("job"), key=lambda item: item["created_at"])
        for job in jobs:
            if job["status"] != "queued":
                continue
            if not self.catalog.claim_job(job["job_id"], job["attempt"]):
                continue
            environment = {
                **os.environ,
                "CODALITH_JOB_POLICY": json.dumps(
                    {
                        "default_max_lines": self.policy.default_max_lines,
                        "hard_max_lines": self.policy.hard_max_lines,
                        "max_file_bytes": self.policy.max_file_bytes,
                        "deny_globs": list(self.policy.deny_globs),
                    }
                ),
            }
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "codalith.management.worker",
                        str(self.registry.path),
                        job["job_id"],
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=environment,
                    creationflags=(
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    ),
                    start_new_session=os.name != "nt",
                )
            except OSError as exc:
                message = self.safe_error(exc)

                def launch_failed(value: dict[str, Any], error: str = message) -> None:
                    value.update(status="failed", phase="failed", error=error)

                self.catalog.update_job(job["job_id"], launch_failed, event="failed")
                logger.error("Management build process could not start: %s", message)
                return True
            diagnostics: deque[bytes] = deque(maxlen=4)
            assert process.stderr is not None
            reader = threading.Thread(
                target=_drain_diagnostics, args=(process.stderr, diagnostics), daemon=True
            )
            reader.start()
            self._process = process
            heartbeat_at = 0.0
            cancel_at: float | None = None
            while process.poll() is None and not self._stop.wait(0.2):
                now = time.monotonic()
                current = self.catalog.get("job", job["job_id"])
                if current["status"] == "cancel_requested":
                    cancel_at = cancel_at or now
                    if now - cancel_at >= _CANCEL_GRACE_SECONDS:
                        _terminate_worker(process)
                        break
                if now - heartbeat_at >= _HEARTBEAT_SECONDS:
                    self.catalog.update_job(
                        job["job_id"],
                        lambda value: value.update(worker_heartbeat_at=timestamp()),
                        event="heartbeat",
                    )
                    heartbeat_at = now
            if self._stop.is_set() and process.stdin is not None:
                if not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_worker(process)
            elif process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            self._process = None
            reader.join(timeout=2)
            process.stderr.close()
            current = self.catalog.get("job", job["job_id"])
            if current["status"] not in _TERMINAL:
                failure_message = f"Build worker exited with code {process.returncode} before recording a terminal result"
                detail = b"".join(diagnostics).decode("utf-8", errors="replace").strip()
                if detail:
                    failure_message += ": " + self.safe_error(RuntimeError(detail))

                def interrupted(item: dict[str, Any], message: str = failure_message) -> None:
                    from codalith.indexing.shared import SharedPool

                    snapshots = SharedPool(
                        self.config.storage_root / "shared", self.policy
                    ).snapshots()
                    published = {
                        (value["target_id"], value["commit"]): value for value in snapshots
                    }
                    terminal = (
                        "cancelled" if item["status"] == "cancel_requested" else "interrupted"
                    )
                    for child in item["items"]:
                        snapshot = published.get((child["target_id"], child["commit"]))
                        if snapshot is not None:
                            child.update(
                                status="succeeded",
                                phase="published",
                                snapshot_id=snapshot["snapshot_id"],
                                generation_id=snapshot["generation_id"],
                            )
                        elif child["status"] != "succeeded":
                            child.update(status=terminal, phase=terminal)
                    count = sum(child["status"] == "succeeded" for child in item["items"])
                    if count == len(item["items"]):
                        terminal = "succeeded"
                    item.update(
                        status=terminal,
                        phase=terminal,
                        completed_tags=count,
                        completed_units=count,
                        error=None if terminal == "succeeded" else message,
                    )

                self.catalog.update_job(
                    job["job_id"],
                    interrupted,
                    event="interrupted",
                )
            if self.on_publish is not None:
                self.on_publish()
            return True
        return False

    def profile(self, plan: dict[str, Any]) -> Corpus:
        profile = self.profiles[plan["profile"]["profile_id"]]
        expected = plan["profile"]
        if (
            ADAPTERS[profile.adapter].version != expected["adapter_version"]
            or any(
                getattr(profile, name) != expected[name]
                for name in ("adapter", "embedding_provider", "embedding_model")
            )
            or list(profile.include_extensions) != expected["include_extensions"]
            or list(profile.exclude_globs) != expected["exclude_globs"]
        ):
            raise ValueError("Import profile changed after planning; create a new plan")
        policy_data = {
            "default_max_lines": self.policy.default_max_lines,
            "hard_max_lines": self.policy.hard_max_lines,
            "max_file_bytes": self.policy.max_file_bytes,
            "deny_globs": list(self.policy.deny_globs),
        }
        if plan["policy"] != policy_data:
            raise ValueError("Source policy changed after planning; create a new plan")
        return replace(profile)

    def routes(self) -> list[Any]:
        from codalith.management.routes import management_routes

        return management_routes(self)


def _drain_diagnostics(stream: IO[bytes], output: deque[bytes]) -> None:
    try:
        while block := stream.read(2048):
            output.append(block)
    except (OSError, ValueError):
        return


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
