from __future__ import annotations

import pytest

from momoclaw.im.feishu_longconn_cli import build_parser, main


def test_feishu_longconn_parser_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.workspace == "."
    assert args.provider == "deepseek"
    assert args.model == "deepseek-v4-pro"
    assert args.read_write is False
    assert args.card is False
    assert args.metadata is False


def test_feishu_longconn_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    with pytest.raises(SystemExit, match="Missing Feishu app id"):
        main([])
