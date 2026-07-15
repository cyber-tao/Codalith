"""C# structural adapter backed by the official tree-sitter grammar."""

from __future__ import annotations

from pathlib import Path

import tree_sitter_c_sharp
from tree_sitter import Language, Node, Parser

from codalith.languages.base import (
    ExtractionResult,
    ModuleDependencyObservation,
    ReferenceObservation,
    SymbolObservation,
)

_TYPE_NODES = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "record_struct_declaration": "record_struct",
}
_CALLABLE_NODES = {
    "method_declaration",
    "constructor_declaration",
    "destructor_declaration",
    "operator_declaration",
    "conversion_operator_declaration",
    "local_function_statement",
}
_MEMBER_NODES = {
    "property_declaration": "property",
    "event_declaration": "event",
    "indexer_declaration": "indexer",
}


class CSharpAdapter:
    adapter_id = "csharp"
    version = 1

    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_c_sharp.language()))

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() == ".cs"

    def extract(self, path: str, text: str) -> ExtractionResult:
        source = text.encode("utf-8")
        tree = self._parser.parse(source)
        state = _CSharpState(path, source)
        state.walk(tree.root_node)
        warnings: tuple[str, ...] = ()
        if tree.root_node.has_error:
            warnings = (
                f"{path}: tree-sitter reported C# parse errors; partial index retained",
            )
        return ExtractionResult(
            language="csharp",
            symbols=tuple(_dedupe_symbols(state.symbols)),
            references=tuple(_dedupe_references(state.references)),
            module_dependencies=tuple(_dedupe_dependencies(state.dependencies)),
            warnings=warnings,
        )


class _CSharpState:
    def __init__(self, path: str, source: bytes) -> None:
        self.path = path
        self.source = source
        self.symbols: list[SymbolObservation] = []
        self.references: list[ReferenceObservation] = []
        self.dependencies: list[ModuleDependencyObservation] = []

    def walk(self, root: Node) -> None:
        initial_namespace = _file_scoped_namespace(root, self.source)
        stack: list[tuple[Node, tuple[str, ...], tuple[str, ...], str | None]] = [
            (root, initial_namespace, (), None)
        ]
        while stack:
            node, namespaces, types, current_symbol = stack.pop()
            children = self._visit(node, namespaces, types, current_symbol)
            stack.extend(reversed(children))

    def _visit(
        self,
        node: Node,
        namespaces: tuple[str, ...],
        types: tuple[str, ...],
        current_symbol: str | None,
    ) -> list[tuple[Node, tuple[str, ...], tuple[str, ...], str | None]]:
        node_type = node.type
        if node_type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            namespace = _text(name_node, self.source).replace(" ", "") if name_node else ""
            nested = (*namespaces, *(part for part in namespace.split(".") if part))
            if body is not None:
                return [(body, nested, types, current_symbol)]

        if node_type in _TYPE_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, self.source)
                qualified = _qualified(name, namespaces, types)
                body = node.child_by_field_name("body")
                self.symbols.append(
                    SymbolObservation(
                        qualified_name=qualified,
                        name=name,
                        kind=_TYPE_NODES[node_type],
                        signature=_declaration_signature(node, body, self.source),
                        path=self.path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        module=_module_name(self.path, namespaces),
                        parent_qualified_name=(
                            _qualified(types[-1], namespaces, types[:-1]) if types else None
                        ),
                        metadata={"bases": _base_names(node, self.source)},
                    )
                )
                if body is not None:
                    return [(body, namespaces, (*types, name), qualified)]

        if node_type == "delegate_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, self.source)
                self.symbols.append(
                    SymbolObservation(
                        qualified_name=_qualified(name, namespaces, types),
                        name=name,
                        kind="delegate",
                        signature=_declaration_signature(node, None, self.source),
                        path=self.path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        module=_module_name(self.path, namespaces),
                        parent_qualified_name=(
                            _qualified(types[-1], namespaces, types[:-1]) if types else None
                        ),
                    )
                )

        if node_type in _CALLABLE_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, self.source)
                qualified = _qualified(name, namespaces, types)
                body = node.child_by_field_name("body")
                self.symbols.append(
                    SymbolObservation(
                        qualified_name=qualified,
                        name=name,
                        kind=(
                            "function"
                            if node_type == "local_function_statement"
                            else "constructor"
                            if node_type == "constructor_declaration"
                            else "method"
                        ),
                        signature=_declaration_signature(node, body, self.source),
                        path=self.path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        module=_module_name(self.path, namespaces),
                        parent_qualified_name=(
                            _qualified(types[-1], namespaces, types[:-1]) if types else None
                        ),
                    )
                )
                if body is not None:
                    return [(body, namespaces, types, qualified)]

        if node_type in _MEMBER_NODES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, self.source)
                self.symbols.append(
                    SymbolObservation(
                        qualified_name=_qualified(name, namespaces, types),
                        name=name,
                        kind=_MEMBER_NODES[node_type],
                        signature=_declaration_signature(
                            node,
                            node.child_by_field_name("body")
                            or node.child_by_field_name("accessors"),
                            self.source,
                        ),
                        path=self.path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        module=_module_name(self.path, namespaces),
                        parent_qualified_name=(
                            _qualified(types[-1], namespaces, types[:-1]) if types else None
                        ),
                    )
                )

        if node_type == "invocation_expression" and current_symbol:
            function = node.child_by_field_name("function")
            if function is not None:
                target = _text(function, self.source).replace(" ", "")
                if target:
                    self.references.append(
                        ReferenceObservation(
                            source_qualified_name=current_symbol,
                            target_name=target,
                            kind="call",
                            path=self.path,
                            line=node.start_point[0] + 1,
                        )
                    )

        if node_type == "using_directive":
            using_target = _using_target(_text(node, self.source))
            source_module = _module_name(self.path, namespaces)
            if using_target and source_module and using_target != source_module:
                self.dependencies.append(
                    ModuleDependencyObservation(
                        source_module=source_module,
                        target_module=using_target,
                        kind="using",
                        path=self.path,
                        line=node.start_point[0] + 1,
                    )
                )

        return [
            (child, namespaces, types, current_symbol)
            for child in node.named_children
        ]


def _file_scoped_namespace(root: Node, source: bytes) -> tuple[str, ...]:
    for child in root.named_children:
        if child.type != "file_scoped_namespace_declaration":
            continue
        name = child.child_by_field_name("name")
        if name is not None:
            return tuple(part for part in _text(name, source).split(".") if part)
    return ()


def _qualified(
    name: str,
    namespaces: tuple[str, ...],
    types: tuple[str, ...],
) -> str:
    return ".".join((*namespaces, *types, name))


def _module_name(path: str, namespaces: tuple[str, ...]) -> str:
    if namespaces:
        return ".".join(namespaces)
    name = Path(path).name
    return name.removesuffix(".cs").rsplit(".", 1)[0]


def _declaration_signature(node: Node, body: Node | None, source: bytes) -> str:
    end = body.start_byte if body is not None else node.end_byte
    signature = " ".join(source[node.start_byte:end].decode("utf-8", errors="replace").split())
    return signature if len(signature) <= 4_000 else signature[:3_999] + "…"


def _base_names(node: Node, source: bytes) -> list[str]:
    for child in node.named_children:
        if child.type == "base_list":
            return [
                _text(item, source).replace(" ", "")
                for item in child.named_children
            ]
    return []


def _using_target(value: str) -> str | None:
    candidate = value.strip().removeprefix("global ").removeprefix("using ")
    candidate = candidate.removeprefix("static ").removesuffix(";").strip()
    if "=" in candidate:
        candidate = candidate.rsplit("=", 1)[-1].strip()
    return candidate or None


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _dedupe_symbols(items: list[SymbolObservation]) -> list[SymbolObservation]:
    seen: set[tuple[str, str, str, int]] = set()
    result: list[SymbolObservation] = []
    for item in items:
        key = (item.qualified_name, item.kind, item.path, item.start_line)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedupe_references(items: list[ReferenceObservation]) -> list[ReferenceObservation]:
    seen: set[tuple[str | None, str, str, int]] = set()
    result: list[ReferenceObservation] = []
    for item in items:
        key = (item.source_qualified_name, item.target_name, item.path, item.line)
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
