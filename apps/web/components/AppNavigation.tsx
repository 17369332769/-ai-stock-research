'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const NAV = [
  { href: '/', label: '研究池' },
  { href: '/scorecard', label: '模型表现' },
] as const;

export function AppNavigation() {
  const pathname = usePathname();
  return (
    <>
      <Link href="/" className="app-header__brand">
        A股 AI 研究助手
      </Link>
      <nav className="app-header__nav" aria-label="主导航">
        {NAV.map((item) => {
          const active = item.href === '/'
            ? pathname === '/' || pathname.startsWith('/stocks/')
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`app-header__link ${active ? 'app-header__link--active' : ''}`}
              aria-current={active ? 'page' : undefined}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <Link
        href="/settings/data-sources"
        className={`app-header__status-link ${pathname.startsWith('/settings/data-sources') ? 'app-header__link--active' : ''}`}
        aria-current={pathname.startsWith('/settings/data-sources') ? 'page' : undefined}
      >
        系统状态
      </Link>
    </>
  );
}
