# MomoClaw IM Layer

The IM layer is the bridge between platform events and long-lived coding-agent
sessions. Its MVP follows the same core shape as XingClaw:

```text
platform webhook
  -> adapter parses platform payload
  -> IMService deduplicates and queues by channel/thread
  -> SessionRouter resolves a persistent coding-agent session id
  -> AgentSession handles the prompt
  -> adapter sends a text reply
```

## Modules

- `types.py`: platform-neutral incoming/outgoing message types and adapter
  protocol.
- `session_router.py`: persistent mapping from `platform/channel/thread` to
  `.momoclaw/im/session_map.json`.
- `service.py`: deduplication, stale event filtering, per-channel serialization,
  control commands, coding-agent prompting, and replies.
- `adapters/fake.py`: in-memory adapter for tests and local development.
- `adapters/feishu.py`: Feishu webhook parsing, text/card replies, card
  updates, token caching, and user/chat metadata lookup.
- `adapters/feishu_longconn.py`: optional `lark-oapi` long connection entry for
  local or intranet development.

## Control Commands

- `/help`: show IM commands.
- `/new`: rotate the current channel/thread session.
- `/clear`: same as `/new` for the MVP.
- `/session`: show the current session id.
- `/usage`: show last model token usage for the active session.

## Defaults

IM sessions default to `deepseek/deepseek-v4-pro` and read-only coding-agent
tools. This is intentional: IM conversations can be high fan-out and should not
mutate a workspace unless explicitly configured.

`IMServiceConfig` accepts `provider`, `model_id`, optional `base_url`, or a
fully constructed `Model`. Tests can inject `stream_fn` to avoid live API calls.

## Feishu MVP Boundary

The Feishu adapter currently supports:

- URL verification responses.
- `im.message.receive_v1` text message parsing.
- Bot mention removal for text messages.
- Text replies, including reply-to-message mode.
- Markdown card replies.
- Card content updates for future streaming replies.
- Basic user and chat metadata lookup with TTL caches.

A production server entrypoint is intentionally left out of the MVP. The next
step should add a small ASGI app that receives Feishu webhooks and calls
`IMService.handle_webhook`.

See `docs/04_IM飞书桥接层/02_飞书适配器实现.md` for the Feishu adapter design notes.

## Run Feishu Long Connection

Long connection mode uses `lark-oapi` and does not require a public webhook URL.

```bash
uv sync --extra feishu --extra dev

export FEISHU_APP_ID=...
export FEISHU_APP_SECRET=...
export DEEPSEEK_API_KEY=...

uv run --extra feishu momoclaw-feishu-longconn --workspace /home/wyc/MomoClaw
```

Options:

- `--card`: use Feishu markdown card replies.
- `--read-write`: enable `write`, `edit`, and `bash`; default is read-only.
- `--provider`, `--model`, `--base-url`: choose model provider and endpoint.
- `--max-reply-chars`: truncate long replies.
- `--no-usage`: hide token usage footer.
- `--metadata`: fetch Feishu user/chat metadata and inject it into prompts.

Metadata lookup is disabled by default because it requires extra Feishu
permissions and contact visibility. Without those permissions, Feishu may return
400 responses for user/chat lookup even though message receive/reply works.
