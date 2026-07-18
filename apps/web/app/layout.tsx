import type { Metadata } from 'next';
import { AntdRegistry } from '@ant-design/nextjs-registry';

import { AppNavigation } from '@/components/AppNavigation';
import { AppProviders } from '@/components/AppProviders';
import { RESEARCH_ONLY_DISCLAIMER } from '@/lib/constants';
import './globals.css';

export const metadata: Metadata = {
  title: 'A股 AI 研究助手',
  description: '沪深300研究池行情、公告解读、概率预测与模型表现（仅供研究）',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <AppProviders>
            <a className="skip-link" href="#main-content">跳到主要内容</a>
            <header className="app-header">
              <div className="app-header__inner">
                <AppNavigation />
                <span className="app-header__note" data-testid="global-disclaimer">
                  {RESEARCH_ONLY_DISCLAIMER}
                </span>
              </div>
            </header>
            <main className="app-main" id="main-content" tabIndex={-1}>{children}</main>
            <footer className="app-footer">
              <div className="app-footer__inner">
                <span className="app-footer__brand">A股 AI 研究助手</span>
                <p>本工具只做研究记录与复盘，不提供交易功能，也不承诺任何收益。免费数据源不保证交易所级实时性。</p>
              </div>
            </footer>
          </AppProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}
