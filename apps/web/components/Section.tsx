import type { ReactNode } from 'react';

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
      <header className="section__header">
        <div>
          <h2 className="section__title">{title}</h2>
          {subtitle ? <p className="section__subtitle">{subtitle}</p> : null}
        </div>
        {action ? <div className="section__action">{action}</div> : null}
      </header>
      <div className="section__body">{children}</div>
    </section>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <p className="empty-hint">{children}</p>;
}
