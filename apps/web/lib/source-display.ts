export interface SourcePresentation {
  name: string;
  detail: string;
  known: boolean;
}

const SOURCE_PRESENTATIONS: Record<string, Omit<SourcePresentation, 'known'>> = {
  sina_via_akshare: {
    name: '新浪财经行情',
    detail: '通过 AKShare 采集',
  },
  eastmoney_via_akshare: {
    name: '东方财富行情',
    detail: '通过 AKShare 采集',
  },
  akshare: {
    name: '行情数据',
    detail: '通过 AKShare 采集',
  },
  cninfo: {
    name: '巨潮资讯',
    detail: '上市公司法定公告',
  },
  cn_disclosure: {
    name: '巨潮资讯',
    detail: '上市公司法定公告',
  },
  eastmoney_news: {
    name: '东方财富新闻',
    detail: '财经新闻聚合',
  },
  csindex: {
    name: '中证指数',
    detail: '沪深 300 成分数据',
  },
  csi300: {
    name: '中证指数',
    detail: '沪深 300 成分数据',
  },
};

export function sourcePresentation(source: string): SourcePresentation {
  const presentation = SOURCE_PRESENTATIONS[source];
  if (presentation) return { ...presentation, known: true };
  return {
    name: '未识别的数据来源',
    detail: '请在技术信息中核对原始标识',
    known: false,
  };
}

/** 文档 source 可能是中文媒体名，也可能是内部采集标识。 */
export function documentSourceName(source: string): string {
  const presentation = sourcePresentation(source);
  if (presentation.known) return presentation.name;
  return /[\u3400-\u9fff]/.test(source) ? source : presentation.name;
}
