import type { ReactNode } from 'react';
import {
  Activity,
  BarChart3,
  Bell,
  BookOpen,
  Bot,
  FlaskConical,
  Gauge,
  ListOrdered,
  ShieldCheck,
  TerminalSquare,
  WalletCards,
  type LucideIcon,
} from 'lucide-react';
import { useLocation } from 'wouter';

type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
};

const NAVIGATION: Array<{ label: string; items: NavItem[] }> = [
  {
    label: 'Operate',
    items: [
      { label: 'Overview', href: '/trader', icon: Gauge },
      { label: 'Strategies', href: '/trader/strategies', icon: Bot },
      { label: 'Commands & orders', href: '/trader/orders', icon: ListOrdered },
      { label: 'Positions', href: '/trader/positions', icon: WalletCards },
      { label: 'Risk controls', href: '/trader/risk', icon: ShieldCheck },
    ],
  },
  {
    label: 'Observe',
    items: [
      { label: 'Market data', href: '/trader/market-data', icon: Activity },
      { label: 'Performance', href: '/trader/performance', icon: BarChart3 },
      { label: 'Alerts', href: '/trader/alerts', icon: Bell },
    ],
  },
  {
    label: 'Research',
    items: [
      { label: 'Backtesting', href: '/trader/backtesting', icon: FlaskConical },
      { label: 'Documentation', href: '/docs', icon: BookOpen },
    ],
  },
];

const PAGE_TITLES: Record<string, { eyebrow: string; title: string }> = {
  '/trader': { eyebrow: 'Operations', title: 'Execution overview' },
  '/trader/strategies': { eyebrow: 'Operate', title: 'Strategy management' },
  '/trader/orders': { eyebrow: 'Operate', title: 'Commands & orders' },
  '/trader/positions': { eyebrow: 'Operate', title: 'Positions' },
  '/trader/risk': { eyebrow: 'Operate', title: 'Risk controls' },
  '/trader/market-data': { eyebrow: 'Observe', title: 'Market data' },
  '/trader/performance': { eyebrow: 'Observe', title: 'Performance' },
  '/trader/alerts': { eyebrow: 'Observe', title: 'Alerts' },
  '/trader/backtesting': { eyebrow: 'Research', title: 'Backtesting' },
};

function isActiveRoute(location: string, href: string): boolean {
  return href === '/trader' ? location === href : location.startsWith(href);
}

interface TraderLayoutProps {
  children: ReactNode;
}

export default function TraderLayout({ children }: TraderLayoutProps) {
  const [location] = useLocation();
  const page = PAGE_TITLES[location] ?? PAGE_TITLES['/trader'];

  return (
    <div className="trader-theme min-h-screen bg-[var(--trader-canvas)] text-[var(--trader-text)]">
      <div className="mx-auto grid min-h-screen max-w-[1800px] lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="hidden border-r border-white/7 bg-[var(--trader-sidebar)] lg:flex lg:flex-col">
          <a className="flex h-20 items-center gap-3 border-b border-white/7 px-6" href="/trader">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-cyan-400 text-[var(--trader-canvas)] shadow-[0_0_32px_rgba(34,211,238,0.25)]">
              <TerminalSquare className="h-5 w-5" />
            </span>
            <span>
              <span className="block font-semibold tracking-tight text-white">NAUTILUS</span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.2em] text-slate-500">Control plane</span>
            </span>
          </a>

          <nav aria-label="Trader navigation" className="flex-1 space-y-7 px-3 py-6">
            {NAVIGATION.map(section => (
              <div key={section.label}>
                <div className="px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-600">
                  {section.label}
                </div>
                <div className="mt-2 space-y-1">
                  {section.items.map(({ label, href, icon: Icon }) => {
                    const active = isActiveRoute(location, href);
                    return (
                      <a
                        aria-current={active ? 'page' : undefined}
                        className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                          active
                            ? 'bg-cyan-400/10 text-cyan-200 ring-1 ring-cyan-400/15'
                            : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
                        }`}
                        href={href}
                        key={href}
                      >
                        <Icon className="h-4 w-4" />
                        <span>{label}</span>
                      </a>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          <div className="border-t border-white/7 p-4">
            <div className="rounded-xl border border-white/7 bg-white/[0.025] p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                Execution boundary
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
                Trading actions remain inside the authoritative Nautilus agent.
              </p>
            </div>
          </div>
        </aside>

        <main className="min-w-0">
          <header className="sticky top-0 z-20 border-b border-white/7 bg-[var(--trader-sidebar)]/95 px-5 py-4 backdrop-blur-xl sm:px-8 lg:px-10">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                  {page.eyebrow}
                </div>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-white">{page.title}</h1>
              </div>
              <a
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs font-medium text-slate-400 transition-colors hover:bg-white/10 hover:text-white lg:hidden"
                href="/trader"
              >
                Trader home
              </a>
            </div>

            <nav aria-label="Trader mobile navigation" className="mt-4 flex gap-2 overflow-x-auto pb-1 lg:hidden">
              {NAVIGATION.flatMap(section => section.items)
                .filter(item => item.href.startsWith('/trader'))
                .map(({ label, href }) => {
                  const active = isActiveRoute(location, href);
                  return (
                    <a
                      aria-current={active ? 'page' : undefined}
                      className={`shrink-0 rounded-lg px-3 py-2 text-xs font-medium ${
                        active ? 'bg-cyan-400/10 text-cyan-200' : 'bg-white/5 text-slate-500'
                      }`}
                      href={href}
                      key={href}
                    >
                      {label}
                    </a>
                  );
                })}
            </nav>
          </header>

          <div className="trader-page">{children}</div>
        </main>
      </div>
    </div>
  );
}