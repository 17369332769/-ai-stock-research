'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BarChartOutlined, DatabaseOutlined, FundProjectionScreenOutlined, RobotOutlined } from '@ant-design/icons';
import { Badge, Button, Menu, Space, Typography } from 'antd';

const NAV = [
  { href: '/', label: '研究池', icon: <FundProjectionScreenOutlined /> },
  { href: '/scorecard', label: '模型表现', icon: <BarChartOutlined /> },
] as const;

export function AppNavigation() {
  const pathname = usePathname();
  const activeKey = pathname.startsWith('/scorecard') ? '/scorecard' : '/';
  return (
    <>
      <Link href="/" className="app-header__brand">
        <span className="app-header__brand-mark" aria-hidden="true"><RobotOutlined /></span>
        <span className="app-header__brand-copy">
          <Typography.Text strong>A股 AI 研究助手</Typography.Text>
          <Typography.Text type="secondary">证据优先 · 本地研究</Typography.Text>
        </span>
      </Link>
      <nav className="app-header__nav" aria-label="主导航">
        <Menu
          mode="horizontal"
          selectedKeys={[activeKey]}
          items={NAV.map((item) => ({
            key: item.href,
            icon: item.icon,
            label: <Link href={item.href} aria-current={activeKey === item.href ? 'page' : undefined}>{item.label}</Link>,
          }))}
        />
      </nav>
      <Space className="app-header__status">
        <Badge status="processing" />
        <Link href="/settings/data-sources" aria-current={pathname.startsWith('/settings/data-sources') ? 'page' : undefined}>
          <Button type={pathname.startsWith('/settings/data-sources') ? 'primary' : 'text'} icon={<DatabaseOutlined />}>
            系统状态
          </Button>
        </Link>
      </Space>
    </>
  );
}
