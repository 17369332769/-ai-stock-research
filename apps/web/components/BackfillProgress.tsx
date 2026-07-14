import { BACKFILL_STEP_LABELS } from '@/lib/constants';
import { BACKFILL_STEPS, type BackfillStep, type JobDTO } from '@/lib/api/types';

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
        <span data-testid="backfill-step-counter">
          回补进度 {completed}/{total}
        </span>
        <span className="backfill__current" data-testid="backfill-current-step">
          {job.current_step ? `当前：${stepLabel(job.current_step)}` : '等待开始'}
        </span>
      </div>

      <div
        className="backfill__bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={completed}
        aria-label="首次回补进度"
      >
        <div className="backfill__bar-fill" style={{ width: `${percent}%` }} />
      </div>

      <ol className="backfill__steps">
        {BACKFILL_STEPS.map((step: BackfillStep, index) => {
          const done = index < completed;
          const active = job.current_step === step && !done;
          const state = done ? 'done' : active ? 'active' : 'pending';
          return (
            <li key={step} data-step={step} data-step-state={state} className={`backfill__step backfill__step--${state}`}>
              {stepLabel(step)}
            </li>
          );
        })}
      </ol>

      {job.warnings && job.warnings.length > 0 ? (
        <ul className="backfill__warnings" data-testid="backfill-warnings">
          {job.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      {job.status === 'failed' ? (
        <p className="backfill__error" data-testid="backfill-error">
          回补失败{job.error_code ? `（${job.error_code}）` : ''}
          {job.error_message ? `：${job.error_message}` : ''}
        </p>
      ) : null}
    </div>
  );
}
