"""参考价（spec §7.4）。

- ``today_close``：**固定为昨收**。所以同一天 09:45 / 10:00 / 14:45 的多个版本
  预测的是同一个目标（当日收盘相对昨收），概率变化可以横向比较。
- ``next_5d``：各自 ``as_of`` 时点的**最新有效价**（盘中报价 → 分钟线 → 最近收盘价）。

复权基准的坑（"复权边界"）：
    ``bars`` 是 qfq（前复权）序列，而 ``quotes.price`` 是**未复权**的实时价。
    如果直接拿未复权的参考价，和结算时读到的（可能因为期间除权而被重新缩放的）复权收盘价相除，
    收益率会在除权日附近整个错掉。

    解法：参考价除了存"给人看的价格"，还要存一个**复权基准锚点**：
      anchor_session          —— 锚定的交易日
      anchor_close_at_as_of   —— 该交易日在 **as_of 当时** 的复权收盘价
      price_on_as_of_basis    —— 参考价换算到 as_of 当时的复权基准上

    结算时（``evaluation/settlement.py``）：
      rescale = 现在读到的 anchor_session 复权收盘价 / anchor_close_at_as_of
      参考价（现基准） = price_on_as_of_basis × rescale
      实际收益 = 目标日复权收盘价 / 参考价（现基准） - 1

    这样无论期间发生几次除权，收益率都算在同一个基准上。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.errors import InsufficientData
from services.prediction.features.panel import PitPanel
from services.prediction.features.repository import QuoteSnapshot

__all__ = ["ReferencePrice", "resolve_reference_price"]


@dataclass(frozen=True, slots=True)
class ReferencePrice:
    price: float  # 写进 predictions.reference_price（给人看的价格）
    source: str  # 'previous_close' | 'quote' | 'minute_bar' | 'last_close'
    anchor_session: date
    anchor_close_at_as_of: float
    price_on_as_of_basis: float
    intraday_anchor: bool  # next_5d 用了盘中价（训练锚点是收盘价，见模型卡的限制说明）

    def to_json(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "source": self.source,
            "anchor_session": self.anchor_session.isoformat(),
            "anchor_close_at_as_of": self.anchor_close_at_as_of,
            "price_on_as_of_basis": self.price_on_as_of_basis,
            "intraday_anchor": self.intraday_anchor,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReferencePrice:
        return cls(
            price=float(data["price"]),
            source=str(data["source"]),
            anchor_session=date.fromisoformat(data["anchor_session"]),
            anchor_close_at_as_of=float(data["anchor_close_at_as_of"]),
            price_on_as_of_basis=float(data["price_on_as_of_basis"]),
            intraday_anchor=bool(data["intraday_anchor"]),
        )


def resolve_reference_price(
    *,
    horizon: str,
    panel: PitPanel,
    quote: QuoteSnapshot | None,
    as_of: datetime,
) -> ReferencePrice:
    last_close = panel.last_close
    last_session = panel.last_session
    if last_close is None or last_session is None or last_close <= 0:
        raise InsufficientData(f"{panel.symbol} 没有可用的已收盘日线，无法确定参考价")

    if horizon == PredictionHorizon.TODAY_CLOSE:
        # 固定为昨收。昨收本身就在复权序列上，锚点即自身。
        return ReferencePrice(
            price=last_close,
            source="previous_close",
            anchor_session=last_session,
            anchor_close_at_as_of=last_close,
            price_on_as_of_basis=last_close,
            intraday_anchor=False,
        )

    if horizon != PredictionHorizon.NEXT_5D:
        raise ValueError(f"未知 horizon：{horizon!r}")

    # next_5d：as_of 时点的最新有效价
    if quote is not None and quote.price > 0 and quote.observed_at <= as_of:
        if quote.previous_close <= 0:
            raise InsufficientData(f"{panel.symbol} 的报价昨收为 0，无法换算复权基准")
        # 报价是未复权价；用「同一天的未复权昨收」与「复权昨收」的比值换算到复权基准
        factor = last_close / quote.previous_close
        return ReferencePrice(
            price=quote.price,
            source="quote",
            anchor_session=last_session,
            anchor_close_at_as_of=last_close,
            price_on_as_of_basis=quote.price * factor,
            intraday_anchor=True,
        )

    if panel.minute:
        # 分钟线与日线同复权基准（repository 已校验），可直接用
        price = panel.minute[-1].close
        if price > 0:
            return ReferencePrice(
                price=price,
                source="minute_bar",
                anchor_session=last_session,
                anchor_close_at_as_of=last_close,
                price_on_as_of_basis=price,
                intraday_anchor=True,
            )

    # 盘后 / 无盘中数据：用最近收盘价。这与训练锚点一致，是最"正"的一种。
    return ReferencePrice(
        price=last_close,
        source="last_close",
        anchor_session=last_session,
        anchor_close_at_as_of=last_close,
        price_on_as_of_basis=last_close,
        intraday_anchor=False,
    )
