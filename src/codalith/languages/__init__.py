"""Language adapter registry; adapters load their parsers only when created."""

from codalith.languages.base import LanguageAdapter


def create_adapter(adapter_id: str) -> LanguageAdapter:
    if adapter_id == "python":
        from codalith.languages.python import PythonAdapter

        return PythonAdapter()
    if adapter_id == "cpp-ue":
        from codalith.languages.cpp_ue import CppUEAdapter

        return CppUEAdapter()
    if adapter_id == "csharp":
        from codalith.languages.csharp import CSharpAdapter

        return CSharpAdapter()
    if adapter_id == "generic":
        from codalith.languages.generic import GenericAdapter

        return GenericAdapter()
    raise ValueError(f"Unsupported language adapter: {adapter_id}")


__all__ = ["LanguageAdapter", "create_adapter"]
