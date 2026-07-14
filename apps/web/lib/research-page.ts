/**
 * 研究页数据装配。
 *
 * 每个区块独立捕获错误：一个数据源失败不得让整页空白
 * （spec §15：外部行情源连续失败时，已有历史页面仍可访问）。
 */

import {
  getAnalogs,
  getAnalyses,
  getDocuments,
  getLatestPrediction,
  getScorecard,
  getSnapshot,
} from './api/endpoints';
import type {
  AnalogDTO,
  AnalysisDTO,
  DocumentDTO,
  JobDTO,
  PredictionDTO,
  PredictionHorizon,
  ScorecardDTO,
  SnapshotDTO,
} from './api/types';
import { errorMessage } from './error-messages';
import { isApiError } from './api/client';
import { mapErrorToState, type UiState } from './ui-state';

export const HORIZONS: PredictionHorizon[] = ['today_close', 'next_5d'];

export interface PredictionSlot {
  horizon: PredictionHorizon;
  prediction: PredictionDTO | null;
  /** 无预测时的状态：initial_backfill / model_unavailable / no_prediction / provider_failed。 */
  state: UiState | null;
  message: string | null;
  /** 202 时返回的回补作业（spec §7）。 */
  job: JobDTO | null;
}

export interface ResearchPageData {
  snapshot: SnapshotDTO | null;
  snapshotState: UiState | null;
  snapshotMessage: string | null;
  backfillJob: JobDTO | null;

  anomalies: AnalysisDTO[];
  anomalyMessage: string | null;

  predictions: PredictionSlot[];

  documents: DocumentDTO[];
  documentAnalyses: AnalysisDTO[];
  documentsMessage: string | null;

  analogs: AnalogDTO[];
  analogsInsufficient: boolean;
  analogsMessage: string | null;

  scorecards: ScorecardDTO[];
  scorecardMessage: string | null;
}

async function loadSnapshot(symbol: string): Promise<{
  snapshot: SnapshotDTO | null;
  state: UiState | null;
  message: string | null;
  job: JobDTO | null;
}> {
  try {
    const response = await getSnapshot(symbol);
    // 202：回补仍在进行，body 为作业状态（spec §7）。
    if (response.status === 202) {
      const job = response.data as unknown as JobDTO;
      return { snapshot: null, state: 'initial_backfill', message: null, job };
    }
    const snapshot = response.data;
    return {
      snapshot,
      state: null,
      message: null,
      job: snapshot.backfill_job ?? null,
    };
  } catch (error) {
    return {
      snapshot: null,
      state: mapErrorToState(error) ?? 'provider_failed',
      message: errorMessage(error),
      job: null,
    };
  }
}

async function loadPrediction(
  symbol: string,
  horizon: PredictionHorizon,
): Promise<PredictionSlot> {
  try {
    const response = await getLatestPrediction(symbol, horizon);
    if (response.status === 202) {
      return {
        horizon,
        prediction: null,
        state: 'initial_backfill',
        message: '数据回补完成前不生成预测。',
        job: response.data as unknown as JobDTO,
      };
    }
    return { horizon, prediction: response.data, state: null, message: null, job: null };
  } catch (error) {
    return {
      horizon,
      prediction: null,
      state: mapErrorToState(error) ?? 'no_prediction',
      message: errorMessage(error),
      job: null,
    };
  }
}

/** 历史相似行情：有效候选少于30个时 API 返回 422（spec §10）。 */
async function loadAnalogs(symbol: string): Promise<{
  analogs: AnalogDTO[];
  insufficient: boolean;
  message: string | null;
}> {
  try {
    const response = await getAnalogs(symbol, 'next_5d', 10);
    return { analogs: response.items, insufficient: false, message: null };
  } catch (error) {
    const insufficient = isApiError(error) && error.code === 'INSUFFICIENT_DATA';
    return { analogs: [], insufficient, message: errorMessage(error) };
  }
}

async function loadScorecards(models: string[]): Promise<{
  scorecards: ScorecardDTO[];
  message: string | null;
}> {
  if (models.length === 0) return { scorecards: [], message: null };

  const settled = await Promise.all(
    models.map(async (modelKey) => {
      try {
        return await getScorecard(modelKey, '100');
      } catch {
        return null;
      }
    }),
  );

  const scorecards = settled.filter((item): item is ScorecardDTO => item !== null);
  return {
    scorecards,
    message: scorecards.length === 0 ? '暂无模型成绩数据。' : null,
  };
}

export async function loadResearchPage(symbol: string): Promise<ResearchPageData> {
  const [snapshotResult, anomalyResult, predictionSlots, documentsResult, documentAnalysesResult, analogsResult] =
    await Promise.all([
      loadSnapshot(symbol),
      getAnalyses(symbol, 'anomaly')
        .then((response) => ({ items: response.items, message: null as string | null }))
        .catch((error: unknown) => ({ items: [] as AnalysisDTO[], message: errorMessage(error) })),
      Promise.all(HORIZONS.map((horizon) => loadPrediction(symbol, horizon))),
      getDocuments(symbol)
        .then((response) => ({ items: response.items, message: null as string | null }))
        .catch((error: unknown) => ({ items: [] as DocumentDTO[], message: errorMessage(error) })),
      getAnalyses(symbol, 'document')
        .then((response) => ({ items: response.items, message: null as string | null }))
        .catch(() => ({ items: [] as AnalysisDTO[], message: null as string | null })),
      loadAnalogs(symbol),
    ]);

  const modelKeys = [
    ...new Set(
      predictionSlots
        .map((slot) => slot.prediction?.model.key)
        .filter((key): key is string => typeof key === 'string'),
    ),
  ];
  const scorecardResult = await loadScorecards(modelKeys);

  return {
    snapshot: snapshotResult.snapshot,
    snapshotState: snapshotResult.state,
    snapshotMessage: snapshotResult.message,
    backfillJob: snapshotResult.job ?? predictionSlots.find((slot) => slot.job)?.job ?? null,

    anomalies: anomalyResult.items,
    anomalyMessage: anomalyResult.message,

    predictions: predictionSlots,

    documents: documentsResult.items,
    documentAnalyses: documentAnalysesResult.items,
    documentsMessage: documentsResult.message,

    analogs: analogsResult.analogs,
    analogsInsufficient: analogsResult.insufficient,
    analogsMessage: analogsResult.message,

    scorecards: scorecardResult.scorecards,
    scorecardMessage: scorecardResult.message,
  };
}
