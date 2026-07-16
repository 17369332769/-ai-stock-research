'use client';

import { Section } from '@/components/Section';
import { SourceDisplay } from '@/components/SourceDisplay';
import { StateNotice } from '@/components/StateNotice';
import { getSystemStatus } from '@/lib/api/endpoints';
import { DATA_SOURCE_STATUS_LABELS, MODEL_STATUS_LABELS } from '@/lib/constants';
import { formatDateTime } from '@/lib/format';
import { errorMessage } from '@/lib/error-messages';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { mapErrorToState } from '@/lib/ui-state';
import type { DataSourceStatus, ModelConnectionStatus } from '@/lib/api/types';

const SOURCE_TONE: Record<DataSourceStatus, string> = {
  ok: 'badge--ok',
  pending: 'badge--neutral',
  degraded: 'badge--warning',
  failed: 'badge--danger',
};

const MODEL_TONE: Record<ModelConnectionStatus, string> = {
  active: 'badge--ok',
  degraded: 'badge--warning',
  unavailable: 'badge--danger',
};

/**
 * 数据源与模型状态页（spec §13.1 / §8）。
 * 展示具体失败源、最后成功时间和模型连接状态；不静默用缓存冒充新数据。
 */
export default function DataSourcesPage() {
  const { data, error, loading, loaded, reload } = useApiResource(() => getSystemStatus(), []);

  const sources = data?.sources ?? [];
  const models = data?.models ?? [];
  const agent = data?.agent ?? null;

  const failedSources = sources.filter((source) => source.status === 'failed');
  const unavailableModels = models.filter((model) => model.status === 'unavailable');

  return (
    <div data-testid="data-sources-page">
      <h1 className="page-title">系统状态</h1>
      <p className="page-subtitle">
        免费数据源不保证交易所级实时性。数据源连续失败后进入降级状态，此处展示具体失败源与最后成功时间。
      </p>

      {failedSources.length > 0 ? (
        <StateNotice
          state="provider_failed"
          detail={`以下数据源当前失败：${failedSources
            .map((source) => source.name)
            .join('、')}。请查看失败原因与最后成功时间。`}
        />
      ) : null}

      {unavailableModels.length > 0 ? (
        <StateNotice
          state="model_unavailable"
          detail={`以下模型当前不可用：${unavailableModels
            .map((model) => model.model_key)
            .join('、')}。`}
        />
      ) : null}

      <Section
        id="data-sources"
        title="数据源"
        action={
          <button type="button" className="btn" onClick={reload} data-testid="status-refresh">
            刷新
          </button>
        }
      >
        {loading && !loaded ? (
          <p className="empty-hint">加载中…</p>
        ) : error ? (
          <StateNotice
            state={mapErrorToState(error) ?? 'provider_failed'}
            detail={errorMessage(error)}
          />
        ) : sources.length === 0 ? (
          <p className="empty-hint">暂无数据源记录。</p>
        ) : (
          <div className="status-grid" data-testid="data-source-list">
            {sources.map((source) => (
              <article
                key={source.key}
                className="status-card"
                data-testid="data-source-card"
                data-source-key={source.key}
                data-status={source.status}
              >
                <div className="status-card__head">
                  <span className="status-card__name">{source.name}</span>
                  <span className={`badge ${SOURCE_TONE[source.status]}`} data-testid="source-status">
                    {DATA_SOURCE_STATUS_LABELS[source.status]}
                  </span>
                </div>
                <dl>
                  <dt>实际来源</dt>
                  <dd data-testid="source-active-source">
                    <SourceDisplay source={source.active_source} />
                  </dd>
                  <dt>数据覆盖</dt>
                  <dd data-testid="source-coverage">
                    {source.coverage}/{source.total}
                  </dd>
                  <dt>最后成功时间</dt>
                  <dd data-testid="source-last-success">{formatDateTime(source.last_success_at)}</dd>
                  <dt>下次运行</dt>
                  <dd data-testid="source-next-run">{formatDateTime(source.next_run_at)}</dd>
                  <dt>采集作业</dt>
                  <dd>{source.job_count}</dd>
                  <dt>连续失败次数</dt>
                  <dd data-testid="source-failures">{source.consecutive_failures}</dd>
                  {source.failing_jobs.length > 0 ? (
                    <>
                      <dt>异常作业</dt>
                      <dd data-testid="source-failing-jobs">{source.failing_jobs.join('、')}</dd>
                    </>
                  ) : null}
                  {source.last_error_code ? (
                    <>
                      <dt>失败原因</dt>
                      <dd data-testid="source-error">
                        {source.last_error_code}
                        {source.last_error_message ? `：${source.last_error_message}` : ''}
                      </dd>
                    </>
                  ) : null}
                </dl>
              </article>
            ))}
          </div>
        )}
      </Section>

      <Section id="models" title="模型连接状态">
        {models.length === 0 ? (
          <p className="empty-hint">暂无已注册模型。</p>
        ) : (
          <div className="status-grid" data-testid="model-list">
            {models.map((model) => (
              <article
                key={model.model_key}
                className="status-card"
                data-testid="model-card"
                data-model-key={model.model_key}
                data-status={model.status}
              >
                <div className="status-card__head">
                  <span className="status-card__name">{model.model_key}</span>
                  <span className={`badge ${MODEL_TONE[model.status]}`} data-testid="model-status">
                    {MODEL_STATUS_LABELS[model.status]}
                  </span>
                  {model.better_than_baseline === false ? (
                    <span className="badge badge--warning" data-testid="model-baseline-flag">
                      未优于基准
                    </span>
                  ) : null}
                </div>
                <dl>
                  <dt>活跃版本</dt>
                  <dd data-testid="model-version">{model.active_version ?? '—'}</dd>
                  <dt>最近预测</dt>
                  <dd>{formatDateTime(model.last_prediction_at)}</dd>
                  {model.reason ? (
                    <>
                      <dt>说明</dt>
                      <dd data-testid="model-reason">{model.reason}</dd>
                    </>
                  ) : null}
                </dl>
              </article>
            ))}
          </div>
        )}
      </Section>

      <Section id="agent" title="分析 Agent 连接状态">
        {!agent ? (
          <p className="empty-hint">暂无 Agent 记录。</p>
        ) : (
          <div className="status-card" data-testid="agent-card" data-status={agent.status}>
            <div className="status-card__head">
              <span className="status-card__name">
                {agent.provider ?? '未配置供应商'} / {agent.model_name ?? '未配置模型'}
              </span>
              <span className={`badge ${MODEL_TONE[agent.status]}`} data-testid="agent-status">
                {MODEL_STATUS_LABELS[agent.status]}
              </span>
            </div>
            <dl>
              <dt>最后成功时间</dt>
              <dd>{formatDateTime(agent.last_success_at)}</dd>
              {agent.reason ? (
                <>
                  <dt>说明</dt>
                  <dd>{agent.reason}</dd>
                </>
              ) : null}
            </dl>
          </div>
        )}
      </Section>
    </div>
  );
}
