import { UNKNOWN_CAUSE_LABEL } from '@/lib/constants';
import { formatDateTime } from '@/lib/format';
import type { EvidenceDTO } from '@/lib/api/types';
import { FileSearchOutlined } from '@ant-design/icons';
import { Empty, List, Typography } from 'antd';

export interface EvidenceListProps {
  evidence: EvidenceDTO[];
}

/**
 * 证据列表（spec §3.2 红线）：每条 AI 结论必须有可点击证据；
 * 没有证据时显示"原因未知"。证据项包含 document_id / title / source_url /
 * published_at / quote，链接直接指向原文（spec §7.3）。
 */
export function EvidenceList({ evidence }: EvidenceListProps) {
  if (evidence.length === 0) {
    return (
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={UNKNOWN_CAUSE_LABEL} className="evidence-empty" data-testid="evidence-unknown" />
    );
  }

  return (
    <List className="evidence" data-testid="evidence-list" dataSource={evidence} renderItem={(item) => (
        <List.Item key={item.document_id} className="evidence__item" data-document-id={item.document_id}>
          <List.Item.Meta avatar={<FileSearchOutlined />} title={
          <a
            className="evidence__link"
            href={item.source_url}
            target="_blank"
            rel="noreferrer"
            data-testid="evidence-link"
          >
            {item.title}
            <span className="sr-only">（将在新窗口打开）</span>
          </a>} description={<>
          <blockquote className="evidence__quote" data-testid="evidence-quote">
            “{item.quote}”
          </blockquote>
          <Typography.Text type="secondary" className="evidence__meta">{formatDateTime(item.published_at)}</Typography.Text>
          </>} />
        </List.Item>
      )} />
  );
}
