from __future__ import annotations

from pathlib import Path

from jobs.extract_semantic import extract_semantic_summary


def test_extract_semantic_summary_counts_fixture(fake_engine_root):
    summary = extract_semantic_summary(fake_engine_root)
    assert summary["modules"] >= 2
    assert summary["module_dependencies"] >= 4
    assert summary["reflection_entities"] >= 3


def test_stop_after_min_prunes_third_party_headers(tmp_path: Path):
    root = tmp_path / "ue"
    files = {
        "Engine/Source/Runtime/Engine/Engine.Build.cs": (
            'PublicDependencyModuleNames.AddRange(new string[] { "Core" });\n'
        ),
        "Engine/Source/Runtime/Engine/Public/FastActor.h": (
            "UCLASS()\n"
            "class ENGINE_API AFastActor : public UObject {\n"
            "GENERATED_BODY()\n"
            "UPROPERTY()\n"
            "int32 Health;\n"
            "#if WITH_EDITOR\n"
            "void EditorOnlyPreview();\n"
            "#endif\n"
            "};\n"
        ),
        "Engine/Source/ThirdParty/Noise/Public/NoisyHeader.h": (
            "UCLASS()\n"
            "class NOISE_API UNoisyHeader : public UObject { GENERATED_BODY() };\n"
            "#if PLATFORM_WINDOWS\n"
            "void Noise();\n"
            "#endif\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    summary = extract_semantic_summary(
        root,
        stop_after_min=True,
        min_modules=1,
        min_reflection_entities=1,
        min_guards=1,
    )

    assert summary["modules"] == 1
    assert summary["headers_scanned"] == 1
    assert summary["reflection_entities"] == 2
    assert summary["compile_guards"] == 1
