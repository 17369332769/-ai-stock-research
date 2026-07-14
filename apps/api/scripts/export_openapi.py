"""导出 OpenAPI 契约快照（spec §7：CI 比较生成的 openapi.json，未审查的契约变化会失败）。

用法::

    python -m apps.api.scripts.export_openapi            # 写入仓库内快照
    python -m apps.api.scripts.export_openapi --check    # 只比对，不写（CI 用）

快照路径：``apps/api/openapi.json``。
契约变化时**必须**显式重新生成并在 code review 中审查 diff。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from apps.api.app.main import create_app

SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def generate_openapi() -> dict[str, Any]:
    app = create_app()
    schema: dict[str, Any] = app.openapi()
    return schema


def dumps(schema: dict[str, Any]) -> str:
    # 排序键 + 固定缩进：让 diff 只反映真实契约变化，而不是字典顺序抖动
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出/校验 OpenAPI 契约快照")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件（CI 用）")
    args = parser.parse_args()

    current = dumps(generate_openapi())

    if args.check:
        if not SNAPSHOT_PATH.exists():
            print(f"[FAIL] 契约快照缺失：{SNAPSHOT_PATH}", file=sys.stderr)
            return 1
        stored = SNAPSHOT_PATH.read_text(encoding="utf-8")
        if stored != current:
            print(
                "[FAIL] OpenAPI 契约与仓库快照不一致。\n"
                "若这是有意的契约变更，请运行 `python -m apps.api.scripts.export_openapi` "
                "并在 review 中审查 diff。",
                file=sys.stderr,
            )
            return 1
        print("[OK] OpenAPI 契约与快照一致")
        return 0

    SNAPSHOT_PATH.write_text(current, encoding="utf-8")
    print(f"[OK] 已写入 {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
