#pragma once

#include "Components/ActorComponent.h"
#include "InventoryComponent.generated.h"

UCLASS(ClassGroup=(ProjectA), meta=(BlueprintSpawnableComponent))
class PROJECTA_API UInventoryComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UPROPERTY(ReplicatedUsing=OnRep_Items)
    int32 ItemCount = 0;

    UFUNCTION()
    void OnRep_Items();
};
