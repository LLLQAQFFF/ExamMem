# 阶段 03：通过 Feature Flag 裁剪非核心能力

> 阶段性质：最小运行面与可逆裁剪。  
> 前置条件：[阶段 02](./02_ExamMem架构与评测协议.md)验收通过。  
> 退出条件：ExamMem 最小运行配置可用，非核心模块关闭但源码、配置和恢复路径完整保留。  
> 阶段门禁：裁剪矩阵和恢复测试未通过，不得进入[阶段 04](./04_知识点Taxonomy_slot_key与四维Scope.md)。

## 1. 阶段目标

DeepTutor 功能面较大，一人开发不能同时维护全部能力。本阶段通过配置和注册阶段的 Feature Flag 缩小运行面，让后续调试只关注刷题、判题、知识库、编排和 Memory，同时保留上游代码用于升级、原生 Baseline 和对照实验。

原则：

- **关闭，不删除**；
- **在注册边界阻止能力进入运行时**，而不是只隐藏 UI；
- **默认配置明确**，不能依赖某位开发者机器上的隐式状态；
- **开关可观察**，Trace 必须记录实际启用的能力和 Memory 模式；
- **恢复可测试**，关闭后重新开启仍能通过原生烟雾测试。

## 2. 能力范围

### 2.1 MVP 必须保留

- Chat Orchestrator 与共享上下文；
- Tool Registry 和 Capability Registry；
- Quiz/Question Bank 或锁定版本中对应的题目能力；
- Knowledge Base/RAG 的最小检索能力；
- Native Memory，用作 `native` Baseline；
- CLI 和必要的 FastAPI/WebSocket 接口；
- Provider、结构化输出、日志、Trace 和用户隔离；
- 后续注册 `exam_practice` Capability 所需的扩展点。

### 2.2 MVP 默认关闭

- Deep Research；
- Book/Co-Writer；
- Visualization、Math Animator；
- Partner、Subagent 和外部消息渠道；
- 非必要的多 RAG Engine；
- 音频、视频、图像生成；
- 与本项目无关的自动化任务。

具体名称必须以阶段 01 锁定版本的实际注册项回填，不能根据最新版文档猜测。

## 3. 配置设计

建议将 ExamMem 配置纳入 DeepTutor 现有配置体系，不额外创建第二套无管理入口的 `.env`。

```yaml
exam_mem:
  enabled: true
  subject: postgraduate_math_1
  memory_backend: lifecycle
  capabilities:
    exam_practice: true
    native_chat: true
    knowledge_base: true
    native_quiz: true
    deep_research: false
    book: false
    cowriter: false
    visualize: false
    partners: false
```

`memory_backend` 是枚举而不是多个互相冲突的布尔值：

```text
none | native | append_only | vector | lifecycle
```

启动时必须做以下校验：

- 未知模式直接失败并给出合法值；
- `exam_mem.enabled=false` 时不得注册 ExamMem Capability/Tools；
- `lifecycle` 模式缺少数据库配置时快速失败，不降级成另一个 Baseline；
- `native` 模式不得写 ExamMem 表；
- 评测运行必须把最终解析后的配置写入结果快照。

## 4. 技术路线

### 4.1 在注册阶段裁剪

推荐在 Tool/Capability Registry 的装配层读取 Flag：

```python
def register_capabilities(registry, settings):
    registry.register(ChatCapability(...))

    if settings.exam_mem.capabilities.native_quiz:
        registry.register(NativeQuizCapability(...))

    if settings.exam_mem.enabled:
        registry.register(ExamPracticeCapability(...))

    if settings.exam_mem.capabilities.deep_research:
        registry.register(DeepResearchCapability(...))
```

不要采用以下伪裁剪：

- 仅从菜单隐藏，后端仍注册和初始化；
- 请求进入后才返回“功能关闭”，但模型仍能看到工具描述；
- 删除源码、依赖和数据库结构；
- 注释掉大量注册代码；
- 用不同分支维护不同功能集合。

### 4.2 UI、API 和后台任务同步

同一 Flag 要约束四个层面：

1. UI 不展示入口；
2. API/Capability 不注册或返回明确的 `feature_disabled`；
3. LLM Tool Schema 中不存在被关闭工具；
4. 后台 Scheduler 不启动相关任务。

如果底座无法在注册期移除 API 路由，允许保留路由并返回稳定的 404/403/409，但必须保证不初始化重型依赖、不调用模型、不写数据。

### 4.3 Memory 模式适配

使用工厂选择唯一 Backend：

```python
def build_memory_backend(mode, dependencies) -> MemoryBackend:
    match mode:
        case "none":
            return NoMemoryBackend()
        case "native":
            return NativeMemoryAdapter(dependencies.native_memory)
        case "append_only":
            return AppendOnlyBackend(dependencies.repositories)
        case "vector":
            return VectorMemoryBackend(dependencies.repositories)
        case "lifecycle":
            return LifecycleMemoryBackend(dependencies.lifecycle_service)
        case _:
            raise ConfigurationError(...)
```

调用方不能出现五份 `if mode == ...` 的业务分支。模式差异封装在 Backend 内，但所有模式输出统一 Trace。

### 4.4 配置优先级与审计

沿用锁定版本的配置优先级，不另造规则。文档必须回填最终顺序，例如“CLI 参数 > 用户设置 > 系统默认”。每次启动记录：

- 配置版本和非敏感摘要；
- 启用的 Capability/Tool 名单；
- Memory Backend；
- 被关闭的后台任务；
- 配置错误和回退行为。

## 5. 测试矩阵

| 场景 | 预期行为 |
| --- | --- |
| ExamMem 总开关关闭 | 不注册 ExamMem Capability/Tools |
| Research 关闭 | UI/Tool Schema/后台任务均不可用 |
| Research 重新开启 | 原生烟雾测试恢复通过 |
| `none` | 不产生 Native 或 Learning Memory 写入 |
| `native` | 只产生原生副作用 |
| `append_only` | 只追加，不归档、不合并 |
| `vector` | 可向量检索，不执行生命周期转移 |
| `lifecycle` | 走完整 Learning Memory 更新 |
| 非法 Backend | 启动失败，错误可定位 |
| 缺数据库配置 | 仅依赖数据库的模式失败，不静默降级 |

至少增加：配置解析单测、Registry 组装测试、Tool Schema 快照测试、禁用模块零副作用测试、重启后配置保持测试和恢复烟雾测试。

## 6. 引导式编程任务

### 任务 A：识别真正的注册边界

先阅读 Registry 和配置代码，预测关闭一个 Capability 后哪些对象仍会初始化。写测试观察 Registry、Tool Schema 和后台任务，再实施最小改动。

### 任务 B：实现一个 Backend 工厂

先写参数化测试覆盖五种合法模式和一个非法模式，再实现工厂。解释为什么模式应是枚举，而不是五个布尔开关。

### 任务 C：证明“零副作用”

为关闭的模块安装调用计数器或 Fake Provider，验证请求不会触发模型、网络和数据库写入。只验证 UI 不显示不算完成。

AI Review 重点：是否触碰无关代码、是否产生重复配置系统、是否把关闭误写成删除、是否存在静默降级。

## 7. 运行命令模板

具体配置路径和命令以阶段 01 锁定版本为准：

```powershell
# 查看解析后的非敏感配置与注册项
deeptutor config show
deeptutor plugin list

# 分模式运行 Registry/配置测试
pytest -m "feature_flag or registry" -q
pytest -m "backend_mode" -q

# 恢复一个原生能力后运行烟雾测试
pytest -m "native_smoke" -q
```

运行日志应同时保存最终配置摘要、Capability/Tool 列表和外部调用计数。

## 8. 交付物

- 锁定版本的完整能力盘点表；
- 默认 ExamMem 配置和配置 Schema；
- Capability/Tool 注册期 Feature Flag；
- 五种 Memory Backend 工厂或接口骨架；
- UI/API/Tool/Scheduler 一致性说明；
- 测试矩阵结果和恢复 Runbook；
- 已知限制与后续重新启用方法。

## 9. 验收标准

| 验收项 | 目标值 | 验证方法 |
| --- | --- | --- |
| 非核心模块源码保留 | 100% | Git diff 与上游对比 |
| 关闭模块注册数 | 0 | Registry 和 Tool Schema 断言 |
| 关闭模块外部调用/写入 | 0 | Spy/Fake 与数据库快照 |
| 五种 Backend 配置 | 100% 可解析 | 参数化测试 |
| 非法配置 | 100% 快速失败 | 负例测试 |
| 恢复能力 | 被关闭模块烟雾测试通过 | 打开 Flag 后复测 |
| 核心链路 | CLI/API 基线不回归 | 与阶段 01 对比 |

回滚方式：恢复上一版默认配置或关闭 `exam_mem.enabled`，不需要恢复已删除代码，因为本阶段禁止删除。

## 10. 提交清单与 Git 门禁

- [ ] 代码：仅包含配置、注册和必要的适配接口；
- [ ] 测试结果：配置、注册、零副作用、恢复和核心烟雾测试；
- [ ] 运行命令：不同模式和开关组合的运行方式；
- [ ] 交付物：第 8 节全部存在；
- [ ] 已知问题：无法动态切换的开关、需要重启的配置等；
- [ ] 独立 Git Commit：不包含 taxonomy 或数据库实现。

建议 Commit Message：

```text
feat(config): gate non-core capabilities without removing upstream code
```

## 11. 面试复盘卡

你应能回答：

1. 为什么隐藏 UI 不等于关闭能力？
2. Feature Flag 应该放在请求层还是注册层？
3. 如何证明关闭模块没有 LLM、网络或数据库副作用？
4. 为什么保留 Native Memory 能提高实验可信度？
5. 为什么不能在缺数据库时把 `lifecycle` 静默降级为 `native`？

推荐表述：

> 我没有直接删除 DeepTutor 的非核心模块，而是在 Capability、Tool、UI 和 Scheduler 的装配边界使用统一 Feature Flag。这样既缩小了运行面，也保留了上游升级、故障回退和原生 Memory Baseline 的能力。
