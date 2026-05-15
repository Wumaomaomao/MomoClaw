from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path

from momoclaw.ai.models import get_model
from momoclaw.im.adapters import (
    FeishuAdapter,
    FeishuAdapterConfig,
    FeishuLongConnOptions,
    run_feishu_long_connection,
)
from momoclaw.im.service import IMService, IMServiceConfig

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL_ID = "deepseek-v4-pro"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MomoClaw Feishu long connection bot.")
    parser.add_argument("--workspace", default=".", help="Workspace directory for sessions and tools.")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, help="AI provider name.")
    parser.add_argument("--model", default=DEFAULT_MODEL_ID, help="Model id within the provider.")
    parser.add_argument("--base-url", default=None, help="Override model base URL.")
    parser.add_argument("--read-write", action="store_true", help="Enable write/edit/bash tools. Default is read-only.")
    parser.add_argument("--max-reply-chars", type=int, default=4000, help="Maximum reply length before truncation.")
    parser.add_argument("--no-usage", action="store_true", help="Do not append token usage to replies.")
    parser.add_argument("--card", action="store_true", help="Reply with Feishu markdown cards instead of plain text.")
    parser.add_argument("--metadata", action="store_true", help="Fetch Feishu user/chat metadata and inject it into prompts.")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"], help="lark-oapi log level.")
    parser.add_argument("--app-id", default=None, help="Feishu app id. Defaults to FEISHU_APP_ID.")
    parser.add_argument("--app-secret", default=None, help="Feishu app secret. Defaults to FEISHU_APP_SECRET.")
    parser.add_argument("--verify-token", default=None, help="Feishu verification token. Defaults to FEISHU_VERIFY_TOKEN.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    app_id = args.app_id or os.getenv("FEISHU_APP_ID")
    app_secret = args.app_secret or os.getenv("FEISHU_APP_SECRET")
    verify_token = args.verify_token if args.verify_token is not None else os.getenv("FEISHU_VERIFY_TOKEN")
    if not app_id:
        raise SystemExit("Missing Feishu app id. Set FEISHU_APP_ID or pass --app-id.")
    if not app_secret:
        raise SystemExit("Missing Feishu app secret. Set FEISHU_APP_SECRET or pass --app-secret.")

    model = get_model(args.provider, args.model)
    if args.base_url:
        model = replace(model, base_url=args.base_url)

    workspace_dir = Path(args.workspace).resolve()
    adapter = FeishuAdapter(
        FeishuAdapterConfig(
            app_id=app_id,
            app_secret=app_secret,
            verify_token=verify_token,
        )
    )
    service = IMService(
        adapter,
        IMServiceConfig(
            workspace_dir=workspace_dir,
            model=model,
            read_only_mode=not args.read_write,
            max_reply_chars=args.max_reply_chars,
            use_card_reply=args.card,
            show_usage_in_reply=not args.no_usage,
            include_metadata_context=args.metadata,
        ),
    )

    mode = "read-write" if args.read_write else "read-only"
    reply_mode = "card" if args.card else "text"
    metadata_mode = "metadata" if args.metadata else "no-metadata"
    print(
        f"[im] Feishu long connection starting workspace={workspace_dir} "
        f"model={model.provider}/{model.id} mode={mode} reply={reply_mode} {metadata_mode}"
    )
    run_feishu_long_connection(
        service,
        FeishuLongConnOptions(
            app_id=app_id,
            app_secret=app_secret,
            log_level=args.log_level,
        ),
    )


if __name__ == "__main__":
    main()
