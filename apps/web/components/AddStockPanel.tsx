'use client';

import { useState, type FormEvent } from 'react';

import { searchInstruments } from '@/lib/api/endpoints';
import { errorCodeOf, errorMessage } from '@/lib/error-messages';
import type { InstrumentDTO } from '@/lib/api/types';

export interface AddStockPanelProps {
  /** 由页面负责调用 POST /watchlist 并处理 202 回补作业。 */
  onAdd: (symbol: string) => Promise<void>;
  /** 添加失败时页面回传的错误（409 非成分股 / 409 重复）。 */
  addError?: unknown;
  adding?: boolean;
}

/**
 * 添加自选股（spec §3.1）。
 * 搜索只返回查询日沪深300当前成分（由 API 保证）；
 * 非成分股与重复添加由 API 分别返回 409 NOT_CURRENT_UNIVERSE_MEMBER / DUPLICATE_WATCHLIST_ITEM。
 */
export function AddStockPanel({ onAdd, addError, adding }: AddStockPanelProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<InstrumentDTO[]>([]);
  const [searchError, setSearchError] = useState<unknown>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    const keyword = query.trim();
    if (keyword.length === 0) return;

    setSearching(true);
    setSearchError(null);
    try {
      const response = await searchInstruments(keyword);
      setResults(response.items);
    } catch (error) {
      setResults([]);
      setSearchError(error);
    } finally {
      setSearching(false);
      setSearched(true);
    }
  }

  const addErrorCode = errorCodeOf(addError);

  return (
    <div className="add-stock" data-testid="add-stock-panel">
      <form className="add-stock__form" onSubmit={handleSearch} role="search">
        <label className="field">
          <span className="field__label">按代码或名称搜索沪深300成分股</span>
          <input
            className="field__input"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如 600519 或 贵州茅台"
            data-testid="instrument-search-input"
          />
        </label>
        <button
          type="submit"
          className="btn btn--primary"
          disabled={searching || query.trim().length === 0}
          data-testid="instrument-search-submit"
        >
          {searching ? '搜索中…' : '搜索'}
        </button>
      </form>

      {searchError ? (
        <p className="form-error" role="alert" data-testid="search-error">
          {errorMessage(searchError)}
        </p>
      ) : null}

      {addError ? (
        <p
          className="form-error"
          role="alert"
          data-testid="add-error"
          data-error-code={addErrorCode ?? 'UNKNOWN'}
        >
          {errorMessage(addError)}
        </p>
      ) : null}

      {searched && !searching && results.length === 0 && !searchError ? (
        <p className="empty-hint" data-testid="search-empty">
          没有匹配的当前沪深300成分股。非成分股不可添加。
        </p>
      ) : null}

      {results.length > 0 ? (
        <ul className="add-stock__results" data-testid="instrument-search-results">
          {results.map((instrument) => (
            <li key={instrument.symbol} className="add-stock__result" data-symbol={instrument.symbol}>
              <span className="add-stock__symbol">{instrument.symbol}</span>
              <span className="add-stock__name">{instrument.name}</span>
              {instrument.industry ? (
                <span className="add-stock__industry">{instrument.industry}</span>
              ) : null}
              <button
                type="button"
                className="btn btn--primary"
                disabled={adding}
                onClick={() => onAdd(instrument.symbol)}
                data-testid={`add-${instrument.symbol}`}
              >
                加入自选
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
