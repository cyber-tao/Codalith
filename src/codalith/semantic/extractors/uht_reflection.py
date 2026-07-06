"""UHT reflection macro extractor v0."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReflectionEntity:
    kind: str
    name: str
    owner: str | None
    specifiers: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    declaration_uri: str | None = None
    generated_header: str | None = None
    module_name: str | None = None
    confidence: float = 1.0


class UHTReflectionExtractor:
    _MACRO_RE = re.compile(r"\b(?P<macro>UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY)\s*\((?P<body>[^)]*)\)")
    _GENERATED_MACRO_RE = re.compile(r"\b(?P<macro>GENERATED_BODY|GENERATED_UCLASS_BODY|GENERATED_USTRUCT_BODY)\s*\(")
    _GENERATED_RE = re.compile(r'#include\s+"(?P<header>[^"]+\.generated\.h)"')
    _CLASS_RE = re.compile(r"\b(?:class|struct)\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
    _ENUM_RE = re.compile(r"\benum\s+(?:class\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
    _FUNCTION_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
    _PROPERTY_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;")

    def extract_text(
        self,
        text: str,
        *,
        module_name: str | None = None,
        declaration_uri: str | None = None,
    ) -> list[ReflectionEntity]:
        generated = self._generated_header(text)
        entities: list[ReflectionEntity] = []
        owner: str | None = None
        pending: tuple[str, dict[str, Any]] | None = None
        for line in text.splitlines():
            generated_macro = self._GENERATED_MACRO_RE.search(line)
            if generated_macro and owner:
                entities.append(
                    ReflectionEntity(
                        kind="generated_macro",
                        name=generated_macro.group("macro"),
                        owner=owner,
                        specifiers={},
                        declaration_uri=declaration_uri,
                        generated_header=generated,
                        module_name=module_name,
                        confidence=0.8,
                    )
                )
            macro = self._MACRO_RE.search(line)
            if macro:
                pending = (macro.group("macro"), parse_specifiers(macro.group("body")))
                continue
            if pending is None:
                class_match = self._CLASS_RE.search(line)
                if class_match:
                    owner = class_match.group("name")
                continue
            macro_name, specifiers = pending
            if macro_name in {"UCLASS", "USTRUCT", "UINTERFACE"}:
                class_match = self._CLASS_RE.search(line)
                if class_match:
                    owner = class_match.group("name")
                    entities.append(
                        ReflectionEntity(
                            kind=macro_name.lower(),
                            name=owner,
                            owner=None,
                            specifiers=specifiers,
                            declaration_uri=declaration_uri,
                            generated_header=generated,
                            module_name=module_name,
                        )
                    )
                    generated_inline = self._GENERATED_MACRO_RE.search(line)
                    if generated_inline:
                        entities.append(
                            ReflectionEntity(
                                kind="generated_macro",
                                name=generated_inline.group("macro"),
                                owner=owner,
                                specifiers={},
                                declaration_uri=declaration_uri,
                                generated_header=generated,
                                module_name=module_name,
                                confidence=0.8,
                            )
                        )
                    pending = None
                continue
            if macro_name == "UENUM":
                enum_match = self._ENUM_RE.search(line)
                if enum_match:
                    name = enum_match.group("name")
                    entities.append(
                        ReflectionEntity(
                            kind="uenum",
                            name=name,
                            owner=None,
                            specifiers=specifiers,
                            declaration_uri=declaration_uri,
                            generated_header=generated,
                            module_name=module_name,
                        )
                    )
                    pending = None
                continue
            if macro_name == "UFUNCTION":
                function_match = self._FUNCTION_RE.search(line)
                if function_match:
                    name = function_match.group("name")
                    entities.append(
                        ReflectionEntity(
                            kind="ufunction",
                            name=name,
                            owner=owner,
                            specifiers=specifiers,
                            declaration_uri=declaration_uri,
                            generated_header=generated,
                            module_name=module_name,
                        )
                    )
                    pending = None
                continue
            if macro_name == "UPROPERTY":
                property_match = self._PROPERTY_RE.search(line)
                if property_match:
                    name = property_match.group("name")
                    metadata: dict[str, Any] = {}
                    if "ReplicatedUsing" in specifiers:
                        metadata["rep_notify"] = specifiers["ReplicatedUsing"]
                    entities.append(
                        ReflectionEntity(
                            kind="uproperty",
                            name=name,
                            owner=owner,
                            specifiers=specifiers,
                            metadata=metadata,
                            declaration_uri=declaration_uri,
                            generated_header=generated,
                            module_name=module_name,
                        )
                    )
                    pending = None
        return entities

    def extract_file(self, path: str | Path, *, declaration_uri: str | None = None) -> list[ReflectionEntity]:
        return self.extract_text(Path(path).read_text(encoding="utf-8"), declaration_uri=declaration_uri)

    def _generated_header(self, text: str) -> str | None:
        match = self._GENERATED_RE.search(text)
        return match.group("header") if match else None


def parse_specifiers(text: str) -> dict[str, Any]:
    specifiers: dict[str, Any] = {}
    for part in _split_top_level(text):
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            specifiers[key.strip()] = value.strip().strip('"')
        else:
            specifiers[part.strip()] = True
    return specifiers


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts
