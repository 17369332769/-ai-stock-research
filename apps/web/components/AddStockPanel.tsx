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
  addedSymbol?: string | null;
  onResetError?: () => void;
}

/**
 * 添加额外自选。当前沪深300已自动进入研究池，只允许添加其他本地已知 A 股。
 */
export function AddStockPanel({
  onAdd,
  addError,
  adding,
  addedSymbol = null,
  onResetError,
}: AddStockPanelProps) {
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
  const directSymbol = /^\d{6}$/.test(query.trim()) ? query.trim() : null;

  return (
    <div className="add-stock" data-testid="add-stock-panel">
      <form className="add-stock__form" onSubmit={handleSearch} role="search">
        <label className="field">
          <span className="field__label">按代码或名称搜索额外关注股票</span>
          <input
            className="field__input"
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              onResetError?.();
            }}
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
        <div data-testid="search-empty">
          <p className="empty-hint">
            {directSymbol
              ? '本地尚无这只股票的名称，可先按代码加入；名称将在后续基础信息同步后更新。'
              : '没有匹配的本地已知股票。可输入完整6位股票代码直接加入。'}
          </p>
          {directSymbol ? (
            <button
              type="button"
              className="btn btn--primary"
              disabled={adding || addedSymbol === directSymbol}
              onClick={() => onAdd(directSymbol)}
              data-testid={`add-${directSymbol}`}
            >
              {addedSymbol === directSymbol ? '已加入我的关注' : '按代码加入我的关注'}
            </button>
          ) : null}
        </div>
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
              {instrument.is_current_universe_member ? (
                <span className="badge badge--ok">已在沪深300研究池</span>
              ) : null}
              <button
                type="button"
                className="btn btn--primary"
                disabled={adding || instrument.is_current_universe_member || addedSymbol === instrument.symbol}
                onClick={() => onAdd(instrument.symbol)}
                data-testid={`add-${instrument.symbol}`}
              >
                {instrument.is_current_universe_member
                  ? '无需添加'
                  : addedSymbol === instrument.symbol
                    ? '已加入我的关注'
                    : '加入我的关注'}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
