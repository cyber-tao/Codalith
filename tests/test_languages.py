from __future__ import annotations

from pathlib import Path

from codalith.languages.cpp_ue import CppUEAdapter
from codalith.languages.csharp import CSharpAdapter
from codalith.languages.python import PythonAdapter


def test_python_adapter_extracts_nested_symbols_calls_and_imports() -> None:
    result = PythonAdapter().extract(
        "src/demo/service.py",
        "import json\nfrom pathlib import Path\n\n"
        "class Service:\n"
        "    async def run(self, path: str) -> str:\n"
        "        return json.dumps(Path(path).name)\n",
    )
    symbols = {item.qualified_name: item for item in result.symbols}
    assert symbols["Service"].kind == "class"
    assert symbols["Service.run"].kind == "method"
    assert symbols["Service.run"].parent_qualified_name == "Service"
    assert {item.target_name for item in result.references} >= {
        "json.dumps",
        "Path",
    }
    assert {item.target_module for item in result.module_dependencies} == {
        "json",
        "pathlib",
    }


def test_python_adapter_retains_partial_build_as_a_warning() -> None:
    result = PythonAdapter().extract("broken.py", "def nope(:\n")
    assert not result.symbols
    assert result.warnings and result.warnings[0].startswith("broken.py:1:")


def test_python_adapter_does_not_classify_nested_functions_as_methods() -> None:
    result = PythonAdapter().extract(
        "src/demo/nested.py",
        "def outer():\n    def inner():\n        return 1\n    return inner()\n",
    )
    symbols = {item.qualified_name: item for item in result.symbols}
    assert symbols["outer.inner"].kind == "function"
    assert symbols["outer.inner"].parent_qualified_name == "outer"


def test_cpp_ue_adapter_extracts_reflection_symbols_and_calls() -> None:
    source = """#include \"Gameplay/Ability.h\"
class AActor;
UCLASS(BlueprintType)
class ENGINE_API AHero : public AActor
{
public:
    UPROPERTY(EditAnywhere, Replicated)
    int32 Health;

    UFUNCTION(BlueprintCallable)
    void Jump();

    void Tick() { Jump(); }
};
"""
    result = CppUEAdapter().extract(
        "Engine/Source/Runtime/Engine/Public/Hero.h",
        source,
    )
    symbols = {item.qualified_name: item for item in result.symbols}
    assert "AActor" not in symbols
    assert symbols["AHero"].metadata["ue_macro"] == "UCLASS"
    assert symbols["AHero::Health"].metadata["ue_macro"] == "UPROPERTY"
    assert symbols["AHero::Jump"].metadata["ue_macro"] == "UFUNCTION"
    assert symbols["AHero::Tick"].kind == "method"
    assert any(item.target_name == "Jump" for item in result.references)
    assert any(
        item.source_module == "Engine" and item.target_module == "Gameplay"
        for item in result.module_dependencies
    )


def test_cpp_ue_adapter_extracts_build_cs_dependencies() -> None:
    path = Path("Engine/Source/Runtime/MyModule/MyModule.Build.cs")
    adapter = CppUEAdapter()
    assert adapter.supports(path)
    result = adapter.extract(
        path.as_posix(),
        'PublicDependencyModuleNames.AddRange(new string[] { "Core", "Engine" });\n'
        'PrivateDependencyModuleNames.Add("Slate");\n',
    )
    assert result.language == "csharp"
    assert result.symbols[0].qualified_name == "MyModule"
    assert {item.target_module for item in result.module_dependencies} == {
        "Core",
        "Engine",
        "Slate",
    }
    assert {item.kind for item in result.module_dependencies} == {"public", "private"}


def test_cpp_ue_adapter_extracts_dynamic_dependencies_and_target_kind() -> None:
    adapter = CppUEAdapter()
    build = adapter.extract(
        "Engine/Source/Runtime/MyModule/MyModule.Build.CS",
        'DynamicallyLoadedModuleNames.Add("OnlineSubsystem");\n',
    )
    assert build.symbols[0].kind == "module"
    assert build.module_dependencies[0].kind == "dynamic"
    target = adapter.extract(
        "Engine/Source/MyGame.Target.cs",
        "public class MyGameTarget : TargetRules {}\n",
    )
    assert target.symbols[0].kind == "target"


def test_cpp_ue_adapter_extracts_type_aliases_and_macro_definitions() -> None:
    result = CppUEAdapter().extract(
        "Engine/Source/Runtime/Core/Public/Math/MathFwd.h",
        "#define UFUNCTION(...) BODY\n"
        "using FVector = UE::Math::TVector<double>;\n",
    )
    symbols = {item.name: item for item in result.symbols}
    assert symbols["UFUNCTION"].kind == "macro"
    assert symbols["FVector"].kind == "type_alias"


def test_cpp_ue_adapter_masks_nested_reflection_macro_arguments() -> None:
    result = CppUEAdapter().extract(
        "Engine/Source/Runtime/Engine/Public/NestedMetadata.h",
        "class FExample {\n"
        "  UPROPERTY(meta=(EditCondition=\"IsReady() && (Count > 0)\"))\n"
        "  int32 Count;\n"
        "};\n",
    )
    symbols = {item.name: item for item in result.symbols}
    assert "UPROPERTY" not in symbols
    assert symbols["Count"].kind == "field"
    assert symbols["Count"].metadata["ue_macro"] == "UPROPERTY"


def test_csharp_adapter_extracts_types_members_calls_and_usings() -> None:
    result = CSharpAdapter().extract(
        "Engine/Source/Programs/UnrealBuildTool/Configuration/TargetRules.cs",
        "using System.Collections.Generic;\n"
        "namespace UnrealBuildTool {\n"
        "public class TargetRules : RulesBase {\n"
        "  public string Name { get; set; }\n"
        "  public void Validate() { Helper.Check(Name); }\n"
        "}\n}\n",
    )
    symbols = {item.qualified_name: item for item in result.symbols}
    assert symbols["UnrealBuildTool.TargetRules"].kind == "class"
    assert symbols["UnrealBuildTool.TargetRules.Name"].kind == "property"
    assert symbols["UnrealBuildTool.TargetRules.Validate"].kind == "method"
    assert any(item.target_name == "Helper.Check" for item in result.references)
    assert any(
        item.target_module == "System.Collections.Generic"
        for item in result.module_dependencies
    )


def test_cpp_ue_adapter_uses_an_iterative_walker_for_deep_trees() -> None:
    depth = 1_200
    source = "int Deep() { return " + "(" * depth + "1" + ")" * depth + "; }\n"
    result = CppUEAdapter().extract("Engine/Source/Runtime/Core/Private/Deep.cpp", source)
    assert any(symbol.name == "Deep" for symbol in result.symbols)
