#!/usr/bin/env python3
"""带鉴权的工具调用可靠性测试运行器。

由于 ``test_tool_reliability.py`` 的请求默认不携带任何鉴权头，而开启
``DOUBAO_API_KEY`` 的实例会拒绝匿名请求（401），本运行器在**不修改**原有测试
脚本的前提下，为每次请求注入 OpenAI 风格的 ``Authorization: Bearer <key>`` 头。

测试场景、工具定义、结果判定（``classify_result``）与汇总统计全部复用
``test_tool_reliability.py``；本文件只负责三件事：
  1. 构造鉴权头；
  2. 解析命令行参数；
  3. 调用测试并保存报告。

用法::

    # 实例未开启鉴权
    python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090

    # 实例开启鉴权（推荐用环境变量，避免 API Key 出现在 shell 历史）
    export DOUBAO_API_KEY=your-key
    python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090 --rounds 50

    # 或显式传参
    python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090 --api-key your-key
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import httpx  # noqa: E402

import test_tool_reliability as T  # noqa: E402


def _install_auth_header(api_key: str) -> None:
    """Monkeypatch httpx.post，为所有请求注入 Bearer 头。"""
    _orig_post = httpx.post

    def _post(url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {api_key}"
        kwargs["headers"] = headers
        return _orig_post(url, **kwargs)

    httpx.post = _post


def main() -> int:
    parser = argparse.ArgumentParser(
        description="带鉴权的工具调用可靠性测试（复用 test_tool_reliability.py 的场景与判定）"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:9090",
        help="被测 doubao2api 实例地址（默认 http://127.0.0.1:9090）",
    )
    parser.add_argument("--rounds", type=int, default=50, help="测试轮次（默认 50）")
    parser.add_argument(
        "--delay",
        type=int,
        default=T.DELAY_BETWEEN_ROUNDS,
        help=f"每轮间隔秒数，防限流（默认 {T.DELAY_BETWEEN_ROUNDS}）",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DOUBAO_API_KEY", ""),
        help="Bearer token；缺省读环境变量 DOUBAO_API_KEY，为空则不加鉴权头",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(tempfile.gettempdir(), "tool_reliability_report.json"),
        help="报告输出路径（默认系统临时目录）",
    )
    args = parser.parse_args()

    if args.api_key:
        _install_auth_header(args.api_key)
        print("[auth] 已注入 Authorization: Bearer <key>", flush=True)
    else:
        print(
            "[auth] 未提供 API Key，请求将不带鉴权头（实例若开启鉴权会返回 401）",
            flush=True,
        )

    report = T.run_reliability_test(
        base_url=args.base_url, max_rounds=args.rounds, delay=args.delay
    )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[report] 已保存到 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
