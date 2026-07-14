"""验收 §15.19：应用层的所有外部数据请求都必须经过 OpenBB 内部 REST。

spec §4.2 / §5.1：
- 业务代码**不得直接调用第三方 URL**；
- **只有** ``services/openbb_extensions`` 里的自定义 Provider 可以访问
  AKShare、巨潮、交易所和中证指数来源。

这是一个静态扫描：它不需要数据库，也不访问公网。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]

# 被扫描的目录（spec §5.1 里的"业务代码"）
SCANNED_ROOTS = ("apps", "services")

# 唯一豁免：自定义 OpenBB Provider —— 它们的职责就是访问上游
EXEMPT_PREFIXES = (
    REPO_ROOT / "services" / "openbb_extensions",
)

SKIPPED_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    ".next",
    ".venv",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

SCANNED_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"}

# 上游数据源域名（AKShare 背后的东财、巨潮、上交所、深交所、中证指数）
FORBIDDEN_HOSTS = (
    "eastmoney.com",
    "push2.eastmoney.com",
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
    "csindex.com.cn",
    "akshare.akfamily.xyz",
    "sina.com.cn",
    "finance.yahoo.com",
)

# 直接 import 数据源库
FORBIDDEN_IMPORTS = (
    "import akshare",
    "from akshare",
)


def is_exempt(path: Path) -> bool:
    return any(path.is_relative_to(prefix) for prefix in EXEMPT_PREFIXES)


def is_test_file(path: Path) -> bool:
    """测试夹具里出现 source_url 字面量是**数据**，不是调用。

    数据库 schema 本身就要求保存 ``source_url``（spec §4.2），
    因此固定夹具中出现上游 URL 是正常的；真正要禁止的是**业务代码**里的直连。
    """
    parts = set(path.parts)
    return "tests" in parts or "fixtures" in parts


def iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SCANNED_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if SKIPPED_DIR_NAMES & set(path.parts):
                continue
            if is_exempt(path):
                continue
            files.append(path)
    return files


def test_scanner_actually_sees_files() -> None:
    """守卫：扫描器本身不能因为路径写错而"零命中假绿"。"""
    files = iter_scanned_files()
    assert len(files) >= 10, f"扫描到的文件太少（{len(files)}），扫描根可能配错了：{REPO_ROOT}"
    assert any(p.name == "main.py" for p in files)


def test_exempt_path_exists() -> None:
    """豁免目录必须真实存在，否则豁免规则形同虚设。"""
    assert (REPO_ROOT / "services" / "openbb_extensions").exists()


def test_no_akshare_import_outside_providers() -> None:
    """只有 services/openbb_extensions 可以 import akshare。"""
    offenders: list[str] = []
    for path in iter_scanned_files():
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(stripped.startswith(bad) for bad in FORBIDDEN_IMPORTS):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {stripped}")

    assert not offenders, (
        "业务代码直接 import akshare（spec §4.2：只有 OpenBB Provider 可以访问数据源）：\n"
        + "\n".join(offenders)
    )


def test_no_third_party_urls_in_business_code() -> None:
    """业务代码里不得出现上游数据源的 URL/域名。"""
    offenders: list[str] = []
    for path in iter_scanned_files():
        if is_test_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        for host in FORBIDDEN_HOSTS:
            if host in lowered:
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if host in line.lower():
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{line_no}: 直连 {host} → {line.strip()}"
                        )

    assert not offenders, (
        "业务代码里出现第三方数据源 URL（spec §4.2：必须经 OpenBB 内部 REST）：\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("module", ["apps.api.app.main", "apps.api.app.services.freshness"])
def test_api_layer_does_not_import_akshare(module: str) -> None:
    """运行时校验：导入 API 模块后，akshare 不应该出现在 sys.modules 里。"""
    import importlib
    import sys

    importlib.import_module(module)
    assert "akshare" not in sys.modules, f"{module} 间接拉起了 akshare"


# 前端发起网络请求的构造。命中这些且同一行出现上游域名 = 真的直连。
_WEB_REQUEST_CALL = re.compile(
    r"(fetch|axios|XMLHttpRequest|EventSource|WebSocket|\.(get|post|put|delete)\s*\(|"
    r"<script[^>]+src=|<img[^>]+src=|import\s*\()",
    re.IGNORECASE,
)

# 生产前端源码目录：这里**连字符串都不许出现**上游域名（域名只能来自 API 返回的 source_url）。
_WEB_PRODUCTION_DIRS = ("app", "components", "lib")


def test_web_frontend_does_not_call_upstream_directly() -> None:
    """apps/web 只能调本地 API，不得直连行情源（spec §5.1：前端只做展示）。

    注意区分两件事：
    * **渲染**指向巨潮/东方财富的链接是**必须**的 —— spec §7.3 的 ``EvidenceDTO.source_url``
      与 §3.2「每条 AI 结论必须包含可点击证据」要求前端把原文链接渲染成 ``<a href>``。
      因此测试夹具（mock 的 API 响应）里出现这些域名是正确的，不能一律判违规。
    * **请求**这些域名才是违规 —— 数据必须经本地 API（它再经 OpenBB 内部 REST）。

    所以断言分两层：生产源码不得硬编码上游域名；任何文件都不得对上游域名发起网络调用。
    """
    web_root = REPO_ROOT / "apps" / "web"
    if not web_root.exists():
        pytest.skip("apps/web 尚未创建")

    hardcoded: list[str] = []
    calls: list[str] = []

    for path in web_root.rglob("*"):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIR_NAMES & set(path.parts):
            continue
        relative = path.relative_to(web_root)
        in_production = relative.parts and relative.parts[0] in _WEB_PRODUCTION_DIRS

        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            lowered = line.lower()
            for host in FORBIDDEN_HOSTS:
                if host not in lowered:
                    continue
                where = f"{path.relative_to(REPO_ROOT)}:{line_no}"
                if _WEB_REQUEST_CALL.search(line):
                    calls.append(f"{where}: 向 {host} 发起请求 → {line.strip()}")
                elif in_production:
                    hardcoded.append(f"{where}: 生产源码硬编码 {host} → {line.strip()}")

    assert not calls, "前端直连上游数据源（必须经本地 API）：\n" + "\n".join(calls)
    assert not hardcoded, (
        "前端生产源码硬编码了上游域名（域名只能来自 API 返回的 source_url）：\n" + "\n".join(hardcoded)
    )
