"""Python AST structural adapter."""

from __future__ import annotations

import ast
from pathlib import Path

from codalith.languages.base import (
    ExtractionResult,
    ModuleDependencyObservation,
    ReferenceObservation,
    SymbolObservation,
)


class PythonAdapter:
    adapter_id = "python"
    version = 2

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".py", ".pyi"}

    def extract(self, path: str, text: str) -> ExtractionResult:
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return ExtractionResult(
                language="python",
                warnings=(f"{path}:{exc.lineno or 1}: {exc.msg}",),
            )
        visitor = _Visitor(path, _module_from_path(path))
        visitor.visit(tree)
        return ExtractionResult(
            language="python",
            symbols=tuple(visitor.symbols),
            references=tuple(visitor.references),
            module_dependencies=tuple(visitor.dependencies),
        )


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str | None) -> None:
        self.path = path
        self.module = module
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self.current_symbol: list[str] = []
        self.symbols: list[SymbolObservation] = []
        self.references: list[ReferenceObservation] = []
        self.dependencies: list[ModuleDependencyObservation] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_type(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, async_function=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, async_function=True)

    def visit_Call(self, node: ast.Call) -> None:
        target = _dotted_name(node.func)
        if target:
            self.references.append(
                ReferenceObservation(
                    source_qualified_name=self.current_symbol[-1] if self.current_symbol else None,
                    target_name=target,
                    kind="call",
                    path=self.path,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._dependency(alias.name.split(".", 1)[0], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._dependency(node.module.split(".", 1)[0], node.lineno)

    def _visit_type(self, node: ast.ClassDef, kind: str) -> None:
        qualified = ".".join([*self.scope, node.name])
        parent = ".".join(self.scope) or None
        bases = [name for item in node.bases if (name := _dotted_name(item))]
        self.symbols.append(
            SymbolObservation(
                qualified_name=qualified,
                name=node.name,
                kind=kind,
                signature=f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}",
                path=self.path,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                module=self.module,
                parent_qualified_name=parent,
                metadata={"bases": bases},
            )
        )
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.current_symbol.append(qualified)
        self.generic_visit(node)
        self.current_symbol.pop()
        self.scope_kinds.pop()
        self.scope.pop()

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        async_function: bool,
    ) -> None:
        qualified = ".".join([*self.scope, node.name])
        parent = ".".join(self.scope) or None
        if self.scope_kinds and self.scope_kinds[-1] == "class":
            kind = "method"
        elif async_function:
            kind = "async_function"
        else:
            kind = "function"
        signature = f"{'async ' if async_function else ''}def {node.name}{_signature(node)}"
        self.symbols.append(
            SymbolObservation(
                qualified_name=qualified,
                name=node.name,
                kind=kind,
                signature=signature,
                path=self.path,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                module=self.module,
                parent_qualified_name=parent,
            )
        )
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.current_symbol.append(qualified)
        self.generic_visit(node)
        self.current_symbol.pop()
        self.scope_kinds.pop()
        self.scope.pop()

    def _dependency(self, target: str, line: int) -> None:
        if self.module and target and target != self.module:
            self.dependencies.append(
                ModuleDependencyObservation(self.module, target, "import", self.path, line)
            )


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"({args}){returns}"


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _module_from_path(path: str) -> str | None:
    parts = Path(path).as_posix().split("/")
    if "src" in parts:
        index = parts.index("src")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[0] if len(parts) > 1 else Path(path).stem
