"""可执行训练入口：审计 → 泄漏测试 → walk-forward → candidate → 可选激活。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from typing import Any

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.db import session_scope
from apps.api.app.core.runtime import get_clock
from services.prediction.training.audit import audit_training_data
from services.prediction.training.pipeline import run_training
from services.prediction.training.registry import activate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计数据并训练沪深300日频模型")
    parser.add_argument(
        "--horizon",
        action="append",
        choices=("today_close", "next_5d"),
        help="可重复；默认训练两个 horizon",
    )
    parser.add_argument("--audit-only", action="store_true", help="只输出数据质量审计")
    parser.add_argument(
        "--activate-passing",
        action="store_true",
        help="仅当发布门槛通过且优于基准时激活",
    )
    parser.add_argument("--no-qlib", action="store_true", help="不额外导出 Qlib 布局")
    return parser


def _run_leakage_tests() -> None:
    command = [sys.executable, "-m", "pytest", "-q", "services/prediction/tests/test_leakage.py"]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit("未来数据泄漏测试未通过，拒绝训练")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    now = get_clock().now()
    async with session_scope() as session:
        audit = await audit_training_data(session, as_of=to_shanghai(now).date())
        result: dict[str, Any] = {"audit": audit.to_json(), "training_runs": []}
        if args.audit_only:
            return result
        if not audit.ready:
            raise SystemExit("训练数据审计未通过：" + "; ".join(audit.blockers))

    _run_leakage_tests()
    horizons = args.horizon or ["today_close", "next_5d"]
    for horizon in horizons:
        async with session_scope() as session:
            run = await run_training(
                session,
                horizon=horizon,
                leakage_tests_passed=True,
                build_qlib=not args.no_qlib,
            )
            summary = run.summary()
            summary["status"] = "candidate"
            if args.activate_passing:
                if not run.model.release_gate.passed:
                    summary["activation"] = "blocked_by_release_gate"
                elif not run.model.better_than_baseline:
                    summary["activation"] = "blocked_not_better_than_baseline"
                else:
                    await activate(
                        session,
                        model_key=run.model.model_key,
                        version=run.model.version,
                        now=now,
                    )
                    summary["status"] = "active"
                    summary["activation"] = "activated"
            result["training_runs"].append(summary)
    return result


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
