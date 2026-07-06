# Codalith UE5.7 Common-Issue MCP Evaluation

日期: 2026-07-06
源码根目录: `E:/UnrealEngine_5.7`
Codalith 查询版本: `5.7.4`
评估数据集: `eval/datasets/ue57_common_issues_30.jsonl`
评估报告: `eval/benchmarks/ue57_common_issues_mcp_eval.md`

## 结论

本轮选取 30 个 UE5 开发中高频出现的问题，先从 Epic 官方文档、常见工程经验和社区问题中归纳候选答案，再用 `E:/UnrealEngine_5.7/Engine/Source` 做源码核验和修正。30 个问题都已落到 UE5.7 源码文件与行号证据。

对 Codalith MCP 的测试使用已连接的 `codalith_context` 工具，参数为 `version=5.7.4`、`include_project_overlay=false`、`max_source_spans=5`。判定口径是 MCP 返回的 cards/source spans 是否足以支撑正确答案。

结果:

- `PASS`: 17/30
- `PARTIAL`: 9/30
- `FAIL`: 4/30
- 本轮测试验证的是 MCP 检索和上下文供给能力；由于本机 Docker Desktop Linux engine 未运行、HTTP MCP endpoint 未启动，未通过本地 HTTP gateway 跑自动化 eval runner。

## 调研来源

问题候选主要参考:

- [Epic docs: Replicate Actor Properties](https://dev.epicgames.com/documentation/unreal-engine/replicate-actor-properties-in-unreal-engine)
- [Epic docs: Unreal Engine Modules](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-modules)
- [Epic docs: Enhanced Input](https://dev.epicgames.com/documentation/unreal-engine/enhanced-input-in-unreal-engine)
- [Epic docs: Object Handling](https://dev.epicgames.com/documentation/unreal-engine/unreal-object-handling-in-unreal-engine)
- [Epic docs: Actor Lifecycle](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-actor-lifecycle)
- [Epic docs: Gameplay Timers](https://dev.epicgames.com/documentation/unreal-engine/gameplay-timers-in-unreal-engine)
- [Epic docs: Asynchronous Asset Loading](https://dev.epicgames.com/documentation/unreal-engine/asynchronous-asset-loading-in-unreal-engine)
- [Epic docs: Gameplay Ability System](https://dev.epicgames.com/documentation/unreal-engine/understanding-the-unreal-engine-gameplay-ability-system)
- [Epic docs: Actor Ticking](https://dev.epicgames.com/documentation/unreal-engine/actor-ticking-in-unreal-engine)
- [Epic docs: Components](https://dev.epicgames.com/documentation/unreal-engine/components-in-unreal-engine)

社区文章和论坛用于识别高频问题类型；最终答案以 UE5.7 本地源码为准。

## 判定标准

- `PASS`: MCP 返回了能直接支撑答案的关键文件和源码片段。
- `PARTIAL`: MCP 返回了相关模块或部分证据，但缺少关键实现文件、关键宏定义或关键运行时路径。
- `FAIL`: MCP 返回的主要上下文偏离问题，无法支撑正确答案。

## 30 个问题核验结果

| ID | 问题 | UE5.7 源码核验后的正确答案摘要 | 关键源码证据 | MCP 结果 | 备注 |
|---|---|---|---|---|---|
| 001 | 原始 `UObject*` 为什么可能被 GC 回收？ | 强引用应放在反射字段里，优先使用 `UPROPERTY`/`TObjectPtr`；非拥有关系使用 `TWeakObjectPtr` 并检查有效性。 | `ObjectPtr.h:29`, `WeakObjectPtrTemplates.h:21` | PASS | 返回了对象指针与弱指针相关证据。 |
| 002 | 构造函数里何时用 `CreateDefaultSubobject` 而不是 `NewObject`？ | 默认子对象在构造函数用 `CreateDefaultSubobject`；运行时对象用 `NewObject`，组件还需注册。 | `UObjectGlobals.h:1363`, `Actor.h:231` | PASS | 能定位到默认子对象和 Actor 构造相关上下文。 |
| 003 | 修改默认子对象 `FName` 为什么会破坏蓝图组件数据？ | 默认子对象名称是序列化和查重身份的一部分，应保持稳定且唯一。 | `UObjectGlobals.cpp:4864`, `UObjectGlobals.cpp:5980` | PARTIAL | 返回了名称和对象相关上下文，但缺少重复默认子对象实现点。 |
| 004 | 为什么 `.generated.h` 必须是最后一个 include？ | UHT 解析头文件时显式检查 `.generated.h` 位置，后续 include 会报错。 | `UhtHeaderFileParser.cs:885`, `UhtHeaderFileParser.cs:906` | FAIL | MCP 偏向宏和生成代码文件，未命中 UHT 解析器。 |
| 005 | `UCLASS`/`USTRUCT`/`UPROPERTY`/`UFUNCTION` 什么时候需要？ | 需要反射、GC 可见性、蓝图、序列化、复制或动态委托绑定时使用对应宏。 | `ObjectMacros.h:744`, `ObjectMacros.h:758` | PASS | 返回了宏定义和 UHT 相关证据。 |
| 006 | `Build.cs` 的 Public/Private dependency 怎么选？ | 公开头文件暴露的模块应放 `PublicDependencyModuleNames`；只在私有实现用到的模块放 `PrivateDependencyModuleNames`。 | `ModuleRules.cs:1189`, `ModuleRules.cs:1200` | PARTIAL | 命中若干模块规则示例，但未命中 `ModuleRules.cs` 字段定义。 |
| 007 | `WITH_EDITOR` 和构建配置宏应该怎么用？ | 编辑器专用代码用 `WITH_EDITOR`/`WITH_EDITORONLY_DATA`；Debug/Development/Shipping 宏来自构建配置头。 | `Build.h:66`, `Build.h:67` | FAIL | MCP 返回通用 Actor/World 上下文，缺少构建配置定义。 |
| 008 | 动态委托 `AddDynamic` 为什么要求 `UFUNCTION`？ | 动态委托通过反射脚本委托绑定，目标函数需要反射可见。 | `DelegateSignatureImpl.inl:1218`, `ScriptDelegates.h:180` | PARTIAL | 命中委托宏相关文件，但未命中动态脚本委托关键实现。 |
| 009 | `Tick` 不执行通常缺什么？ | Actor 需要启用 `PrimaryActorTick.bCanEverTick`；组件需要启用并注册，且可受 Tick enabled 状态影响。 | `Actor.h:247`, `ActorComponent.h:956` | PASS | 返回 Actor/Component tick 证据。 |
| 010 | 构造函数、`PostInitializeComponents`、`BeginPlay` 分别适合做什么？ | 构造函数建默认对象，初始化阶段处理组件和实例状态，`BeginPlay` 处理运行时开始逻辑。 | `Actor.h:722`, `Actor.h:793` | PASS | 能返回生命周期相关声明。 |
| 011 | `NewObject` 创建组件后为什么不可见或不工作？ | 运行时创建组件后通常要设置 Outer/Owner、Attach，并调用 `RegisterComponent`。 | `ActorComponent.h:1036`, `ActorComponent.h:1305` | PASS | 返回组件注册相关证据。 |
| 012 | `SetupAttachment` 和 `AttachToComponent` 怎么选？ | 构造期设置默认层级用 `SetupAttachment`；运行时附加用 `AttachToComponent`。 | `SceneComponent.h:724`, `SceneComponent.h:741` | PASS | 返回两个 API 的相关片段。 |
| 013 | `SpawnActor` 失败和碰撞处理怎么排查？ | 检查 class/world/transform/碰撞策略，可通过 `FActorSpawnParameters` 配置 collision handling。 | `World.h:452`, `Actor.h:3055` | PASS | 返回 spawn 参数和 Actor 生成上下文。 |
| 014 | `SpawnActorDeferred` 后忘了 `FinishSpawningActor` 会怎样？ | Deferred spawn 需要完成构造流程，否则 Actor 初始化链不完整。 | `World.h:3732`, `World.h:3749`, `GameplayStatics.h:71` | PARTIAL | 返回了相关任务和 Actor 片段，但缺少 World deferred API 关键行。 |
| 015 | Timer 如何安全清理？ | 保存 `FTimerHandle`，不需要时调用 `ClearTimer`，或清理对象关联 timer。 | `TimerManager.h:276`, `TimerManager.h:286` | PASS | 返回 `FTimerManager` 清理 API。 |
| 016 | 对象销毁后 timer 回调为什么还可能引发问题？ | timer 绑定对象和回调生命周期需要显式管理，销毁或结束播放时清理句柄。 | `TimerManager.cpp:701`, `TimerManager.h:276` | PASS | 返回对象绑定和 timer 管理上下文。 |
| 017 | LineTrace 打不到目标常见原因是什么？ | 检查 trace channel、query params、ignored actors、complex/simple collision 和碰撞响应。 | `WorldCollision.cpp:127`, `CollisionQueryParams.h:51`, `CollisionQueryParams.h:243` | PARTIAL | 返回查询参数和 World trace 声明，但缺少核心实现文件。 |
| 018 | 属性复制为什么要 `DOREPLIFETIME` 或注册复制描述？ | 复制属性必须进入类复制布局，旧路径通过 `GetLifetimeReplicatedProps` 和 `DOREPLIFETIME` 注册。 | `UnrealNetwork.h:259`, `PropertyReplicationFragment.cpp:97` | PARTIAL | 返回 Actor/复制相关上下文，但缺少 `UnrealNetwork.h` 宏定义。 |
| 019 | `OnRep` 为什么服务端不自动调用？ | RepNotify 面向客户端接收复制更新；服务端改值后需要自行调用等价逻辑。 | `PropertyReplicationFragment.cpp:101`, `Actor.cpp:5470` | PASS | 返回 RepNotify 和复制路径证据。 |
| 020 | Client RPC 为什么提示没有 owning connection？ | Client RPC 只能发送给拥有该 Actor 的连接；Actor owner / net connection 必须正确。 | `NetDriver.cpp:2929`, `Actor.cpp:5500` | PASS | 返回 owning connection 和 RPC 路径证据。 |
| 021 | 客户端调用 NetMulticast 为什么不能广播给所有人？ | NetMulticast 从服务端调用才会广播；客户端调用只在本地执行或不具备服务端 fanout。 | `NetDriver.cpp:3135`, `Actor.cpp:5500` | PARTIAL | 返回 RPC/Actor 上下文，但缺少 NetDriver 广播路径细节。 |
| 022 | Reliable RPC 是否可以滥用？ | Reliable 会进入可靠队列，过量或高频使用可能阻塞连接，应只用于必须送达的事件。 | `NetDriver.cpp:3228`, `NetDriver.cpp:7966`, `NetDriver.cpp:8065` | PARTIAL | 返回网络模拟和通用 RPC 上下文，缺少连接队列实现证据。 |
| 023 | `bReplicateMovement` 解决什么、不解决什么？ | 它复制 Actor 根运动状态，但不替代自定义状态复制或预测系统。 | `Actor.h:556`, `ActorReplication.cpp:179` | PASS | 返回 movement replication 相关证据。 |
| 024 | CharacterMovement 为什么网络表现不同于普通 Pawn 移动？ | `CharacterMovementComponent` 内置网络预测、压缩移动和修正路径。 | `CharacterMovementComponent.h:20`, `CharacterMovementComponent.cpp:51`, `CharacterMovementComponent.cpp:53` | PASS | 返回 CharacterMovement 网络预测证据。 |
| 025 | `FFastArraySerializer` 为什么要标记 item dirty？ | Fast array 复制依赖 item/array dirty 标记来收集增量变化。 | `FastArraySerializer.h:122`, `FastArraySerializer.h:124`, `FastArraySerializer.h:441` | PASS | 返回 fast array dirty 相关证据。 |
| 026 | GAS 的 ASC 初始化应该放哪里？ | ASC owner/avatar 初始化应在合适生命周期完成，服务端和客户端路径都要覆盖。 | `AbilitySystemComponent.h:258`, `AbilitySystemComponent.cpp:1913` | PASS | 返回 ASC 初始化相关证据。 |
| 027 | GAS AttributeSet 复制和 `OnRep` 怎么写？ | AttributeSet 属性按 UE 复制注册，并用 RepNotify / attribute helper 保持回调语义。 | `AttributeSet.h:399`, `AttributeSet.h:403`, `AttributeSet.h:420` | PASS | 返回 AttributeSet 复制相关证据。 |
| 028 | Enhanced Input 的 Mapping Context 应该加到哪里？ | 通常从本地玩家的 Enhanced Input subsystem 调用 `AddMappingContext`。 | `EnhancedInputSubsystems.h:37`, `EnhancedInputSubsystems.h:64` | FAIL | MCP 返回旧输入和手柄上下文，未命中 EnhancedInput 插件。 |
| 029 | Enhanced Input 的 `BindAction` 应该用哪个 component？ | 使用 `UEnhancedInputComponent` 绑定 `UInputAction`，不是旧 `UInputComponent` 语义。 | `EnhancedInputComponent.h:373`, `EnhancedInputComponent.h:467` | FAIL | MCP 返回旧输入组件，未命中增强输入绑定 API。 |
| 030 | 软引用、`StreamableManager` 和 `ConstructorHelpers` 怎么选？ | 软引用可异步加载；`ConstructorHelpers` 适合构造期硬查找；运行时加载用 streamable/asset manager 路径。 | `SoftObjectPtr.h:170`, `StreamableManager.h:730`, `ConstructorHelpers.h:82` | PARTIAL | 返回软对象指针，但缺少 streamable 和 constructor helper 关键文件。 |

## Codalith 改进建议

1. 补强 UHT/UBT/C# 工具链源码检索。`generated.h`、模块依赖和构建宏问题需要命中 `Programs/Shared/EpicGames.UHT`、`Programs/UnrealBuildTool`、`Runtime/Core/Public/Misc/Build.h`。
2. 提高插件源码召回权重。Enhanced Input 的两个问题都落在 `Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public`，但 MCP 返回了旧输入系统。
3. 对网络复制问题增加运行时实现路径权重。`Actor.h` 声明有帮助，但 `NetDriver.cpp`、`ActorReplication.cpp`、`PropertyReplicationFragment.cpp` 更能支撑正确答案。
4. 对资源加载问题同时召回 `SoftObjectPtr`、`StreamableManager` 和 `ConstructorHelpers`。只命中软引用会让答案缺少构造期与异步加载边界。
5. 将 `eval/datasets/ue57_common_issues_30.jsonl` 纳入后续 eval runner，按 `expected_files` 和 `verified_sources` 做自动化判分。

## 本轮环境限制

- Docker Desktop Linux engine pipe 不可用，`docker compose ps --all` 失败。
- `http://127.0.0.1:8765/mcp` 连接被拒绝，因此未运行 HTTP MCP gateway 自动测评。
- 活跃 MCP 连接可用，`codalith_index_status(version="5.7.4")` 显示索引存在且可查询；本报告的 30 个 MCP 结果来自逐题调用 `codalith_context` 的判读。
