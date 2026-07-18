'use client';

import { DatabaseOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons';
import { Button, Card, Descriptions, Space, Tag, Typography } from 'antd';

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
  ok: 'success', pending: 'default', degraded: 'warning', failed: 'error',
};

const MODEL_TONE: Record<ModelConnectionStatus, string> = {
  active: 'success', degraded: 'warning', unavailable: 'error',
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
      <div className="page-heading"><div>
        <Typography.Text className="page-kicker">运行透明度</Typography.Text>
        <Typography.Title className="page-title">系统状态</Typography.Title>
        <Typography.Paragraph className="page-subtitle">免费数据源不保证交易所级实时性。数据源连续失败后进入降级状态，此处展示具体失败源与最后成功时间。</Typography.Paragraph>
      </div></div>

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
          <Button icon={<ReloadOutlined />} onClick={reload} loading={loading} data-testid="status-refresh">刷新</Button>
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
              <Card
                key={source.key}
                className="status-card"
                data-testid="data-source-card"
                data-source-key={source.key}
                data-status={source.status}
                title={<Space><DatabaseOutlined /><span className="status-card__name">{source.name}</span></Space>}
                extra={<Tag color={SOURCE_TONE[source.status]} data-testid="source-status">{DATA_SOURCE_STATUS_LABELS[source.status]}</Tag>}
              >
                <Descriptions size="small" column={1} items={[
                  { key: 'source', label: '实际来源', children: <span data-testid="source-active-source"><SourceDisplay source={source.active_source} /></span> },
                  { key: 'coverage', label: '数据覆盖', children: <span data-testid="source-coverage">{source.coverage}/{source.total}</span> },
                  { key: 'success', label: '最后成功时间', children: <span data-testid="source-last-success">{formatDateTime(source.last_success_at)}</span> },
                  { key: 'next', label: '下次运行', children: <span data-testid="source-next-run">{formatDateTime(source.next_run_at)}</span> },
                  { key: 'jobs', label: '采集作业', children: source.job_count },
                  { key: 'failures', label: '连续失败次数', children: <span data-testid="source-failures">{source.consecutive_failures}</span> },
                  ...(source.failing_jobs.length > 0 ? [{ key: 'failing', label: '异常作业', children: <span data-testid="source-failing-jobs">{source.failing_jobs.join('、')}</span> }] : []),
                  ...(source.last_error_code ? [{ key: 'error', label: '失败原因', children: <span data-testid="source-error">{source.last_error_code}{source.last_error_message ? `：${source.last_error_message}` : ''}</span> }] : []),
                ]} />
              </Card>
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
              <Card
                key={model.model_key}
                className="status-card"
                data-testid="model-card"
                data-model-key={model.model_key}
                data-status={model.status}
                title={<Space><RobotOutlined /><span className="status-card__name">{model.model_key}</span></Space>}
                extra={<Space><Tag color={MODEL_TONE[model.status]} data-testid="model-status">{MODEL_STATUS_LABELS[model.status]}</Tag>{model.better_than_baseline === false ? <Tag color="warning" data-testid="model-baseline-flag">未优于基准</Tag> : null}</Space>}
              >
                <Descriptions size="small" column={1} items={[
                  { key: 'version', label: '活跃版本', children: <span data-testid="model-version">{model.active_version ?? '—'}</span> },
                  { key: 'prediction', label: '最近预测', children: formatDateTime(model.last_prediction_at) },
                  ...(model.reason ? [{ key: 'reason', label: '说明', children: <span data-testid="model-reason">{model.reason}</span> }] : []),
                ]} />
              </Card>
            ))}
          </div>
        )}
      </Section>

      <Section id="agent" title="分析 Agent 连接状态">
        {!agent ? (
          <p className="empty-hint">暂无 Agent 记录。</p>
        ) : (
          <Card className="status-card" data-testid="agent-card" data-status={agent.status} title={<Space><RobotOutlined />{agent.provider ?? '未配置供应商'} / {agent.model_name ?? '未配置模型'}</Space>} extra={<Tag color={MODEL_TONE[agent.status]} data-testid="agent-status">{MODEL_STATUS_LABELS[agent.status]}</Tag>}>
            <Descriptions size="small" column={1} items={[{ key: 'success', label: '最后成功时间', children: formatDateTime(agent.last_success_at) }, ...(agent.reason ? [{ key: 'reason', label: '说明', children: agent.reason }] : [])]} />
          </Card>
        )}
      </Section>
    </div>
  );
}
