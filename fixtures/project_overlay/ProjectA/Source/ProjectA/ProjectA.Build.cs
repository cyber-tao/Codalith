using UnrealBuildTool;

public class ProjectA : ModuleRules
{
    public ProjectA(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new string[] { "GameplayAbilities" });
    }
}
