# MomoClaw 学习指南

MomoClaw 是一个 Python 编写的编程助手项目，可以作为 CLI 命令行工具或 IM 机器人运行。它的核心思路是：接收用户提问 → 调用大模型 → 执行工具（如读文件、写代码、运行命令）→ 把结果传回模型继续对话。

## 项目结构

```
momoclaw/
├── ai/                # AI 层——统一封装各个模型提供商
├── agent/             # Agent 循环——模型推理 + 工具执行的循环逻辑
├── coding_agent/      # 编程助手层——加上工作区、文件工具、会话持久化
├── im/                # IM 层——支持飞书等即时通讯平台
└── cli.py             # 命令行入口
```

## 各层职责

### 1. `momoclaw.ai` — AI 层

负责与各个 AI 模型提供商打交道：

- 统一的消息类型（`UserMessage` / `AssistantMessage` / `ToolResultMessage`）。
- 统一的流式事件协议，屏蔽 OpenAI 和 Anthropic 的差异。
- 内置模型表：OpenAI、Anthropic、DeepSeek。
- 支持自定义 `base_url`，可以对接兼容网关。

关键文件：`types.py`、`stream.py`、`models.py`、`registry.py`。

### 2. `momoclaw.agent` — Agent 核心循环

纯逻辑层，不关心工作区、IM 或 CLI 渲染：

- 接收用户提示词 + 对话上下文。
- 调用 AI 层进行流式推理。
- 解析模型返回的工具调用，逐一执行。
- 将工具结果传回模型，直到模型不再请求工具调用。
- 通过事件机制（`subscribe`）通知上层。

关键文件：`loop.py`、`agent.py`、`types.py`。

### 3. `momoclaw.coding_agent` — 编程助手应用层

在 agent 核心之上叠加编程场景：

- 内置工具：`read`（读文件）、`write`（写文件）、`edit`（编辑文件）、`bash`（执行命令）。
- `write`、`edit`、`bash` 仅在 `read_only_mode=False` 时启用。
- `AgentSession`：长生命周期会话，支持事件订阅。
- `SessionStore`：用 JSONL 文件持久化消息和事件日志，存储于 `.momoclaw/sessions/`。

关键文件：`builtin_tools.py`、`session.py`、`session_store.py`、`factory.py`。

### 4. `momoclaw.im` — IM 即时通讯层

把编程助手接入聊天平台：

- `types.py`：平台无关的消息模型（入站/出站）。
- `session_router.py`：按 `平台/频道/线程` 映射到持久会话 ID。
- `service.py`：去重、排队、串行化、命令处理、回复发送。
- `adapters/`：平台适配器（飞书适配器和测试用假适配器）。

IM 默认以只读模式运行，避免聊天场景下意外修改工作区。

### 5. `momoclaw.cli` — 命令行入口

交互式 CLI，提供：

- `/model set` — 切换模型。
- `/new` — 开启新会话。
- `/clear` — 清空内存中的对话。
- `/tools` / `/usage` — 查看工具和用量。
- `--read-write` — 启用写入和命令执行工具。

运行命令：

```bash
uv run momoclaw                              # 默认 deepseek/deepseek-v4-pro
uv run momoclaw --provider openai --model gpt-4.1-mini
uv run momoclaw --read-write                 # 启用写权限
```

## 核心流程（一次完整的对话）

```
用户输入 "帮我看看 README.md 的内容"
        ↓
    cli.py → session.prompt(text)
        ↓
    agent.loop.run_agent_loop()
        ├─ 调用 AI 层 → 模型返回 read("README.md")
        ├─ 执行工具 → 读取文件内容
        ├─ 将结果传回 AI 层
        └─ 模型生成摘要文本 → 返回 AssistantMessage
        ↓
    cli.py 打印结果给用户
```

## 关键设计决策

1. **分层清晰**：AI 层不感知工具，agent 不感知工作区，coding_agent 不感知 IM。
2. **事件驱动**：上层通过 `subscribe` 监听事件（工具调用开始/结束、文本增量等），CLI 用此打印 `[tool]` 日志。
3. **会话持久化**：JSONL 追加写入，重启后可恢复历史消息。
4. **安全默认值**：CLI 和 IM 都默认只读，显式 opt-in 才能写文件和执行命令。

## 开发和测试

```bash
# 安装依赖
uv sync

# 运行测试
uv run pytest tests

# 运行 CLI
uv run momoclaw
```

## 扩展方向

- 添加新的模型提供商：在 `ai/providers/` 实现，然后在 `models.py` 注册。
- 添加新工具：在 `coding_agent/builtin_tools.py` 添加。
- 接入新的 IM 平台：在 `im/adapters/` 实现适配器。
