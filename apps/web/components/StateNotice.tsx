import type { ReactNode } from 'react';
import { Alert, Tag } from 'antd';

import { STATE_DESCRIPTORS, type UiState } from '@/lib/ui-state';

export interface StateNoticeProps {
  state: UiState;
  /** 覆盖默认说明文案（例如带上具体失败源、age_seconds）。 */
  detail?: ReactNode;
  action?: ReactNode;
}

/**
 * 九种必须状态的统一展示（spec §13.2）。
 * data-state 属性供 E2E 精确定位。
 */
export function StateNotice({ state, detail, action }: StateNoticeProps) {
  const descriptor = STATE_DESCRIPTORS[state];
  const type = descriptor.tone === 'danger' ? 'error' : descriptor.tone === 'warning' ? 'warning' : 'info';

  return (
    <Alert
      className={`notice notice--${descriptor.tone}`}
      data-state={state}
      data-testid={`state-${state}`}
      role={descriptor.tone === 'danger' ? 'alert' : 'status'}
      type={type}
      showIcon
      title={<strong className="notice__label">{descriptor.label}</strong>}
      description={<span className="notice__detail">{detail ?? descriptor.description}</span>}
      action={action}
    />
  );
}

export interface StateBadgeProps {
  state: UiState;
  title?: string;
}

export function StateBadge({ state, title }: StateBadgeProps) {
  const descriptor = STATE_DESCRIPTORS[state];
  return (
    <Tag
      className={`badge badge--${descriptor.tone}`}
      data-state={state}
      data-testid={`badge-${state}`}
      title={title ?? descriptor.description}
    >{descriptor.label}</Tag>
  );
}

export interface StateNoticeListProps {
  states: UiState[];
  details?: Partial<Record<UiState, ReactNode>>;
  actions?: Partial<Record<UiState, ReactNode>>;
}

/** 多个状态同时成立时全部展示（休市 + 过期不得互相吞掉）。 */
export function StateNoticeList({ states, details, actions }: StateNoticeListProps) {
  if (states.length === 0) return null;
  return (
    <div className="notice-list" data-testid="state-notice-list">
      {states.map((state) => (
        <StateNotice
          key={state}
          state={state}
          detail={details?.[state]}
          action={actions?.[state]}
        />
      ))}
    </div>
  );
}
