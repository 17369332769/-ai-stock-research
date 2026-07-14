"""测试夹具（spec §16.1 确定性测试策略）。

- 时间用 ``FixedClock`` 注入，**不依赖运行机器当前日期**；
- 交易日历用 ``StaticTradingCalendar`` 夹具，覆盖 09:44 / 09:45 / 11:30 / 13:00 / 15:00 /
  节假日 / 跨年第 5 个交易日；
- API 测试用 ``httpx.ASGITransport`` 直连 ASGI app，**不起网络监听、不访问公网**；
- 相似行情等外部算法端口注入确定性 stub。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from apps.api.app.api.v1.deps import get_analog_finder
from apps.api.app.core.clock import SHANGHAI, FixedClock
from apps.api.app.core.db import get_session
from apps.api.app.core.enums import (
    CSI300_BENCHMARK_SYMBOL,
    CSI300_CODE,
    ConfidenceLabel,
    JobStatus,
    JobType,
    ModelStatus,
    PredictionHorizon,
)
from apps.api.app.core.runtime import reset_runtime, set_clock, set_trading_calendar
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from apps.api.app.main import create_app
from apps.api.app.models.base import Base
from apps.api.app.models.tables import (
    Instrument,
    Job,
    ModelVersion,
    Prediction,
    PredictionOutcome,
    Quote,
    Universe,
    UniverseMembership,
)
from apps.api.app.services.ports import AnalogFinder
from services.prediction.analogs.finder import Analog, AnalogResult

# ── 固定时间锚点 ──────────────────────────────────────────────────────────────
# 2026-07-14 是星期二（交易日）。spec 的示例响应也用这一天。
TRADING_DAY = date(2026, 7, 14)
HOLIDAY = date(2026, 7, 15)  # 夹具里人为设为节假日：用来证明"第 5 个交易日"不按自然日算

AT_0944 = datetime(2026, 7, 14, 9, 44, tzinfo=SHANGHAI)  # 今日预测尚不可用
AT_0945 = datetime(2026, 7, 14, 9, 45, tzinfo=SHANGHAI)  # 今日预测最早可用
AT_0950 = datetime(2026, 7, 14, 9, 50, tzinfo=SHANGHAI)  # spec 示例时间
AT_1130 = datetime(2026, 7, 14, 11, 30, tzinfo=SHANGHAI)
AT_1300 = datetime(2026, 7, 14, 13, 0, tzinfo=SHANGHAI)
AT_1500 = datetime(2026, 7, 14, 15, 0, tzinfo=SHANGHAI)

SYMBOL = "600519"  # 贵州茅台
OTHER_SYMBOL = "000001"


def build_sessions() -> list[date]:
    """测试交易日历：2025-01-01 ~ 2027-12-31 的工作日，扣掉两个人造节假日。

    - ``HOLIDAY``(2026-07-15) 用于验证"第 5 个后续交易日"跳过节假日；
    - 2027-01-01 用于跨年场景。
    """
    holidays = {HOLIDAY, date(2027, 1, 1)}
    sessions: list[date] = []
    day = date(2025, 1, 1)
    end = date(2027, 12, 31)
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


@pytest.fixture
def calendar() -> StaticTradingCalendar:
    return StaticTradingCalendar(build_sessions())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(AT_0950)


@pytest.fixture(autouse=True)
def runtime(clock: FixedClock, calendar: StaticTradingCalendar) -> Iterator[FixedClock]:
    """全局注入时钟与日历；每个测试结束后复位，避免测试之间互相污染。"""
    set_clock(clock)
    set_trading_calendar(calendar)
    yield clock
    reset_runtime()


# ── 数据库（集成/API 测试）───────────────────────────────────────────────────
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@127.0.0.1:5432/app_test"
)


@pytest.fixture(scope="session")
def database_ready() -> bool:
    """建表一次。

    刻意用同步 fixture + ``asyncio.run``：pytest-asyncio 的 loop scope 是 function，
    session 级的 async fixture 会把引擎绑到一个随后就被关掉的事件循环上。

    连接不上时**跳过**（而不是伪装通过）：CI 里 docker-compose 提供 PostgreSQL，
    本地没起库时这些用例会显式 skip 并说明原因。
    """

    async def prepare() -> None:
        eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await eng.dispose()

    try:
        asyncio.run(prepare())
    except Exception as exc:  # pragma: no cover - 本地无库
        pytest.skip(f"测试数据库不可用（{TEST_DATABASE_URL}）：{type(exc).__name__}: {exc}")
    return True


@pytest_asyncio.fixture
async def engine(database_ready: bool) -> AsyncIterator[AsyncEngine]:
    """每个用例一个引擎（NullPool）—— 连接不会跨事件循环复用。"""
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """每个用例一个事务，结束后整体回滚 —— 用例之间完全隔离。

    Session 绑在一个**已经开启事务**的连接上，因此路由里的 ``session.commit()``
    只是释放 SAVEPOINT（join_transaction_mode 默认 conditional_savepoint），
    外层事务仍由这里统一回滚。
    """
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    db = maker()
    try:
        yield db
    finally:
        await db.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


# ── 相似行情端口的确定性 stub（spec §16.1 允许固定 Stub）────────────────────
# 注意：stub 返回**真实的** AnalogResult/Analog 类型（services/prediction 的领域对象），
# 不另造一套影子结构 —— 否则测试通过了，真实类型对不上照样线上炸。
class StubAnalogFinder:
    def __init__(self, *, candidates_valid: int = 120, hits: int = 3) -> None:
        self.candidates_valid = candidates_valid
        self.hits = hits

    async def __call__(
        self,
        db: AsyncSession,
        *,
        symbol: str,
        horizon: str,
        as_of: datetime,
        limit: int,
    ) -> AnalogResult:
        return AnalogResult(
            symbol=symbol,
            horizon=horizon,
            as_of=as_of,
            feature_set_version="v1",
            model_key="a_share_5d_lightgbm",
            model_version="2026.07.14.1",
            candidates_considered=self.candidates_valid + 10,
            candidates_valid=self.candidates_valid,
            analogs=tuple(
                Analog(
                    session=date(2025, 3, 3 + i),
                    distance=0.1 * (i + 1),
                    features={"mom_5": 0.01 * (i + 1), "vol_20": 0.2},
                    forward_return_1d=0.002 * (i + 1),
                    forward_return_5d=0.01 * (i + 1),
                )
                for i in range(min(self.hits, limit))
            ),
        )


@pytest.fixture
def analog_finder() -> StubAnalogFinder:
    return StubAnalogFinder()


@pytest.fixture
def app(session: AsyncSession, analog_finder: AnalogFinder) -> FastAPI:
    application = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    application.dependency_overrides[get_session] = _session_override
    application.dependency_overrides[get_analog_finder] = lambda: analog_finder
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """ASGI 直连，不经网络（测试禁止访问公网）。

    ``raise_app_exceptions=False``：Starlette 的 ServerErrorMiddleware 在调用 500 处理器
    之后**仍会重新抛出**异常。不关掉这个开关，断言"未捕获异常 ⇒ 500 且不泄漏堆栈"的用例
    就会收到异常而不是响应。
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


# ── 建数据的小工具 ───────────────────────────────────────────────────────────
async def seed_universe(session: AsyncSession, now: datetime) -> None:
    session.add(
        Universe(
            code=CSI300_CODE,
            name="沪深300",
            benchmark_symbol=CSI300_BENCHMARK_SYMBOL,
            source="csindex",
            source_url="https://www.csindex.com.cn/",
            snapshot_at=now,
        )
    )
    await session.flush()


async def seed_instrument(
    session: AsyncSession,
    now: datetime,
    symbol: str = SYMBOL,
    name: str = "贵州茅台",
    exchange: str = "SSE",
) -> Instrument:
    row = Instrument(
        symbol=symbol,
        exchange=exchange,
        name=name,
        industry="白酒",
        listed_at=date(2001, 8, 27),
        active=True,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_membership(
    session: AsyncSession,
    now: datetime,
    symbol: str = SYMBOL,
    effective_from: date = date(2020, 1, 1),
    effective_to: date | None = None,
) -> UniverseMembership:
    row = UniverseMembership(
        universe_code=CSI300_CODE,
        symbol=symbol,
        effective_from=effective_from,
        effective_to=effective_to,
        source="csindex",
        source_url="https://www.csindex.com.cn/",
        observed_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_quote(
    session: AsyncSession,
    observed_at: datetime,
    symbol: str = SYMBOL,
    price: str = "1215.04",
    previous_close: str = "1211.05",
) -> Quote:
    row = Quote(
        symbol=symbol,
        observed_at=observed_at,
        price=Decimal(price),
        previous_close=Decimal(previous_close),
        open=Decimal("1212.00"),
        high=Decimal("1220.00"),
        low=Decimal("1208.00"),
        volume=Decimal("31000"),
        amount=Decimal("376000000"),
        volume_ratio=Decimal("1.12"),
        source="eastmoney_via_akshare",
        source_url="https://quote.eastmoney.com/",
        raw_payload={"src": "test"},
    )
    session.add(row)
    await session.flush()
    return row


async def seed_model_version(
    session: AsyncSession,
    *,
    model_key: str = "a_share_5d_lightgbm",
    version: str = "2026.07.14.1",
    horizon: PredictionHorizon = PredictionHorizon.NEXT_5D,
    status: ModelStatus = ModelStatus.ACTIVE,
    better_than_baseline: bool = False,
    metrics: dict[str, Any] | None = None,
) -> ModelVersion:
    validation_metrics: dict[str, Any] = {
        "better_than_baseline": better_than_baseline,
        "baseline_direction_accuracy": 0.52,
        "baseline_mae": 0.019,
        "baseline_brier_score": 0.250,
    }
    if metrics is not None:
        validation_metrics = metrics
    row = ModelVersion(
        id=uuid.uuid4(),
        model_key=model_key,
        version=version,
        target_horizon=horizon.value,
        feature_schema={"features": ["mom_5"]},
        train_start=date(2022, 1, 1),
        train_end=date(2026, 6, 30),
        validation_metrics=validation_metrics,
        artifact_uri=f"file:///models/{model_key}/{version}",
        status=status.value,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_prediction(
    session: AsyncSession,
    model: ModelVersion,
    *,
    symbol: str = SYMBOL,
    horizon: PredictionHorizon = PredictionHorizon.NEXT_5D,
    as_of: datetime = AT_0950,
    target_at: datetime | None = None,
    probability_up: str = "0.38",
    expected_return: str = "-0.011",
    lower_return: str = "-0.041",
    upper_return: str = "0.019",
    confidence: ConfidenceLabel = ConfidenceLabel.LOW,
) -> Prediction:
    row = Prediction(
        id=uuid.uuid4(),
        symbol=symbol,
        model_version_id=model.id,
        horizon=horizon.value,
        as_of=as_of,
        target_at=target_at or (as_of + timedelta(days=7)),
        reference_price=Decimal("1215.04"),
        probability_up=Decimal(probability_up),
        expected_return=Decimal(expected_return),
        lower_return=Decimal(lower_return),
        upper_return=Decimal(upper_return),
        confidence_label=confidence.value,
        data_cutoff=as_of,
        features_snapshot={"mom_5": 0.01},
    )
    session.add(row)
    await session.flush()
    return row


async def seed_outcome(
    session: AsyncSession,
    prediction: Prediction,
    *,
    actual_return: str = "0.012",
    direction_correct: bool = False,
    absolute_error: str = "0.023",
    settled_at: datetime = AT_1500,
) -> PredictionOutcome:
    row = PredictionOutcome(
        prediction_id=prediction.id,
        actual_price=Decimal("1229.62"),
        actual_return=Decimal(actual_return),
        direction_correct=direction_correct,
        absolute_error=Decimal(absolute_error),
        settled_at=settled_at,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_job(
    session: AsyncSession,
    *,
    symbol: str = SYMBOL,
    job_type: JobType = JobType.INSTRUMENT_BACKFILL,
    status: JobStatus = JobStatus.RUNNING,
    idempotency_key: str | None = None,
) -> Job:
    row = Job(
        id=uuid.uuid4(),
        job_type=job_type.value,
        symbol=symbol,
        status=status.value,
        completed_steps=1,
        total_steps=3,
        current_step="minute_bars",
        warnings=[],
        idempotency_key=idempotency_key or f"{job_type.value}:{symbol}",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row
