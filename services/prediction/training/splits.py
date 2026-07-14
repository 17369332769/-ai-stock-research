"""时间切分（spec §9.3）。

三条硬规矩：
1. **禁止随机切分** —— ``assert_time_ordered`` 会把任何时间上交错的切分直接判死。
2. 训练/验证/测试时间段**不得重叠**。
3. 两段之间必须留 **embargo**（禁运期）：next_5d 的标签要看未来 5 个交易日，
   如果训练段的最后一天紧挨着验证段的第一天，训练标签就已经"看过"验证期的价格了。
   embargo = horizon 的交易日数，把这条泄漏堵死。

这是 walk-forward 里最容易被忽略、也最致命的一处泄漏。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

__all__ = [
    "DateRange",
    "Fold",
    "HoldoutSplit",
    "SplitError",
    "assert_time_ordered",
    "make_holdout_split",
    "make_walk_forward_folds",
]


class SplitError(ValueError):
    """切分非法（重叠、乱序、样本不足）。绝不"修正"后继续 —— 直接炸。"""


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise SplitError(f"区间非法：{self.start} > {self.end}")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_json(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class HoldoutSplit:
    """训练 / 验证 / 测试三段，按时间先后排列且互不重叠。

    - train：拟合 booster 与归一化参数（归一化只在这里拟合，spec §9.3）
    - validation：拟合概率校准器、算覆盖率、作为 PSI 的参考分布
    - test：**只**用来跟基准比（better_than_baseline），不参与任何拟合
    """

    train: DateRange
    validation: DateRange
    test: DateRange
    embargo_sessions: int

    def to_json(self) -> dict[str, object]:
        return {
            "train": self.train.to_json(),
            "validation": self.validation.to_json(),
            "test": self.test.to_json(),
            "embargo_sessions": self.embargo_sessions,
        }


@dataclass(frozen=True, slots=True)
class Fold:
    """expanding-window walk-forward 的一折：训练窗口只增不减，验证窗口向前滚动。"""

    index: int
    train: DateRange
    validation: DateRange
    embargo_sessions: int

    def to_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "train": self.train.to_json(),
            "validation": self.validation.to_json(),
            "embargo_sessions": self.embargo_sessions,
        }


def assert_time_ordered(*ranges: DateRange, embargo_sessions: int, sessions: Sequence[date]) -> None:
    """相邻两段必须严格时间有序，且中间至少隔 ``embargo_sessions`` 个交易日。

    随机切分（时间上交错）必然触发这里的异常 —— 这就是 spec §16 要求的"随机切分检测"。
    """
    index = {day: i for i, day in enumerate(sessions)}
    for earlier, later in pairwise(ranges):
        if earlier.end >= later.start:
            raise SplitError(
                f"时间段重叠或乱序：前段结束于 {earlier.end}，后段起始于 {later.start}。"
                f"禁止随机切分（spec §9.3）"
            )
        if earlier.end not in index or later.start not in index:
            raise SplitError("切分边界不是交易日")
        gap = index[later.start] - index[earlier.end] - 1
        if gap < embargo_sessions:
            raise SplitError(
                f"禁运期不足：{earlier.end} 与 {later.start} 之间只隔 {gap} 个交易日，"
                f"需要 {embargo_sessions} 个（否则前段的标签会看到后段的价格）"
            )


def assert_no_shuffle(sample_dates: Sequence[date], split: HoldoutSplit) -> None:
    """逐样本核对归属：任何一个样本落错段，都说明切分被打乱过。"""
    for day in sample_dates:
        hits = [
            name
            for name, rng in (
                ("train", split.train),
                ("validation", split.validation),
                ("test", split.test),
            )
            if rng.contains(day)
        ]
        if len(hits) > 1:
            raise SplitError(f"样本 {day} 同时落入 {hits} —— 切分重叠")


def make_holdout_split(
    sessions: Sequence[date],
    *,
    embargo_sessions: int,
    validation_fraction: float = 0.15,
    test_fraction: float = 0.20,
    min_train_sessions: int = 250,
) -> HoldoutSplit:
    """按时间顺序切成 train | embargo | validation | embargo | test。"""
    ordered = sorted(set(sessions))
    total = len(ordered)
    if not 0 < validation_fraction < 1 or not 0 < test_fraction < 1:
        raise SplitError("validation_fraction / test_fraction 必须在 (0,1) 之间")
    if validation_fraction + test_fraction >= 1:
        raise SplitError("验证+测试占比必须小于 1")

    test_len = max(1, int(total * test_fraction))
    valid_len = max(1, int(total * validation_fraction))
    train_len = total - test_len - valid_len - 2 * embargo_sessions
    if train_len < min_train_sessions:
        raise SplitError(
            f"交易日总数 {total} 不足以切分：训练段只剩 {train_len} 个交易日，"
            f"少于要求的 {min_train_sessions} 个（数据不足时不启用模型，spec §9.3）"
        )

    train = DateRange(ordered[0], ordered[train_len - 1])
    valid_start = train_len + embargo_sessions
    validation = DateRange(ordered[valid_start], ordered[valid_start + valid_len - 1])
    test_start = valid_start + valid_len + embargo_sessions
    test = DateRange(ordered[test_start], ordered[-1])

    assert_time_ordered(train, validation, test, embargo_sessions=embargo_sessions, sessions=ordered)
    return HoldoutSplit(
        train=train, validation=validation, test=test, embargo_sessions=embargo_sessions
    )


def make_walk_forward_folds(
    sessions: Sequence[date],
    *,
    embargo_sessions: int,
    n_folds: int = 5,
    min_train_sessions: int = 250,
    validation_sessions: int = 60,
) -> list[Fold]:
    """expanding-window walk-forward（spec §9.3：禁止随机切分）。

    第 i 折：训练 = [起点, split_i)，验证 = [split_i + embargo, +validation_sessions)。
    训练窗口逐折扩张，验证窗口向前滚动，永远不回头看。
    """
    ordered = sorted(set(sessions))
    total = len(ordered)
    if n_folds < 1:
        raise SplitError("n_folds 至少为 1")
    needed = min_train_sessions + n_folds * (validation_sessions + embargo_sessions)
    if total < needed:
        raise SplitError(
            f"交易日总数 {total} 不足以做 {n_folds} 折 walk-forward（至少需要 {needed} 个）"
        )

    folds: list[Fold] = []
    # 让最后一折的验证段刚好顶到序列末尾，训练段从头开始扩张
    span = total - min_train_sessions - embargo_sessions - validation_sessions
    step = span // max(1, n_folds - 1) if n_folds > 1 else 0
    for i in range(n_folds):
        train_end_idx = min_train_sessions - 1 + i * step
        valid_start_idx = train_end_idx + 1 + embargo_sessions
        valid_end_idx = valid_start_idx + validation_sessions - 1
        if valid_end_idx >= total:
            break
        train = DateRange(ordered[0], ordered[train_end_idx])
        validation = DateRange(ordered[valid_start_idx], ordered[valid_end_idx])
        assert_time_ordered(
            train, validation, embargo_sessions=embargo_sessions, sessions=ordered
        )
        folds.append(
            Fold(index=i, train=train, validation=validation, embargo_sessions=embargo_sessions)
        )
    if not folds:
        raise SplitError("没有生成任何 walk-forward 折")
    return folds
