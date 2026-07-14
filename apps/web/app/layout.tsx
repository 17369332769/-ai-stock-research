import type { Metadata } from 'next';
import Link from 'next/link';

import { RESEARCH_ONLY_DISCLAIMER } from '@/lib/constants';
import './globals.css';

export const metadata: Metadata = {
  title: 'A股 AI 研究助手',
  description: '沪深300自选股行情、公告解读、概率预测与预测成绩单（仅供研究）',
};

const NAV = [
  { href: '/', label: '自选股' },
  { href: '/scorecard', label: '预测成绩单' },
  { href: '/settings/data-sources', label: '数据源与模型' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="app-header">
          <Link href="/" className="app-header__brand">
            A股 AI 研究助手
          </Link>
          <nav className="app-header__nav" aria-label="主导航">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="app-header__link">
                {item.label}
              </Link>
            ))}
          </nav>
          <span className="app-header__note" data-testid="global-disclaimer">
            {RESEARCH_ONLY_DISCLAIMER}
          </span>
        </header>
        <main className="app-main">{children}</main>
        <footer className="app-footer">
          <p>
            本工具只做研究记录与复盘，不提供交易功能，也不承诺任何收益。免费数据源不保证交易所级实时性。
          </p>
        </footer>
      </body>
    </html>
  );
}
