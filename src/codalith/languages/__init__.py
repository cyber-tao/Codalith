"""Language adapter registry."""

from codalith.languages.base import LanguageAdapter
from codalith.languages.cpp_ue import CppUEAdapter
from codalith.languages.csharp import CSharpAdapter
from codalith.languages.generic import GenericAdapter
from codalith.languages.python import PythonAdapter


def create_adapter(adapter_id: str) -> LanguageAdapter:
    if adapter_id == "python":
        return PythonAdapter()
    if adapter_id == "cpp-ue":
        return CppUEAdapter()
    if adapter_id == "csharp":
        return CSharpAdapter()
    if adapter_id == "generic":
        return GenericAdapter()
    raise ValueError(f"Unsupported language adapter: {adapter_id}")


__all__ = ["LanguageAdapter", "create_adapter"]
