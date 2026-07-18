'use client';

import { App, ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { ReactNode } from 'react';

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.compactAlgorithm,
        cssVar: {},
        token: {
          colorPrimary: '#0f5fc7',
          colorInfo: '#0f5fc7',
          colorLink: '#0f4cbd',
          colorSuccess: '#00875a',
          colorWarning: '#b76e00',
          colorError: '#cf1322',
          colorText: '#17233c',
          colorTextSecondary: '#4b5870',
          colorBorder: '#d9e2ef',
          colorBgLayout: '#f3f6fb',
          colorBgContainer: '#ffffff',
          borderRadius: 8,
          borderRadiusLG: 12,
          controlHeight: 36,
          controlHeightLG: 44,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: { fontWeight: 600 },
          Card: { headerBg: '#ffffff' },
          Table: {
            headerBg: '#f6f8fc',
            headerColor: '#3d4b63',
            rowHoverBg: '#f5f9ff',
          },
          Menu: {
            itemBg: 'transparent',
            horizontalItemSelectedColor: '#1677ff',
          },
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
