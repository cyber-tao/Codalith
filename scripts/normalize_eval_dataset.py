"""Normalize eval file expectations to corpus-relative source paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_PATH_OVERRIDES = {
    "GameplayAbilities.Build.cs": (
        "Engine/Plugins/Runtime/GameplayAbilities/Source/GameplayAbilities/"
        "GameplayAbilities.Build.cs"
    ),
}

_SYMBOL_EXPECTATIONS = {
    "ue50-002": ["AActor"],
    "ue50-003": ["UObject"],
    "ue50-004": ["TArray"],
    "ue50-005": ["FName"],
    "ue50-009": ["UFUNCTION"],
    "ue50-010": ["UPROPERTY"],
    "ue50-011": ["UClass"],
    "ue50-012": ["UWorld"],
    "ue50-017": ["BeginPlay"],
    "ue50-020": ["TargetRules"],
    "ue57-common-001": ["TObjectPtr", "TWeakObjectPtr"],
    "ue57-common-002": ["CreateDefaultSubobject", "NewObject"],
    "ue57-common-008": ["AddDynamic", "UFUNCTION"],
    "ue57-common-013": ["SpawnActor", "FActorSpawnParameters"],
    "ue57-common-015": ["FTimerHandle", "FTimerManager"],
    "ue57-common-017": ["FCollisionQueryParams", "LineTraceSingleByChannel"],
    "ue57-common-018": ["UPROPERTY", "DOREPLIFETIME"],
    "ue57-common-020": ["GetFunctionCallspace", "ProcessRemoteFunction"],
    "ue57-common-025": ["FFastArraySerializer", "MarkItemDirty"],
    "ue57-common-030": ["TSoftObjectPtr", "FStreamableManager"],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="eval/datasets/ue_eval_suite.jsonl",
    )
    parser.add_argument(
        "--priors",
        default="configs/corpora/ue-5.7.4/source_priors.json",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    dataset_path = Path(args.dataset)
    rows = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    priors = json.loads(Path(args.priors).read_text(encoding="utf-8"))
    paths_by_name: dict[str, set[str]] = {}
    for prior in priors.get("priors", []):
        path = str(prior["path"]).replace("\\", "/")
        paths_by_name.setdefault(Path(path).name, set()).add(path)
    normalized = [
        _normalize_row(row, paths_by_name)
        for row in rows
    ]
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in normalized
    )
    current = dataset_path.read_text(encoding="utf-8")
    if args.check:
        if current != rendered:
            raise SystemExit("UE eval dataset expectations are not normalized")
        return 0
    dataset_path.write_text(rendered, encoding="utf-8")
    return 0


def _normalize_row(
    row: dict[str, Any],
    paths_by_name: dict[str, set[str]],
) -> dict[str, Any]:
    verified_paths = {
        str(source).rsplit(":", 1)[0].replace("\\", "/")
        for source in row.get("verified_sources", [])
    }
    expected_paths: list[str] = []
    for expected in row.get("expected_files", []):
        expected_path = str(expected).replace("\\", "/")
        if "/" in expected_path:
            candidates = {expected_path}
        else:
            candidates = {
                path for path in verified_paths if Path(path).name == expected_path
            }
            if not candidates and expected_path in _PATH_OVERRIDES:
                candidates = {_PATH_OVERRIDES[expected_path]}
            if not candidates:
                candidates = paths_by_name.get(expected_path, set())
        if len(candidates) != 1:
            raise ValueError(
                f"{row.get('id')} cannot resolve {expected_path!r}: "
                f"{sorted(candidates)}"
            )
        candidate = next(iter(candidates))
        if candidate not in expected_paths:
            expected_paths.append(candidate)
    normalized = dict(row)
    normalized["expected_files"] = expected_paths
    symbols = _SYMBOL_EXPECTATIONS.get(str(row.get("id")))
    if symbols:
        normalized["expected_symbols"] = symbols
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
