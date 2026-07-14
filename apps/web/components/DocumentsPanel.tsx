'use client';

import { useState } from 'react';

import { DOCUMENT_TYPE_LABELS } from '@/lib/constants';
import { formatDateTime } from '@/lib/format';
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

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'announcement', label: '公告' },
  { key: 'news', label: '新闻' },
];

/** 公告与新闻（spec §3.2 第三段）。无文档时展示"无文档"状态。 */
export function DocumentsPanel({ documents, analyses }: DocumentsPanelProps) {
  const [filter, setFilter] = useState<Filter>('all');

  if (documents.length === 0) {
    return <StateNotice state="no_documents" detail="暂无公告或新闻。数据源恢复后会自动补齐。" />;
  }

  const visible =
    filter === 'all' ? documents : documents.filter((doc) => doc.document_type === filter);

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
      <div className="filter-row" role="group" aria-label="文档类型筛选">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`filter-chip ${filter === item.key ? 'filter-chip--active' : ''}`}
            aria-pressed={filter === item.key}
            onClick={() => setFilter(item.key)}
            data-testid={`documents-filter-${item.key}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <EmptyHint>该类型下暂无文档。</EmptyHint>
      ) : (
        <ul className="documents" data-testid="documents-list">
          {visible.map((doc) => {
            const analysis = analysisByDocument.get(doc.id);
            return (
              <li key={doc.id} className="documents__item" data-document-id={doc.id}>
                <div className="documents__head">
                  <span className="badge badge--neutral">
                    {DOCUMENT_TYPE_LABELS[doc.document_type]}
                  </span>
                  <a
                    className="documents__title"
                    href={doc.source_url}
                    target="_blank"
                    rel="noreferrer"
                    data-testid="document-link"
                  >
                    {doc.title}
                  </a>
                </div>
                <div className="documents__meta">
                  <span>{doc.source}</span>
                  <span>{formatDateTime(doc.published_at)}</span>
                </div>
                {analysis ? (
                  <AnalysisCard analysis={analysis} />
                ) : (
                  <p className="empty-hint" data-testid="document-no-analysis">
                    暂无解读。
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
