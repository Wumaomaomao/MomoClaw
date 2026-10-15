# MomoClaw 上下文管理与压缩优化方案

- 版本：v1.0
- 日期：2026-10-15
- 状态：待实施设计；本文不代表所述功能已开发完成
- 适用范围：本地 CLI 与 AgentSession；兼顾后续 IM
- 核心约束：同一配置周期内，主任务请求的 system prompt 保持逐字节一致

## 1. 目标与当前代码事实

目标是在长会话和多轮工具调用中控制输入体积，持续保留任务目标、用户约束、关键证据和待办，并支持压缩后的持久恢复与回溯。

已核对的代码：

| 位置 | 当前行为 | 优化切入点 |
| --- | --- | --- |
| momoclaw/coding_agent/factory.py | 创建工具、基础 system prompt 与 AgentSession | 注入上下文策略及预算配置 |
| momoclaw/coding_agent/session.py | 每轮临时把技能与记忆拼入 system prompt，结束后恢复；任务返回后追加消息日志 | 固定主 system prompt，管理动态上下文与压缩状态 |
| momoclaw/agent/agent.py | 持有完整 messages；AgentOptions 已支持 convert_to_llm | 复用现有请求投影接口 |
| momoclaw/agent/loop.py | 每次模型调用前执行 convert_to_llm，包括工具循环与 continue_run | 每次请求前计量及压缩，不只检查新用户输入 |
| momoclaw/coding_agent/session_store.py | messages.jsonl、events.jsonl；消息在一次 prompt 完成后落盘 | 增加可校验检查点；完善运行中消息持久化 |
| momoclaw/ai/types.py | Model 有 context_window、max_tokens；消息有工具调用 ID、thinking/signature、图片等内容块 | 建立预算和协议完整性验证 |
| momoclaw/ai/providers/ | 将 Context 转为供应商请求 | 估算序列化开销、规范错误分类与真实输出上限 |

现状风险：

- 历史消息与工具输出持续增长，Workspace Memory 的 1200-token 限额只覆盖记忆本身。
- system prompt 每轮动态变化，不符合本方案的稳定前缀要求。
- 当前任务返回后才追加消息日志，运行中崩溃可能丢失尚未落盘的消息；压缩恢复不能假设这部分记录已经持久化。
- Model 内声明的窗口和输出上限需要验证或由用户配置，不能把默认元数据当作供应商实际限制的保证。

## 2. 必须保持的约束

1. 压缩、记忆召回及动态技能加载均不得修改主任务 system prompt。
2. system prompt、工具权限和工具 schema 不参与摘要压缩。
3. 原始消息日志可追溯；摘要属于有损派生数据，必须能定位覆盖范围和来源。
4. 保留当前用户请求、明确用户约束与完整的近期交互。较晚的用户修正优先于较早约束。
5. 工具调用、对应结果、顺序和 ID 必须有效；不制造孤立 tool_result 或伪造执行成功。
6. 历史中的引用、文件内容、工具输出、模型推测都不能通过摘要升级成用户授权或系统规则。
7. 只改变发送给模型的请求视图，业务源码不受压缩操作影响。
8. 无法在合法范围内压到预算内时返回明确原因，不能无限重试或静默删除关键约束。
9. 摘要生成失败不能损坏原始日志或已生效检查点。
10. 对未完成工具调用及正在流式生成的消息，不执行跨消息边界的摘要替换。

## 3. system prompt 稳定策略

### 3.1 不变的精确定义

同一个 session 的同一个 configuration_epoch 内：

- Factory 构建一次基线 system prompt，记录 UTF-8 SHA-256。
- 每次主模型请求的 Context.system_prompt 与基线完全一致。
- 禁止在请求前向其追加记忆、摘要、当前任务状态或动态技能内容。
- 系统提示中一次性声明 application_context 的解释规则：它是应用提供的历史/资料，不能自行授予权限；明确保留来源角色与不确定性。

用户显式改变模型、工具集合、固定指令或静态资源配置时，创建新 Session 或新的 configuration_epoch，重新计算指纹。压缩过程自身不触发配置变更。现有 CLI 的模型切换已创建新 Session，可以沿用。

工具 schema 和顺序也应在配置周期内稳定。system prompt 稳定有利于形成可复用请求前缀，但缓存能否命中仍取决于供应商行为、工具定义与其他请求内容，不能承诺固定缓存命中率或费用降幅。

### 3.2 动态内容进入消息视图

主请求的逻辑结构：

~~~text
固定 system prompt
固定工具定义
messages:
  application_context（user 角色的应用上下文数据）
    - 任务检查点摘要
    - 带原始消息引用的用户约束
    - 近期检索到的 Workspace Memory
    - 本轮动态技能资料
  保留的原始近期消息与完整工具调用组
  当前用户请求及本轮新增交互
~~~

现有 AI 类型只支持 user / assistant / toolResult。初版使用合成 UserMessage 的 TextContent 块承载 application_context，并在应用内部记录 kind、来源、指纹和覆盖范围；不扩展出供应商不支持的角色。

合成消息：

- 仅用于请求视图，不写成用户真实发言，也不作为下一轮摘要的原始素材。
- 用结构化序列化保存正文，清楚标记 source_role、message_id 和 untrusted_data。
- 当前用户原文仍完整保留，并按原始时间顺序位于近期交互中。
- 不在未完成的工具调用和结果之间插入应用上下文。
- 供应商需要合并相邻 user 消息时，使用有来源标签的内容块合并，保留当前问题与历史资料的边界。

### 3.3 现有技能与记忆的迁移

- 删除 session.py 在每轮暂时改写 agent.state.system_prompt 的路径。
- Workspace Memory 的检索结果改由 ContextBuilder 放入 application_context。
- 当前逐轮选择的技能正文进入动态上下文；显式选择的固定技能也可在会话创建时冻结为静态资源，更新时需显式重开配置周期。
- 技能内容不能扩大 read/write/bash 权限；权限仍由应用配置和工具执行层约束。
- 摘要模型使用独立的请求和专用提示词；它可以有自己的 system prompt，但不能替换主任务的 system prompt。

## 4. 总体架构与依赖

~~~mermaid
flowchart TD
    A[AgentSession 收到任务] --> B[准备任务状态和记忆检索]
    B --> C[Agent 工具循环]
    C --> D[convert_to_llm 每次请求前调用]
    D --> E[ContextManager 构造请求视图]
    E --> F[预算估算和协议校验]
    F --> G{是否超过触发阈值}
    G -- 否 --> H[固定 system prompt 加请求视图]
    G -- 是 --> I[缩减旧工具输出]
    I --> J[按完整交互组生成检查点摘要]
    J --> K[校验 来源 约束 工具组和预算]
    K --> H
    H --> L[Provider 请求]
    L --> C
~~~

建议应用层结构：

~~~text
momoclaw/coding_agent/context/
  models.py          # 配置、预算、摘要、来源与请求视图
  budgeting.py       # tokenizer/保守估算、输出预留、校准
  grouping.py        # 交互分组与 tool_call/result 配对
  pruning.py         # 工具输出的请求视图缩减
  summarizer.py      # 可选模型摘要及离线结构化摘录
  validation.py      # 摘要来源、用户约束、协议和预算验证
  checkpoints.py     # 持久检查点及恢复
  builder.py         # 构造合成上下文与近期消息视图
  manager.py         # 生命周期门面
~~~

接入方式：

- AgentSession 创建 ContextManager，并把其 async convert_to_llm 适配函数传入已有 AgentOptions。
- 适配函数接收完整消息，返回新的请求消息列表，不原地修改 Agent.state.messages 或原消息对象。
- Session 提供当前任务、记忆结果、工具 schema 和模型预算信息。
- 核心 Agent 只依赖通用接口，不反向 import coding_agent.context。
- 若以后现有接口缺少必要信息，可扩展通用 RequestContext hook；初版优先使用已存在接口。

## 5. 预算与触发策略

### 5.1 预算计算

~~~text
W = 已配置并验证的模型上下文窗口
O = 本次请求实际设置的最大输出预留
M = 序列化差异、估算误差与 reasoning 等模型特性的安全余量
B = W - O - M                 # 最大可用输入预算

T = 固定 system prompt + 工具 schema + 用户约束原文
  + 检查点摘要 + 近期消息 + 当前请求/工具结果
  + 动态技能 + Workspace Memory + 协议开销
~~~

必须同时把输出上限传给真实 Provider 请求。当前 loop.py 创建 SimpleStreamOptions 时未明确传入预算策略的 max_tokens，需要补齐通用参数传递；不能仅在估算中预留一个实际请求没有采用的值。

初始建议参数（待回归评估后调整）：

| 参数 | 建议初值 | 作用 |
| --- | --- | --- |
| high_watermark | 0.80 × B | 超过时触发主动压缩 |
| target_watermark | 0.60 × B | 压缩后尽量降到该值，避免反复触发 |
| hard_limit | B | 请求发送前的硬性检查 |
| recent_user_turns | 4 | 尽量保留最近 4 次完整用户任务交互 |
| summary_max_tokens | 2000 | 摘要上限，随小窗口按比例下调 |
| memory_max_tokens | 1200 | 沿用记忆默认上限，并纳入总预算 |
| max_compaction_attempts | 2 | 防止无收益的重复压缩 |
| context_overflow_retry | 1 | 明确识别窗口超限后，最多一次缩小视图重试 |

安全余量按模型配置选择，例如默认保留窗口的 5%，并提供绝对下限；小窗口必须校验 W、O、M 的可行性，不能让固定默认值消耗全部预算。保留近期交互是目标约束，无法容纳时优先缩减其大输出；仍不够时报告受保护部分超限。

### 5.2 计量方式

- 有可靠 tokenizer 时采用与模型匹配的计量实现；具体库另行评估。
- 无 tokenizer 时按 UTF-8 字节数、消息/工具 schema 的序列化开销做保守估算，并记录 estimated=true。
- 使用每次响应返回的真实 input usage 校准估算比例，分别记录缓存读取/写入量，避免把缓存输入误当成不存在的输入。
- 图片和 reasoning 内容按供应商兼容策略计量；无法可靠估算时提高安全余量，不能按零 token 处理。
- 估算器不能保证精确等同供应商 tokenizer，超限响应仍需要受控降级路径。

### 5.3 检查时机

每次调用模型前都检查，包括：

- 新用户任务；
- 一次或多次工具执行后的下一次模型请求；
- continue_run；
- 模型切换后的第一轮；
- 手动 /compact。

不在流式 assistant 内容尚未结束或工具仍在执行时压缩其未完成交互。

## 6. 压缩算法

### 6.1 固定内容与优先级

从高到低保留：

1. 固定系统提示和工具定义；
2. 当前用户请求、仍生效的明确用户约束、未完成工具调用链；
3. 最近完整交互、关键错误和必要证据；
4. 结构化任务摘要；
5. 当前任务相关的 Wiki/技能资料；
6. 较早的重复输出与已结束探索细节。

用户授权和约束必须能定位到真实用户消息。摘要中的“已经获准部署”没有原始用户授权引用时，不能视为有效授权。

### 6.2 阶段一：缩减请求视图中的大输出

优先处理较早且已完成的 read/bash 等文本输出：

- 文件输出：保留路径、读取区间、内容哈希、与任务有关的片段和再次读取方式。
- 命令输出：保留命令、已知 exit_code、错误摘要、失败断言、尾部关键日志。
- 无法可靠选择关键片段时，使用确定性的头尾截取，明确写出“已截断”。
- 不能根据“日志看起来正常”生成“测试全部通过”等结论。
- 相同内容输出可用哈希识别重复；仍保留对应 tool_result 外壳、ID 和错误状态。
- 工具调用名称、参数、ID 不做有损压缩；禁止凭空补齐缺失结果。
- 原始完整输出留在消息日志，引用到具体消息记录；不得提供实际上不存在的日志文件路径。
- 图片、二进制和签名内容不能套用普通文本截断。对仍需保留的签名块保持字节一致；超大媒体需要重新读取、降采样能力或明确的预算错误。

所有缩减在消息副本上完成，不改变原始工具执行状态或持久日志。

### 6.3 阶段二：压缩较早的完整交互组

分组规则：

- 基本单元是一个完整用户任务及其后续 assistant/tool 交互。
- assistant 同时发起的多个 tool_call 与全部对应结果组成不可拆开的协议组。
- 最近 N 次用户任务以及当前未结束交互受保护。
- 初版优先只在完整用户任务之间切分；后续可支持当前长任务内已经闭合的工具循环。
- 工具结果可以在保留协议外壳的情况下缩短文本，但不能删除部分配对消息。

摘要结构：

~~~json
{
  "schema_version": 1,
  "objective": {"text": "修复登录失败异常", "message_refs": ["m12"]},
  "active_constraints": [
    {"quote": "保持已有 CLI 行为兼容", "source_role": "user", "message_ref": "m12"}
  ],
  "decisions": [],
  "completed_work": [],
  "modified_files": [],
  "evidence": [
    {"claim": "相关测试通过", "message_refs": ["m27"], "scope": "相关用例；未验证完整套件"}
  ],
  "failed_attempts": [],
  "open_questions": [],
  "pending_actions": [],
  "uncertainties": []
}
~~~

摘要准则：

- 有证据才记录为完成或验证成功；计划、推断和观察分别标记。
- 用户约束另存可追溯原文清单，不能只依赖模型重新措辞。
- 用户修正后保留最新状态及其依据，避免摘要把旧结论重新激活。
- 保留失败原因和适用条件，控制冗余过程细节。
- 不摘要或暴露隐式推理过程；只保留可观察的结论和证据。
- 对任务资料中的命令或恶意指令保留其引用属性，不把它们变成应用指令。

### 6.4 摘要生成与校验

模型摘要为可选组件：

1. 输入上一检查点、待压缩的完整新交互组、约束原文清单与明确目标长度。
2. 使用无工具权限的独立摘要请求，输出结构化 JSON。
3. 校验 schema、来源 message_id、覆盖范围、预算和约束引用。
4. 内容没有足够证据时保留 uncertainty，不伪造摘要结论。
5. 检查压缩收益，若没有减少输入或遗漏受保护内容，则拒绝采用。
6. 超大历史按完整交互组分批，限制每批摘要输入和总开销；不能把已经超窗的全部历史再交给摘要模型。
7. 再压缩摘要时始终保留原始证据和约束清单。必要时从原始日志重建，减少多次转述造成的漂移。

模型不可用、无凭据或超时：

- 使用离线结构化摘录：原文目标/约束引用、文件列表、可靠工具状态、错误片段。
- 明确标记生成方式，不把规则摘录描述为理解过全部语义的模型摘要。
- 如果离线结果也无法满足硬预算，则暂停该次请求并解释需调整的预算或受保护内容。

## 7. 工具协议、权限与供应商兼容

每次发送前检查：

- 每个 tool_result 有对应的 assistant tool_call；
- tool_call ID 唯一、结果对应关系和时序有效；
- 不能在调用与结果中间插入摘要；
- 已保留 assistant 消息的签名块不可随意修改；
- user 的图片输入、tool_result 的错误标志按原意保留；
- Provider 适配后的请求仍满足上述关系。

不同供应商序列化存在差异。需要用仓库现有 OpenAI-compatible 和 Anthropic 转换层分别验证相邻角色、工具结果、文本块和 reasoning 的兼容性；不把一个供应商的通过结果外推到所有模型。

摘要和检索内容都不能改变工具白名单、写权限和危险操作确认状态。压缩过程中不执行 bash、写代码、重跑测试或其他业务工具。

## 8. 持久化、恢复与并发

### 8.1 数据布局

~~~text
.momoclaw/sessions/<session_id>/
  meta.json
  messages.jsonl
  events.jsonl
  context/
    manifest.json
    checkpoints/
      checkpoint-000001.json
      checkpoint-000002.json
~~~

manifest 指向当前已提交检查点。检查点建议字段：

- id、schema_version、session_id、configuration_epoch；
- summary、生成方式、摘要模型和策略版本；
- covered_until_sequence、source_prefix_sha256；
- kept_from_sequence、受保护用户约束引用；
- system_prompt_sha256、tools_schema_sha256、budget_config_sha256；
- before/after 的估算输入、真实 usage（可得时）、创建时间；
- parent_checkpoint_id，用于恢复和审计。

### 8.2 原始记录与提交顺序

为原始消息提供稳定 sequence/message_id。建议新追加记录采用以下封装，读取适配器同时支持旧的裸 Message 字典与新版封装：

~~~json
{
  "schema_version": 2,
  "message_id": "message-uuid",
  "sequence": 128,
  "run_id": "run-uuid",
  "message": {"role": "user", "content": "继续修复登录问题", "timestamp": 0}
}
~~~

旧 messages.jsonl 不重写；旧记录的定位使用原始行号及内容哈希，恢复时不能因跳过空行或坏行而让已有来源引用漂移。新增记录通过 session_store.py 的读取适配器与 serde.py 转成现有 Message 类型，Provider 不接收记录封装字段。新旧混合日志、重复 message_id 与截断末行均需有专门恢复规则。

最终建议把 finalized message 的持久化改为增量方式：

1. 只在完整 message_end 后追加，partial 流式对象不进入可提交覆盖范围。
2. 使用 session writer 锁和幂等 message_id，避免事件落盘与 prompt 返回重复追加。
3. 原消息成功持久化后推进 durable_sequence。
4. 摘要仅能声明覆盖 durable_sequence 以内的有效闭合范围。
5. 先原子写 checkpoint，再原子替换 manifest，旧检查点至少保留一个可回退版本。
6. 新请求视图构建成功且校验通过后才发布新活动检查点。

分阶段上线时，若暂时保留“任务返回后才落盘”的旧机制，运行中压缩结果必须标记为临时，只能在原消息落盘后提交；文档与状态命令必须明确其崩溃恢复边界。

### 8.3 恢复规则

- 启动时校验来源前缀哈希、覆盖范围和配置指纹。
- 检查点损坏、来源不一致或配置不兼容时，回退上一个有效检查点，必要时从原始消息重建。
- 单 Session 同时只允许一个 prompt/continue/compact 写入者；IM 后续沿用已有队列。
- 取消时只保留完成写入的消息与已提交检查点。
- 恢复上下文不等同于重放工具执行；未确认的副作用不得自动再次执行。
- /new 创建新上下文状态；/clear 清除活动视图及检查点引用，避免重启后把已清掉的上下文重新加载。普通历史清理策略另行配置。

## 9. 与 Workspace Memory 的协作

| 数据 | 生命周期 | 可信处理 |
| --- | --- | --- |
| 原始消息日志 | 当前 Session 的可追溯历史 | 保留角色与工具证据 |
| 压缩摘要 | 当前任务/Session 的工作状态 | 有损派生数据，不能自动授予权限 |
| Workspace Memory | 同工作区跨 Session 的项目知识 | 来源校验、审核与 stale 状态 |
| 固定 system prompt | 配置周期内的行为规则 | 不参与压缩或动态拼接 |

- 每轮记忆检索结果是可重建的临时内容，不反复存入摘要。
- 摘要中的事实不能直接升为 verified Wiki；后续提取仍需独立证据与审核。
- 预算紧张时先减少非关键检索条目和动态资料，再牺牲近期任务证据。
- 摘要引用源码时附采集时间/版本；后续回答精确代码问题仍需必要核验。
- 长期记忆已 stale 时，即使旧摘要曾引用它，也不能将其重新描述成当前已验证事实。

## 10. CLI、配置和可观测性

建议配置（新增字段，尚未实现）：

~~~json
{
  "context": {
    "enabled": false,
    "high_watermark": 0.8,
    "target_watermark": 0.6,
    "recent_user_turns": 4,
    "summary_max_tokens": 2000,
    "summarizer": "optional",
    "max_compaction_attempts": 2
  }
}
~~~

模型窗口、输出上限与安全余量放在模型预算配置中；修改时验证所有值及指纹。

建议命令：

- /context status：预算占用、估算/真实值、固定提示哈希、当前检查点、配置周期。
- /context on | off：切换自动压缩；off 后仍执行请求硬预算保护。
- /context explain：显示组成与上次压缩原因，不暴露原始敏感正文。
- /compact：在安全边界手动压缩。
- /compact preview：展示计划覆盖范围、保留范围与预计收益，不改变活动检查点。

初期显式启用；协议与质量回归通过后再决定默认策略。手动 /compact 可以在自动压缩关闭时使用。

事件建议：

- context_budget_checked；
- context_pruned；
- context_compaction_started/completed/failed；
- context_checkpoint_committed/recovered。

记录模型、配置版本、估算输入、真实输入、压缩前后比率、摘要额外 token/延迟、保留消息组数、记忆注入量、失败原因和检查点 ID。普通日志不写完整用户 Prompt、密钥或大段工具输出。

## 11. 异常与降级

| 情况 | 行为 |
| --- | --- |
| 摘要请求失败 | 保留上个检查点，尝试规则摘录；仍超限则返回清楚原因 |
| 摘要不合法、缺少来源或没有收益 | 拒绝提交，最多有限次数重试 |
| 固定提示、工具 schema、当前请求等受保护部分本身超窗 | 明确报告占用组成，要求调整配置/输入，不静默删约束 |
| 新工具输出使同一任务快速超窗 | 在下一次模型请求前处理已完成结果的文本副本，保留调用组 |
| 供应商报告窗口超限 | 仅对明确分类的 context-window 错误再缩减一次并重试该模型请求 |
| 网络/鉴权等其他错误 | 沿用对应错误处理，不能一概触发反复压缩 |
| 进程中断 | 使用有效原日志和已提交检查点恢复；不自动重跑有副作用工具 |
| 不完整工具组 | 延后切分或报协议错误，不拼造结果 |
| 摘要模型执行额度不足 | 规则缩减或预算错误，并记录额外开销 |
| 固定 system prompt 指纹意外变化 | 阻止使用旧检查点，记录配置漂移并重建配置状态 |

超限重试必须位于单次 Provider 请求边界。当前项目尚未有统一的 context-window 错误码，需要在 Provider 层添加错误分类；不重启整个 Agent 任务以免重复工具副作用。

## 12. 验证与效果评估

### 12.1 必须通过的行为验收

- 开关关闭时保持兼容；开启后每次主模型请求的 system prompt 哈希完全一致。
- 记忆命中、关键词技能变化、手动压缩和工具循环都不会改变主 system prompt。
- 新用户约束、较晚修正、未完成事项和失败原因在压缩后可追溯。
- 多工具调用组、错误结果、continue_run 均不会出现孤立结果或重复工具执行。
- 大工具输出能在同一任务的下一次请求前受到预算控制。
- 原始日志没有被请求视图裁剪覆盖；恢复不会重复追加同一消息。
- 损坏检查点、部分写入、并发操作、取消和重启有可解释恢复行为。
- 合成上下文不会重复进入原始历史或被反复摘要。
- 恶意 README、工具输出和旧模型回答中的“授权/指令”不升级为用户授权。
- 无凭据时可完成离线规则流程；摘要模型使用 Fake Provider 验证结构、失败及降级。
- OpenAI-compatible、Anthropic 请求转换分别验证协议完整性。

### 12.2 对照实验

冻结一组包含长工具日志、跨多任务、用户修正和代码变更的离线场景：

- A：现有全量上下文；
- B：只缩减工具输出；
- C：工具输出缩减 + 结构化摘要。

统一模型配置、任务和输入历史，比较：

- 实际主请求输入 token；
- 摘要额外成本与净总 token；
- 延迟、调用数、压缩次数及重复读取数；
- 约束保留、任务完成、工具协议错误率；
- system prompt/工具 schema 指纹稳定性；
- 冷启动和重启后的结果一致性。

初始目标：在可压缩的长日志样例中，请求视图 token 减少至少 50%；协议错误为 0，受保护用户约束引用保留率为 100%。这是待验证的验收目标，不能解释为已取得的生产收益或保证模型语义理解无损。短会话不应为达成压缩比例增加额外摘要调用。

## 13. 分阶段实施与具体改动范围

### Phase 0：固定请求边界

- 新建 context/models.py、builder.py、budgeting.py，接入已有 convert_to_llm。
- 调整 session.py、factory.py：固定 system prompt，迁移动态记忆与技能。
- 必要时扩展 agent.py / types.py / loop.py 的通用输出预算配置传递；禁止依赖应用模块。
- 增加固定前缀、Provider 转换与默认关闭兼容性检查。

完成条件：真实请求对象的 system prompt 稳定；无压缩时功能兼容；预算状态可观测。

### Phase 1：离线预算保护与输出缩减

- 实现 grouping.py、pruning.py、validation.py。
- 新增 /context 和 /compact preview，支持规则方式的有界请求视图。
- 在同一工具循环和 continue_run 中验证预算与协议。
- 补齐 Provider context-window 错误分类及一次性请求级降级。

完成条件：无需模型摘要即可控制大工具输出，超限场景有明确、安全的失败路径。

### Phase 2：结构化摘要与恢复

- 实现 summarizer.py、checkpoints.py。
- 完善 session_store.py / serde.py 的稳定消息标识、兼容读取、增量 finalized 持久化和幂等追加。
- 实现有来源摘要、约束清单、检查点提交与恢复、/compact。
- 对照评估生成真实收益报告。

完成条件：长会话可持续运行；重启能恢复；没有工具重复执行或约束无声丢失。

### Phase 3：策略优化

- 针对真实样例调整摘要频率、保留窗口、tokenizer 和检索配额。
- 引入任务感知的证据选择与更细的源码引用失效策略。
- 验证 IM 的队列、取消与上下文生命周期后再接入。
- 只有质量与成本数据支持时才考虑默认开启。

本轮交付仅为设计文档；实施阶段按上述完成条件推进，每阶段报告修改文件、验证证据和未解决问题。
