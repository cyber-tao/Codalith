"""C++ and Unreal Engine structural extraction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from codalith.languages.base import (
    ExtractionResult,
    ModuleDependencyObservation,
    ReferenceObservation,
    SymbolObservation,
)
from codalith.languages.csharp import CSharpAdapter

_CPP_SUFFIXES = {".h", ".hpp", ".inl", ".c", ".cc", ".cpp"}
_GENERIC_SOURCE_LANGUAGES = {
    ".ispc": "ispc",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".usf": "hlsl",
    ".ush": "hlsl",
}
_TYPE_NODES = {
    "class_specifier": "class",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
}
_UE_MACRO = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UFUNCTION|UPROPERTY)\s*\((.*?)\)", re.S)
_UE_PARSE_MACRO_NAME = re.compile(
    r"\b(?:UCLASS|USTRUCT|UENUM|UFUNCTION|UPROPERTY|GENERATED_BODY)\b"
)
_API_EXPORT = re.compile(r"\b[A-Z][A-Z0-9_]*_API\b")
_DEPENDENCY_BLOCK = re.compile(
    r"(?P<kind>PublicDependencyModuleNames|PrivateDependencyModuleNames|"
    r"DynamicallyLoadedModuleNames)\s*\.\s*"
    r"(?:Add|AddRange)\s*\((.*?)(?:\);)",
    re.S,
)
_DEPENDENCY_KIND = {
    "PublicDependencyModuleNames": "public",
    "PrivateDependencyModuleNames": "private",
    "DynamicallyLoadedModuleNames": "dynamic",
}
_QUOTED = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')


class CppUEAdapter:
    adapter_id = "cpp-ue"
    version = 5

    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_cpp.language()))
        self._csharp = CSharpAdapter()

    def supports(self, path: Path) -> bool:
        suffix = path.suffix.casefold()
        return suffix == ".cs" or suffix in _CPP_SUFFIXES | _GENERIC_SOURCE_LANGUAGES.keys()

    def extract(self, path: str, text: str) -> ExtractionResult:
        normalized_path = path.casefold()
        if normalized_path.endswith((".build.cs", ".target.cs")):
            return _extract_build_file(path, text, self._csharp)
        if normalized_path.endswith(".cs"):
            return self._csharp.extract(path, text)
        suffix = Path(path).suffix.lower()
        if suffix in _GENERIC_SOURCE_LANGUAGES:
            return ExtractionResult(language=_GENERIC_SOURCE_LANGUAGES[suffix])
        source = _sanitized_source(text)
        tree = self._parser.parse(source)
        state = _CppState(path, text, source)
        state.walk(tree.root_node, (), (), None)
        warnings: tuple[str, ...] = ()
        if tree.root_node.has_error:
            warnings = (f"{path}: tree-sitter reported parse errors; partial index retained",)
        return ExtractionResult(
            language="cpp",
            symbols=tuple(_dedupe_symbols(state.symbols)),
            references=tuple(state.references),
            module_dependencies=tuple(_dedupe_dependencies(state.dependencies)),
            warnings=warnings,
        )


class _CppState:
    def __init__(self, path: str, text: str, source: bytes) -> None:
        self.path = path
        self.lines = text.split("\n")
        self.source = source
        self.module = _module_from_path(path)
        self.symbols: list[SymbolObservation] = []
        self.references: list[ReferenceObservation] = []
        self.dependencies: list[ModuleDependencyObservation] = []

    def walk(
        self,
        node: Node,
        namespaces: tuple[str, ...],
        types: tuple[str, ...],
        current_symbol: str | None,
    ) -> None:
        frames: list[
            tuple[Node, tuple[str, ...], tuple[str, ...], str | None]
        ] = [(node, namespaces, types, current_symbol)]
        while frames:
            current, current_namespaces, current_types, owner = frames.pop()
            children = self._visit(
                current,
                current_namespaces,
                current_types,
                owner,
            )
            frames.extend(reversed(children))

    def _visit(
        self,
        node: Node,
        namespaces: tuple[str, ...],
        types: tuple[str, ...],
        current_symbol: str | None,
    ) -> list[tuple[Node, tuple[str, ...], tuple[str, ...], str | None]]:
        node_type = node.type
        if node_type == "namespace_definition":
            name = self._field_text(node, "name")
            body = node.child_by_field_name("body")
            if name and body is not None:
                return [(body, (*namespaces, name), types, current_symbol)]
        if node_type in _TYPE_NODES:
            name = self._field_text(node, "name")
            if name:
                body = node.child_by_field_name("body")
                if body is None:
                    return []
                qualified = "::".join([*namespaces, *types, name])
                metadata = self._ue_metadata(node.start_point[0])
                self.symbols.append(
                    SymbolObservation(
                        qualified_name=qualified,
                        name=name,
                        kind=_TYPE_NODES[node_type],
                        signature=self._type_signature(node, name),
                        path=self.path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        module=self.module,
                        parent_qualified_name="::".join([*namespaces, *types]) or None,
                        metadata=metadata,
                    )
                )
                return [(body, namespaces, (*types, name), qualified)]
        if node_type in {"alias_declaration", "type_definition"}:
            name_node = node.child_by_field_name("name") or node.child_by_field_name(
                "declarator"
            )
            if name_node is not None:
                name = _normalize(_text(name_node, self.source))
                if name:
                    self.symbols.append(
                        SymbolObservation(
                            qualified_name=_qualify(name, namespaces, types),
                            name=name,
                            kind="type_alias",
                            signature=_bounded_signature(_text(node, self.source)),
                            path=self.path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            module=self.module,
                            parent_qualified_name="::".join([*namespaces, *types])
                            or None,
                        )
                    )
        if node_type in {"preproc_def", "preproc_function_def"}:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _normalize(_text(name_node, self.source))
                if name:
                    self.symbols.append(
                        SymbolObservation(
                            qualified_name=_qualify(name, namespaces, types),
                            name=name,
                            kind="macro",
                            signature=_bounded_signature(_text(node, self.source)),
                            path=self.path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            module=self.module,
                            parent_qualified_name="::".join([*namespaces, *types])
                            or None,
                        )
                    )
        if node_type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            if declarator is not None:
                name = _declarator_name(declarator, self.source)
                if name:
                    qualified = _qualify(name, namespaces, types)
                    self._add_function(node, declarator, name, qualified, namespaces, types)
                    body = node.child_by_field_name("body")
                    return [(body, namespaces, types, qualified)] if body is not None else []
        if node_type in {"declaration", "field_declaration"}:
            declarator = _find_descendant(node, "function_declarator")
            if declarator is not None:
                name = _declarator_name(declarator, self.source)
                if name:
                    qualified = _qualify(name, namespaces, types)
                    self._add_function(node, declarator, name, qualified, namespaces, types)
            elif node_type == "field_declaration":
                self._add_reflected_field(node, namespaces, types)
        if node_type == "call_expression" and current_symbol:
            function = node.child_by_field_name("function")
            if function is not None:
                target = _normalize(_text(function, self.source))
                if target:
                    self.references.append(
                        ReferenceObservation(
                            current_symbol,
                            target,
                            "call",
                            self.path,
                            node.start_point[0] + 1,
                        )
                    )
        if node_type == "preproc_include" and self.module:
            dependency_target = _include_target(_text(node, self.source))
            if dependency_target and dependency_target != self.module:
                self.dependencies.append(
                    ModuleDependencyObservation(
                        self.module,
                        dependency_target,
                        "include",
                        self.path,
                        node.start_point[0] + 1,
                    )
                )
        return [
            (child, namespaces, types, current_symbol)
            for child in node.named_children
        ]

    def _add_reflected_field(
        self,
        node: Node,
        namespaces: tuple[str, ...],
        types: tuple[str, ...],
    ) -> None:
        metadata = self._ue_metadata(node.start_point[0])
        if metadata.get("ue_macro") != "UPROPERTY":
            return
        name_node = _find_identifier(node)
        if name_node is None:
            return
        name = _text(name_node, self.source)
        qualified = _qualify(name, namespaces, types)
        self.symbols.append(
            SymbolObservation(
                qualified_name=qualified,
                name=name,
                kind="field",
                signature=_normalize(_text(node, self.source)),
                path=self.path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                module=self.module,
                parent_qualified_name="::".join([*namespaces, *types]) or None,
                metadata=metadata,
            )
        )

    def _add_function(
        self,
        node: Node,
        declarator: Node,
        raw_name: str,
        qualified: str,
        namespaces: tuple[str, ...],
        types: tuple[str, ...],
    ) -> None:
        name = raw_name.split("::")[-1]
        kind = "method" if types or "::" in raw_name else "function"
        metadata = self._ue_metadata(node.start_point[0])
        self.symbols.append(
            SymbolObservation(
                qualified_name=qualified,
                name=name,
                kind=kind,
                signature=_normalize(_text(declarator, self.source)),
                path=self.path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                module=self.module,
                parent_qualified_name="::".join([*namespaces, *types]) or None,
                metadata=metadata,
            )
        )

    def _field_text(self, node: Node, field: str) -> str | None:
        child = node.child_by_field_name(field)
        return _normalize(_text(child, self.source)) if child is not None else None

    def _type_signature(self, node: Node, name: str) -> str:
        base = node.child_by_field_name("base_class_clause")
        if base is None:
            base = next((child for child in node.named_children if child.type == "base_class_clause"), None)
        return f"{_TYPE_NODES[node.type]} {name}{' ' + _normalize(_text(base, self.source)) if base else ''}"

    def _ue_metadata(self, zero_based_line: int) -> dict[str, Any]:
        current = self.lines[zero_based_line] if zero_based_line < len(self.lines) else ""
        matches = list(_UE_MACRO.finditer(current))
        prefix: list[str] = []
        for index in range(zero_based_line - 1, max(-1, zero_based_line - 9), -1):
            line = self.lines[index]
            if not line.strip():
                break
            prefix.insert(0, line)
            matches = list(_UE_MACRO.finditer("\n".join(prefix)))
            if matches:
                break
            if any(marker in line for marker in (";", "{", "}")):
                break
        if not matches:
            return {}
        match = matches[-1]
        specifiers = [item.strip() for item in match.group(2).split(",") if item.strip()]
        return {"ue_macro": match.group(1), "ue_specifiers": specifiers}


def _extract_build_file(
    path: str,
    text: str,
    csharp: CSharpAdapter,
) -> ExtractionResult:
    extracted = csharp.extract(path, text)
    module = Path(path).name.split(".", 1)[0]
    is_target = path.casefold().endswith(".target.cs")
    symbols = (
        SymbolObservation(
            qualified_name=module,
            name=module,
            kind="target" if is_target else "module",
            signature=f"{'target' if is_target else 'module'} {module}",
            path=path,
            start_line=1,
            end_line=max(1, len(text.split("\n"))),
            module=module,
        ),
        *(
            symbol
            for symbol in extracted.symbols
            if not (
                symbol.name == module
                and symbol.kind in {"class", "struct", "record", "record_struct"}
            )
        ),
    )
    dependencies: list[ModuleDependencyObservation] = []
    for block in _DEPENDENCY_BLOCK.finditer(text):
        line = text.count("\n", 0, block.start()) + 1
        dependency_kind = _DEPENDENCY_KIND[block.group("kind")]
        for target in _QUOTED.findall(block.group(2)):
            if target != module:
                dependencies.append(
                    ModuleDependencyObservation(
                        module,
                        target,
                        dependency_kind,
                        path,
                        line,
                    )
                )
    return ExtractionResult(
        language="csharp",
        symbols=symbols,
        references=extracted.references,
        module_dependencies=tuple(_dedupe_dependencies(dependencies)),
        warnings=extracted.warnings,
    )


def _declarator_name(node: Node, source: bytes) -> str | None:
    if node.type in {"identifier", "field_identifier", "operator_name", "destructor_name"}:
        return _normalize(_text(node, source))
    if node.type in {"qualified_identifier", "scoped_identifier"}:
        return _normalize(_text(node, source)).replace(" ", "")
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        result = _declarator_name(declarator, source)
        if result:
            return result
    for child in reversed(node.named_children):
        result = _declarator_name(child, source)
        if result:
            return result
    return None


def _find_descendant(node: Node, node_type: str) -> Node | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == node_type:
            return current
        stack.extend(reversed(current.named_children))
    return None


def _find_identifier(node: Node) -> Node | None:
    stack = list(node.named_children)
    while stack:
        current = stack.pop()
        if current.type in {"identifier", "field_identifier"}:
            return current
        stack.extend(current.named_children)
    return None


def _qualify(name: str, namespaces: tuple[str, ...], types: tuple[str, ...]) -> str:
    clean = name.replace(" ", "")
    if "::" in clean:
        return clean
    return "::".join([*namespaces, *types, clean])


def _module_from_path(path: str) -> str | None:
    parts = Path(path).as_posix().split("/")
    if "Source" in parts:
        index = parts.index("Source")
        if index + 1 < len(parts) and parts[index + 1] not in {
            "Runtime",
            "Developer",
            "Editor",
            "Programs",
        }:
            return parts[index + 1]
    for root in ("Runtime", "Developer", "Editor", "Programs"):
        if root in parts:
            index = parts.index(root)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def _include_target(text: str) -> str | None:
    match = re.search(r"[<\"]([^>\"]+)[>\"]", text)
    if not match:
        return None
    path = match.group(1)
    return path.split("/", 1)[0] if "/" in path else None


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _sanitized_source(text: str) -> bytes:
    sanitized = _mask_ue_macro_invocations(text)
    sanitized = _API_EXPORT.sub(_mask_bytes, sanitized)
    return sanitized.encode("utf-8")


def _mask_ue_macro_invocations(text: str) -> str:
    replacements: list[tuple[int, int]] = []
    cursor = 0
    while match := _UE_PARSE_MACRO_NAME.search(text, cursor):
        start = match.start()
        open_parenthesis = match.end()
        while open_parenthesis < len(text) and text[open_parenthesis].isspace():
            open_parenthesis += 1
        if open_parenthesis >= len(text) or text[open_parenthesis] != "(":
            cursor = match.end()
            continue
        end = _balanced_parenthesis_end(text, open_parenthesis)
        if end is None:
            cursor = match.end()
            continue
        line_start = text.rfind("\n", 0, start) + 1
        prefix = text[line_start:start]
        if re.fullmatch(r"\s*#\s*define\s*", prefix) is None:
            replacements.append((start, end))
        cursor = end
    if not replacements:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in replacements:
        pieces.append(text[cursor:start])
        pieces.append(_mask_text(text[start:end]))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _balanced_parenthesis_end(text: str, opening: int) -> int | None:
    depth = 0
    state = "code"
    index = opening
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if character == '"':
                state = "string"
            elif character == "'":
                state = "character"
            elif character == "/" and following == "/":
                state = "line_comment"
                index += 1
            elif character == "/" and following == "*":
                state = "block_comment"
                index += 1
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return index + 1
        elif state in {"string", "character"}:
            if character == "\\":
                index += 1
            elif (state == "string" and character == '"') or (
                state == "character" and character == "'"
            ):
                state = "code"
        elif state == "line_comment":
            if character == "\n":
                state = "code"
        elif state == "block_comment" and character == "*" and following == "/":
            state = "code"
            index += 1
        index += 1
    return None


def _mask_bytes(match: re.Match[str]) -> str:
    return _mask_text(match.group(0))


def _mask_text(value: str) -> str:
    return "".join(
        character if character in {"\r", "\n"} else " " * len(character.encode("utf-8"))
        for character in value
    )


def _bounded_signature(value: str) -> str:
    normalized = _normalize(value)
    return normalized if len(normalized) <= 4_000 else normalized[:3_999] + "…"


def _dedupe_symbols(items: list[SymbolObservation]) -> list[SymbolObservation]:
    seen: set[tuple[str, str, str, int]] = set()
    result: list[SymbolObservation] = []
    for item in items:
        key = (item.qualified_name, item.kind, item.path, item.start_line)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedupe_dependencies(
    items: list[ModuleDependencyObservation],
) -> list[ModuleDependencyObservation]:
    seen: set[tuple[str, str, str, str, int]] = set()
    result: list[ModuleDependencyObservation] = []
    for item in items:
        key = (item.source_module, item.target_module, item.kind, item.path, item.line)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
