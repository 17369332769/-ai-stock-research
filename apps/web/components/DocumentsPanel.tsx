'use client';

import { useState } from 'react';
import { FileTextOutlined } from '@ant-design/icons';
import { Button, List, Pagination, Segmented, Space, Tag, Typography } from 'antd';

import { DOCUMENT_TYPE_LABELS } from '@/lib/constants';
import { formatDateTime } from '@/lib/format';
import { documentSourceName } from '@/lib/source-display';
import type { AnalysisDTO, DocumentDTO, DocumentType } from '@/lib/api/types';
import { AnalysisCard } from './AnalysisCard';
import { EmptyHint } from './Section';
import { StateNotice } from './StateNotice';

export interface DocumentsPanelProps {
  documents: DocumentDTO[];
  /** 文档解读（analysis_type=document），按 evidence.document_id 归属到文档下。 */
  analyses: AnalysisDTO[];
}

type Filter = 'all' | DocumentType;

const PAGE_SIZE = 20;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'announcement', label: '公告' },
  { key: 'news', label: '新闻' },
];

/** 公告与新闻（spec §3.2 第三段）。无文档时展示"无文档"状态。 */
export function DocumentsPanel({ documents, analyses }: DocumentsPanelProps) {
  const [filter, setFilter] = useState<Filter>('all');
  const [page, setPage] = useState(1);

  if (documents.length === 0) {
    return <StateNotice state="no_documents" detail="当前没有公告或新闻记录。" />;
  }

  const visible =
    filter === 'all' ? documents : documents.filter((doc) => doc.document_type === filter);
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageDocuments = visible.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const analysisByDocument = new Map<string, AnalysisDTO>();
  for (const analysis of analyses) {
    for (const item of analysis.evidence) {
      if (!analysisByDocument.has(item.document_id)) {
        analysisByDocument.set(item.document_id, analysis);
      }
    }
  }

  return (
    <div data-testid="documents-panel">
      <Segmented className="filter-row" value={filter} onChange={(value) => { setFilter(value as Filter); setPage(1); }} options={FILTERS.map((item) => ({ value: item.key, label: <span role="button" tabIndex={-1} data-testid={`documents-filter-${item.key}`} aria-pressed={filter === item.key}>{item.label}</span> }))} />

      {visible.length === 0 ? (
        <EmptyHint>该类型下暂无文档。</EmptyHint>
      ) : (
        <List className="documents" data-testid="documents-list" dataSource={pageDocuments} renderItem={(doc) => {
            const analysis = analysisByDocument.get(doc.id);
            return (
              <List.Item key={doc.id} className="documents__item" data-document-id={doc.id}>
                <div className="documents__head">
                  <Tag icon={<FileTextOutlined />}>
                    {DOCUMENT_TYPE_LABELS[doc.document_type]}
                  </Tag>
                  <a
                    className="documents__title"
                    href={doc.source_url}
                    target="_blank"
                    rel="noreferrer"
                    data-testid="document-link"
                  >
                    {doc.title}
                    <span className="sr-only">（将在新窗口打开）</span>
                  </a>
                </div>
                <Space className="documents__meta"><Typography.Text type="secondary">{documentSourceName(doc.source)}</Typography.Text><Typography.Text type="secondary">{formatDateTime(doc.published_at)}</Typography.Text></Space>
                {analysis ? (
                  <AnalysisCard analysis={analysis} />
                ) : (
                  <p className="empty-hint" data-testid="document-no-analysis">
                    尚未生成摘要。
                  </p>
                )}
              </List.Item>
            );
          }} />
      )}

      {visible.length > PAGE_SIZE ? (
        <nav className="pagination" aria-label="公告新闻分页" data-testid="documents-pagination">
          <Button
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={currentPage === 1}
            data-testid="documents-page-prev"
          >
            上一页
          </Button>
          <span data-testid="documents-page-indicator">
            第 {currentPage} 页，共 {totalPages} 页
          </span>
          <Pagination current={currentPage} total={visible.length} pageSize={PAGE_SIZE} showSizeChanger={false} onChange={setPage} />
          <Button
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            disabled={currentPage === totalPages}
            data-testid="documents-page-next"
          >
            下一页
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
