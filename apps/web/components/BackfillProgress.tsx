import { BACKFILL_STEP_LABELS } from '@/lib/constants';
import { BACKFILL_STEPS, type BackfillStep, type JobDTO } from '@/lib/api/types';
import { Alert, Progress, Steps, Typography } from 'antd';

export interface BackfillProgressProps {
  job: JobDTO;
}

function stepLabel(step: string): string {
  return (BACKFILL_STEP_LABELS as Record<string, string>)[step] ?? step;
}

/**
 * 首次回补进度（spec §7.1）：固定三步 daily_bars → minute_bars → documents。
 * 回补未完成时不显示预测（spec §3.1）——由调用方按 initial_backfill 状态控制。
 */
export function BackfillProgress({ job }: BackfillProgressProps) {
  const total = job.total_steps > 0 ? job.total_steps : BACKFILL_STEPS.length;
  const completed = Math.min(job.completed_steps, total);
  const percent = Math.round((completed / total) * 100);

  return (
    <div className="backfill" data-testid="backfill-progress" data-job-status={job.status}>
      <div className="backfill__head">
        <Typography.Text strong data-testid="backfill-step-counter">
          回补进度 {completed}/{total}
        </Typography.Text>
        <Typography.Text type="secondary" className="backfill__current" data-testid="backfill-current-step">
          {job.current_step ? `当前：${stepLabel(job.current_step)}` : '等待开始'}
        </Typography.Text>
      </div>
      <Progress percent={percent} status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : 'active'} aria-label="首次回补进度" />
      <Steps
        size="small"
        current={Math.min(completed, BACKFILL_STEPS.length - 1)}
        status={job.status === 'failed' ? 'error' : job.status === 'succeeded' ? 'finish' : 'process'}
        items={BACKFILL_STEPS.map((step: BackfillStep, index) => {
          const done = index < completed;
          const active = job.current_step === step && !done;
          const state = done ? 'done' : active ? 'active' : 'pending';
          return { title: <span data-step={step} data-step-state={state}>{stepLabel(step)}</span> };
        })}
      />

      {job.warnings && job.warnings.length > 0 ? (
        <Alert type="warning" showIcon title="回补提示" description={job.warnings.join('；')} data-testid="backfill-warnings" />
      ) : null}

      {job.status === 'failed' ? (
        <Alert type="error" showIcon data-testid="backfill-error" title={`回补失败${job.error_code ? `（${job.error_code}）` : ''}`} description={job.error_message} />
      ) : null}
    </div>
  );
}
