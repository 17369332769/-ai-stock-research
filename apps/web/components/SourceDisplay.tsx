import { sourcePresentation } from '@/lib/source-display';

export interface SourceDisplayProps {
  source: string;
  sourceUrl?: string | null;
  dataType?: string;
}

export function SourceDisplay({ source, sourceUrl, dataType }: SourceDisplayProps) {
  const presentation = sourcePresentation(source);
  const name = sourceUrl ? (
    <a href={sourceUrl} target="_blank" rel="noreferrer">
      {presentation.name}
    </a>
  ) : (
    presentation.name
  );

  return (
    <div className="source-display" data-testid="source-display" data-source-known={presentation.known}>
      <strong className="source-display__name">{name}</strong>
      <span className="source-display__detail">
        {[dataType, presentation.detail].filter(Boolean).join(' · ')}
      </span>
      <details className="source-display__technical">
        <summary>查看技术信息</summary>
        <code>{source}</code>
      </details>
    </div>
  );
}
