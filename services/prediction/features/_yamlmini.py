"""受限 YAML 子集解析器（零依赖）。

为什么不用 pyyaml：``pyproject.toml`` 的依赖清单里没有 pyyaml（它只是 pyqlib 的传递依赖），
而特征契约 ``config/features/*.yaml`` 是 point-in-time 的地基，必须在**没有任何第三方库**的
环境里也能加载 —— 否则泄漏测试就依赖了重型科学栈的安装状态。

支持的子集（刻意受限；超出范围一律报错，而不是猜测语义）：
- 块映射 ``key: value`` 与嵌套块
- 块序列 ``- scalar`` / ``- key: value``
- 块标量 ``|`` ``|-`` ``>`` ``>-``
- 标量：``null`` / ``~`` / ``true`` / ``false`` / int / float / 单双引号字符串 / 裸字符串
- 行内空列表与简单流式列表 ``[a, b, c]``
- ``#`` 注释（行首，或前面是空白的行内注释）

不支持（遇到即抛 ``YamlSubsetError``）：锚点、别名、标签、多文档、复杂键、流式映射、制表符缩进。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["YamlSubsetError", "parse_yaml_subset"]

_BLOCK_SCALAR_STYLES = (">-", "|-", ">", "|")


class YamlSubsetError(ValueError):
    """配置文件超出支持的 YAML 子集，或语法错误。"""

    def __init__(self, message: str, lineno: int) -> None:
        super().__init__(f"第 {lineno} 行：{message}")
        self.lineno = lineno


def parse_yaml_subset(text: str) -> Any:
    """解析受限 YAML 文本。顶层必须是映射或序列。"""
    raw = text.split("\n")
    for i, line in enumerate(raw):
        if "\t" in line[: _indent_of(line) + 1]:
            raise YamlSubsetError("缩进禁止使用制表符", i + 1)
    start = _skip(raw, 0)
    if start >= len(raw):
        return None
    value, index = _parse_block(raw, start, _indent_of(raw[start]))
    index = _skip(raw, index)
    if index < len(raw):
        raise YamlSubsetError(f"无法解析的残留内容：{raw[index]!r}", index + 1)
    return value


# ── 行工具 ──────────────────────────────────────────────────────────────────


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _skip(raw: list[str], index: int) -> int:
    """跳到下一个有内容的行。"""
    while index < len(raw) and _is_blank(raw[index]):
        index += 1
    return index


def _strip_comment(line: str) -> str:
    """去掉行内注释；引号内的 ``#`` 不算注释。"""
    out: list[str] = []
    quote: str | None = None
    for i, char in enumerate(line):
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).rstrip()


def _split_key(content: str, lineno: int) -> tuple[str, str]:
    """把 ``key: value`` 拆成 (key, value)。冒号必须在引号外，且后面是空白或行尾。"""
    quote: str | None = None
    for i, char in enumerate(content):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1 :].strip()
    raise YamlSubsetError(f"期望 'key: value'，实际是 {content!r}", lineno)


def _looks_like_mapping(content: str) -> bool:
    try:
        _split_key(content, 0)
    except YamlSubsetError:
        return False
    return True


# ── 标量 ────────────────────────────────────────────────────────────────────


def _scalar(text: str) -> Any:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part.strip()) for part in inner.split(",")]
    lowered = text.lower()
    if lowered in ("", "null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        return text
    # YAML 用 .nan / .inf 表示非有限数；裸的 nan / inf 是字符串。
    # Python 的 float("nan") 会成功，若不拦住，配置里的 `missing: nan` 会变成浮点 NaN
    # 而不是策略名 "nan" —— 缺失值策略会被静默吃掉。
    if math.isnan(number) or math.isinf(number):
        return text
    return number


# ── 块解析 ──────────────────────────────────────────────────────────────────


def _parse_block(raw: list[str], index: int, indent: int) -> tuple[Any, int]:
    index = _skip(raw, index)
    if index >= len(raw):
        return None, index
    content = _strip_comment(raw[index]).strip()
    if content == "-" or content.startswith("- "):
        return _parse_sequence(raw, index, indent)
    return _parse_mapping(raw, index, indent)


def _parse_mapping(raw: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while True:
        index = _skip(raw, index)
        if index >= len(raw):
            break
        line_indent = _indent_of(raw[index])
        if line_indent < indent:
            break
        lineno = index + 1
        if line_indent > indent:
            raise YamlSubsetError(f"缩进异常（期望 {indent} 空格，实际 {line_indent}）", lineno)
        content = _strip_comment(raw[index]).strip()
        if content == "-" or content.startswith("- "):
            break  # 同级序列属于上一层的 key
        key, rest = _split_key(content, lineno)
        if key in result:
            raise YamlSubsetError(f"重复的 key：{key!r}", lineno)
        index += 1
        value, index = _parse_value(raw, index, indent, rest, lineno)
        result[key] = value
    return result, index


def _parse_value(raw: list[str], index: int, indent: int, rest: str, lineno: int) -> tuple[Any, int]:
    """解析 ``key:`` 之后的值：块标量 / 嵌套块 / 行内标量。"""
    if rest in _BLOCK_SCALAR_STYLES:
        return _parse_block_scalar(raw, index, indent, rest)
    if rest != "":
        return _scalar(rest), index
    nxt = _skip(raw, index)
    if nxt >= len(raw):
        return None, index
    next_indent = _indent_of(raw[nxt])
    next_content = _strip_comment(raw[nxt]).strip()
    if next_indent > indent:
        return _parse_block(raw, nxt, next_indent)
    if next_indent == indent and (next_content == "-" or next_content.startswith("- ")):
        # 序列与其 key 同缩进，也是合法 YAML
        return _parse_sequence(raw, nxt, indent)
    if next_indent == indent:
        return None, index  # 空值
    raise YamlSubsetError("缩进返回到未知层级", nxt + 1)


def _parse_sequence(raw: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while True:
        index = _skip(raw, index)
        if index >= len(raw):
            break
        line_indent = _indent_of(raw[index])
        if line_indent < indent:
            break
        lineno = index + 1
        if line_indent > indent:
            raise YamlSubsetError(f"序列缩进异常（期望 {indent} 空格，实际 {line_indent}）", lineno)
        content = _strip_comment(raw[index]).strip()
        if content != "-" and not content.startswith("- "):
            break
        rest = "" if content == "-" else content[2:].strip()
        item_indent = line_indent + 2
        index += 1
        if rest == "":
            value, index = _parse_block(raw, index, item_indent)
        elif _looks_like_mapping(rest):
            value, index = _parse_sequence_mapping_item(raw, index, item_indent, rest, lineno)
        else:
            value = _scalar(rest)
        items.append(value)
    return items, index


def _parse_sequence_mapping_item(
    raw: list[str], index: int, item_indent: int, first: str, lineno: int
) -> tuple[dict[str, Any], int]:
    """序列元素是映射：``- name: ret_1`` 后续同缩进的 key 都属于同一个元素。"""
    key, rest = _split_key(first, lineno)
    value, index = _parse_value(raw, index, item_indent, rest, lineno)
    item: dict[str, Any] = {key: value}
    tail, index = _parse_mapping(raw, index, item_indent)
    for extra_key, extra_value in tail.items():
        if extra_key in item:
            raise YamlSubsetError(f"重复的 key：{extra_key!r}", lineno)
        item[extra_key] = extra_value
    return item, index


def _parse_block_scalar(raw: list[str], index: int, indent: int, style: str) -> tuple[str, int]:
    lines: list[str] = []
    block_indent: int | None = None
    while index < len(raw):
        line = raw[index]
        if line.strip() == "":
            lines.append("")
            index += 1
            continue
        line_indent = _indent_of(line)
        if line_indent <= indent:
            break
        if block_indent is None:
            block_indent = line_indent
        if line_indent < block_indent:
            raise YamlSubsetError("块标量缩进不一致", index + 1)
        lines.append(line[block_indent:].rstrip())
        index += 1
    while lines and lines[-1] == "":
        lines.pop()
    if style.startswith(">"):
        text = " ".join(part.strip() for part in lines if part.strip())
    else:
        text = "\n".join(lines)
    if not style.endswith("-") and style.startswith("|"):
        text += "\n"
    return text, index
