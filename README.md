# MomoClaw

MomoClaw is a Python coding-agent project with a unified AI provider layer,
agent loop, coding-agent session wrapper, IM bridge, and an MVP interactive CLI.

## Run The CLI

```bash
uv run momoclaw
```

Useful options:

```bash
uv run momoclaw
uv run momoclaw --provider openai --model gpt-4.1-mini
uv run momoclaw --provider anthropic --model claude-sonnet-4-5
uv run momoclaw --provider deepseek --model deepseek-v4-pro
uv run momoclaw --provider openai --model gpt-4.1-mini --base-url https://gateway.example/v1
uv run momoclaw --workspace /path/to/project
uv run momoclaw --read-write
```

Set the matching API key before sending prompts:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export DEEPSEEK_API_KEY=...
```

The default model is `deepseek/deepseek-v4-pro`, so `DEEPSEEK_API_KEY` is enough
for the default CLI path.

## CLI Commands

- `/help`: show commands.
- `/model`: show current provider/model/base URL.
- `/model list`: list built-in providers, models, and default base URLs.
- `/model set <provider> <model_id> [base_url]`: save model config and start a new session.
- `/model base-url <url|default>`: override or reset base URL for the current model.
- `/tools`: show enabled tools.
- `/usage`: show last model usage.
- `/new`: start a new session with the current model.
- `/clear`: clear current in-memory conversation.
- `/exit`: exit.

The CLI stores model configuration, including optional `base_url`, under
`.momoclaw/config.json` in the selected workspace. Sessions are stored under
`.momoclaw/sessions/`.

By default the CLI starts in read-only mode and only exposes the `read` tool.
Use `--read-write` to enable `write`, `edit`, and `bash`. The CLI prints compact
tool call/result lines such as `[tool] read README.md` and `[tool:ok] read`.

## Development

Run tests:

```bash
uv run pytest tests
```

Project planning and progress are tracked in `docs/DEVELOPMENT.md`.

## IM MVP

The IM bridge lives under `momoclaw/im/`. It includes normalized IM message
types, persistent channel/thread session routing, a platform-independent
`IMService`, an in-memory fake adapter for tests, and a Feishu adapter skeleton
for webhook parsing plus text replies.

Current IM control commands:

- `/help`
- `/new`
- `/clear`
- `/session`
- `/usage`

Reference doc: `momoclaw/im/README.md`.

### Feishu Long Connection

Install the optional Feishu long-connection dependency:

```bash
uv sync --extra feishu --extra dev
```

Run locally:

```bash
export FEISHU_APP_ID=...
export FEISHU_APP_SECRET=...
export DEEPSEEK_API_KEY=...

uv run --extra feishu momoclaw-feishu-longconn --workspace /home/wyc/MomoClaw
```

Useful options:

```bash
uv run --extra feishu momoclaw-feishu-longconn --card
uv run --extra feishu momoclaw-feishu-longconn --read-write
uv run --extra feishu momoclaw-feishu-longconn --metadata
uv run --extra feishu momoclaw-feishu-longconn --provider openai --model gpt-4.1-mini
uv run --extra feishu momoclaw-feishu-longconn --base-url https://gateway.example/v1
```
