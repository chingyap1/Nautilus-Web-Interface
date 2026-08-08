import type { ReactNode } from 'react';
import {
  Activity,
  ClipboardCheck,
  Gauge,
  ShieldAlert,
  UserRound,
  type LucideIcon,
} from 'lucide-react';
import { Link, useLocation } from 'wouter';

type Tab = {
  label: string;
  href: string;
  icon: LucideIcon;
};

/** Bottom-tab IA from Mobile Ops brief §6 — hard boundary from TraderLayout. */
const TABS: Tab[] = [
  { label: 'Status', href: '/m/status', icon: Gauge },
  { label: 'Approvals', href: '/m/approvals', icon: ClipboardCheck },
  { label: 'Controls', href: '/m/controls', icon: ShieldAlert },
  { label: 'Activity', href: '/m/activity', icon: Activity },
  { label: 'Account', href: '/m/account', icon: UserRound },
];

function isActive(location: string, href: string): boolean {
  return location === href || location.startsWith(`${href}/`);
}

interface MobileOpsLayoutProps {
  children: ReactNode;
}

export default function MobileOpsLayout({ children }: MobileOpsLayoutProps) {
  const [location] = useLocation();

  return (
    <div className="mobile-ops-theme min-h-dvh bg-[var(--mops-canvas)] text-[var(--mops-text)]">
      <div className="mx-auto flex min-h-dvh max-w-lg flex-col md:max-w-3xl lg:max-w-5xl">
        <header className="sticky top-0 z-20 border-b border-[var(--mops-border)] bg-[var(--mops-canvas)]/95 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] backdrop-blur-md">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[var(--mops-muted)]">
                Trading plane
              </p>
              <h1 className="mt-0.5 text-xl font-semibold tracking-tight text-white">
                Mobile Ops
              </h1>
            </div>
            <span
              className="rounded-md border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.16em] text-emerald-300"
              title="v1 is paper trading only"
            >
              Paper
            </span>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-4 py-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))]">
          {children}
        </main>

        <nav
          aria-label="Mobile Ops"
          className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--mops-border)] bg-[var(--mops-panel)]/95 backdrop-blur-md"
        >
          <div className="mx-auto grid max-w-lg grid-cols-5 gap-1 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 md:max-w-3xl lg:max-w-5xl">
            {TABS.map(({ label, href, icon: Icon }) => {
              const active = isActive(location, href);
              return (
                <Link
                  aria-current={active ? 'page' : undefined}
                  className={`flex min-h-11 flex-col items-center justify-center gap-0.5 rounded-lg px-1 py-1.5 text-[10px] font-medium transition-colors ${
                    active
                      ? 'bg-sky-400/10 text-sky-200'
                      : 'text-[var(--mops-muted)] active:bg-white/5'
                  }`}
                  href={href}
                  key={href}
                >
                  <Icon className="h-5 w-5" aria-hidden />
                  <span>{label}</span>
                </Link>
              );
            })}
          </div>
        </nav>
      </div>
    </div>
  );
}
