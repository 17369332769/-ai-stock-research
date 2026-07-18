import type { ReactNode } from 'react';
import { Card, Empty, Space, Typography } from 'antd';

export interface SectionProps {
  id: string;
  title: string;
  /** 研究页信息顺序（spec §3.2）用序号明示，防止后续被打乱。 */
  order?: number;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}

export function Section({ id, title, order, subtitle, action, children }: SectionProps) {
  return (
    <section className="section" id={id} data-section={id} data-order={order}>
      <Card
        title={(
          <Space align="start" size={12}>
            {order ? <span className="section__order" aria-hidden="true">{String(order).padStart(2, '0')}</span> : null}
            <span className="section__heading-copy">
              <Typography.Title level={2} className="section__title">{title}</Typography.Title>
              {subtitle ? <Typography.Text type="secondary" className="section__subtitle">{subtitle}</Typography.Text> : null}
            </span>
          </Space>
        )}
        extra={action}
      >
        <div className="section__body">{children}</div>
      </Card>
    </section>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={children} className="empty-hint" />;
}
