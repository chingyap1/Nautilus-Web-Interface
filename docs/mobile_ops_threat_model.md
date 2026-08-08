# Mobile Ops — Threat Model (P5)

Status: **v1 paper hardening**. Companion to Platform Future brief
`agent-remote-dashboard/docs/mobile_trading_operator_design.md` (§2 dual
planes, §8 API, §10.2 auth). Code lives under `frontend/src/mobile/`.

## 1. Scope and non-goals

**In scope:** browser PWA at `/m/*` talking to authenticated NWI APIs for
paper trading ops (status, approvals, emergency controls, activity).

**Out of scope (deferred / forbidden):**

- Live-exchange supervision or live kill/flatten UI.
- Public internet exposure of command APIs (private network / Tailscale for
  reachability only).
- Reusing ARD session APIs, coding adapters, or executor registry.
- Browser-to-model or browser-to-MCP direct connections.
- Treating push delivery as authority (push is attention-only; JWT remains required).

## 2. Assets

| Asset | Why it matters |
|-------|----------------|
| NWI Bearer JWT (`localStorage`) | Authority for paper mutations; theft = operator impersonation until expiry/revoke. |
| Durable paper commands / approvals | Path to agent action; must stay two-step (approve ≠ dispatch) + role/step-up. |
| Supervisor interlock | Blocks new Supervisor traffic; resume is admin-only. |
| Kill / flatten command path | High-impact paper emergency; single-tap must never fire. |
| `execution.mode` | Only paper mutations allowed in v1; confuse with live → wrong plane trust. |
| Audit / observation feed | Operator awareness; gap hides failed emergencies. |
| Web Push subscription rows | Device endpoints for attention pings; not an auth substitute; opt-in per browser. |

## 3. Trust boundaries

```text
Operator device (PWA / Safari)
        │ Tailscale (or equiv) — reachability only, not authority
        ▼
NWI frontend /m/*  (Bearer JWT in localStorage)
        │ same-origin or private ingress → /api/*
        ▼
NWI FastAPI (JWT middleware, role gates, interlock, approvals)
        │ durable command records
        ▼
Paper Nautilus agent + RiskService
```

- **Tailscale identity** never substitutes for NWI Bearer on mutations.
- **Desktop TraderLayout** and **Mobile Ops** share API client + services;
  chrome is hard-separated so coding-plane patterns are not imported.

## 4. STRIDE-style threats and mitigations

### Spoofing
- *Threat:* caller uses stolen JWT or Tailscale-only identity.
- *Mitigation:* all `/api/*` mutations require Bearer JWT; roles enforced
  server-side (`approver`/`admin` for approve; `operator+` engage;
  `admin` resume). Soft logout on 401; logout hits `/api/auth/logout` when
  possible.

### Tampering
- *Threat:* mutate payload after approve, or skip dispatch confirm.
- *Mitigation:* NWI exact-payload approvals; Mobile Ops keeps separate
  Approve → Dispatch clicks; step-up for HIGH/CRITICAL.

### Repudiation
- *Threat:* operator denies kill/flatten/interlock action.
- *Mitigation:* audit feed + durable command records; Controls require
  reason string + second confirm.

### Information disclosure
- *Threat:* live mode mistaken for paper; JWT in XSS.
- *Mitigation:* persistent PAPER badge; Controls/Approvals refuse
  non-paper mutations client-side (server remains authoritative); no
  secrets in Mobile Ops bundle beyond session token hygiene.

### Denial of service
- *Threat:* Supervisor spam or stuck paused interlock.
- *Mitigation:* interlock fail-closed; Status/Activity surface unreachable
  interlock; human emergency kill/flatten remain available while Supervisor
  traffic is paused.

### Elevation of privilege
- *Threat:* viewer triggers Controls; operator resumes interlock.
- *Mitigation:* UI role gates mirror backend; resume admin-only; Approvals
  hide mutate for viewer.

## 5. Session / expiry UX (P5)

| Event | Behavior |
|-------|----------|
| JWT `exp` passed at boot | Clear storage; Login with “Session expired” reason |
| API 401 | Clear storage; stash return path if `/m/*`; Login reason |
| Token expiring soon (&lt;15m) | Banner on Mobile Ops chrome + Account warning |
| Sign out | Prefer `/api/auth/logout` then clear; return to Login |

Deep link: after re-login, restore stashed `/m/*` path when present.

## 6. Residual risks

1. XSS in the NWI SPA can still read Bearer from `localStorage` — same as
   desktop; mitigate with CSP / dependency hygiene (platform-wide).
2. Kill/flatten backend role is still `get_current_user` only — Mobile Ops
   gates to operator+; tighten server role policy remains a tracked gap.
3. Push is opt-in attention only — missed/denied notifications still need
   an open PWA or desktop Supervision; VAPID must be set in production.
4. Gate 5 sustained-observation drills not yet published — Activity hooks
   are derived reads only.

## 7. Live market

Explicitly **deferred**. No live kill/flatten/approve path in Mobile Ops v1.
When live lands, it must be a distinct phase with separate threat review —
not a mode toggle on this shell.
