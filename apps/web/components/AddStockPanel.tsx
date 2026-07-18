'use client';

import { useState, type FormEvent } from 'react';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Input, List, Space, Tag, Typography } from 'antd';

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
          <Input
            size="large"
            prefix={<SearchOutlined />}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              onResetError?.();
            }}
            placeholder="例如 600519 或 贵州茅台"
            data-testid="instrument-search-input"
            allowClear
          />
        </label>
        <Button
          htmlType="submit"
          type="primary"
          size="large"
          icon={<SearchOutlined />}
          disabled={searching || query.trim().length === 0}
          loading={searching}
          data-testid="instrument-search-submit"
        >
          搜索
        </Button>
      </form>

      {searchError ? (
        <Alert type="error" showIcon title={errorMessage(searchError)} data-testid="search-error" />
      ) : null}

      {addError ? (
        <Alert
          type="error"
          showIcon
          title={errorMessage(addError)}
          data-testid="add-error"
          data-error-code={addErrorCode ?? 'UNKNOWN'}
        />
      ) : null}

      {searched && !searching && results.length === 0 && !searchError ? (
        <div data-testid="search-empty">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={directSymbol
              ? '本地尚无这只股票的名称，可先按代码加入；名称将在后续基础信息同步后更新。'
              : '没有匹配的本地已知股票。可输入完整6位股票代码直接加入。'} />
          {directSymbol ? (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={adding || addedSymbol === directSymbol}
              loading={adding}
              onClick={() => onAdd(directSymbol)}
              data-testid={`add-${directSymbol}`}
            >
              {addedSymbol === directSymbol ? '已加入我的关注' : '按代码加入我的关注'}
            </Button>
          ) : null}
        </div>
      ) : null}

      {results.length > 0 ? (
        <List
          className="add-stock__results"
          data-testid="instrument-search-results"
          dataSource={results}
          renderItem={(instrument) => (
            <List.Item
              className="add-stock__result"
              data-symbol={instrument.symbol}
              actions={[<Button
                key="add"
                type="primary"
                icon={<PlusOutlined />}
                disabled={adding || instrument.is_current_universe_member || addedSymbol === instrument.symbol}
                loading={adding && addedSymbol !== instrument.symbol}
                onClick={() => onAdd(instrument.symbol)}
                data-testid={`add-${instrument.symbol}`}
              >
                {instrument.is_current_universe_member
                  ? '无需添加'
                  : addedSymbol === instrument.symbol
                    ? '已加入我的关注'
                    : '加入我的关注'}
              </Button>]}
            >
              <List.Item.Meta
                title={<Space><Typography.Text strong>{instrument.name}</Typography.Text><Typography.Text code>{instrument.symbol}</Typography.Text></Space>}
                description={<Space wrap>{instrument.industry ? <Tag>{instrument.industry}</Tag> : null}{instrument.is_current_universe_member ? <Tag color="success">已在沪深300研究池</Tag> : null}</Space>}
              />
            </List.Item>
          )}
        />
      ) : null}
    </div>
  );
}
