import { FormEvent, useEffect, useRef, useState } from 'react';
import { Bot, CheckCircle2, FileText, Link2, MessageSquarePlus, Plus, Send, ShieldCheck, Sparkles } from 'lucide-react';
import {
  copilotService,
  type CopilotApproval,
  type CopilotConversation,
  type CopilotMessage,
  type CopilotArtifact,
  type CopilotArtifactKind,
  type CopilotArtifactRevision,
  type CopilotEligibility,
  type CopilotWorkspace,
} from '@/services/copilotService';

interface StrategyOption {
  id: string;
  name: string;
}

export function StrategyCopilot({ strategies }: { strategies: StrategyOption[] }) {
  const [workspaces, setWorkspaces] = useState<CopilotWorkspace[]>([]);
  const [workspace, setWorkspace] = useState<CopilotWorkspace | null>(null);
  const [conversation, setConversation] = useState<CopilotConversation | null>(null);
  const [conversations, setConversations] = useState<CopilotConversation[]>([]);
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [artifacts, setArtifacts] = useState<CopilotArtifact[]>([]);
  const [reviewState, setReviewState] = useState<Record<string, { revision: CopilotArtifactRevision; decision?: CopilotApproval }>>({});
  const [eligibility, setEligibility] = useState<CopilotEligibility | null>(null);
  const [artifactKind, setArtifactKind] = useState<CopilotArtifactKind>('specification');
  const [artifactTitle, setArtifactTitle] = useState('Strategy specification');
  const [artifactContent, setArtifactContent] = useState('');
  const [approvalReason, setApprovalReason] = useState('Reviewed and ready for the next advisory stage.');
  const [title, setTitle] = useState('New strategy idea');
  const [strategyId, setStrategyId] = useState('');
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const selectionVersion = useRef(0);

  useEffect(() => {
    void refreshWorkspaces();
  }, []);

  async function refreshWorkspaces() {
    try {
      const data = await copilotService.listWorkspaces();
      setWorkspaces(data.workspaces);
      if (!workspace && data.workspaces.length) await selectWorkspace(data.workspaces[0]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load Copilot workspaces');
    }
  }

  async function selectWorkspace(next: CopilotWorkspace) {
    const version = ++selectionVersion.current;
    setWorkspace(next);
    setConversation(null);
    setConversations([]);
    setMessages([]);
    setArtifacts([]);
    setEligibility(null);
    setError(null);
    setLoadingWorkspace(true);
    try {
      const [data, loadedArtifacts, lifecycle] = await Promise.all([copilotService.listConversations(next.id), copilotService.listArtifacts(next.id), copilotService.lifecycle(next.id)]);
      let active = data.conversations[0];
      if (!active) active = (await copilotService.createConversation(next.id)).conversation;
      if (version !== selectionVersion.current) return;
      setConversations(data.conversations.length ? data.conversations : [active]);
      setArtifacts(loadedArtifacts.artifacts);
      await refreshReviewState(loadedArtifacts.artifacts);
      setEligibility(lifecycle.eligibility);
      await selectConversation(active, version);
    } catch (reason) {
      if (version === selectionVersion.current) {
        setError(reason instanceof Error ? reason.message : 'Could not load workspace');
      }
    } finally {
      if (version === selectionVersion.current) setLoadingWorkspace(false);
    }
  }

  async function refreshReviewState(items = artifacts) {
    const nextReviewState: Record<string, { revision: CopilotArtifactRevision; decision?: CopilotApproval }> = {};
    await Promise.all(items.map(async (artifact) => {
      const [revisions, approvals] = await Promise.all([
        copilotService.listRevisions(artifact.id),
        copilotService.listApprovals(artifact.id),
      ]);
      const revision = revisions.revisions.find((item) => item.revision === artifact.current_revision);
      if (!revision) return;
      nextReviewState[artifact.id] = {
        revision,
        decision: approvals.approvals.find((item) => item.artifact_revision_id === revision.id),
      };
    }));
    setReviewState(nextReviewState);
  }

  async function selectConversation(next: CopilotConversation, version = ++selectionVersion.current) {
    setConversation(next);
    setMessages([]);
    setError(null);
    setLoadingWorkspace(true);
    try {
      const loadedMessages = (await copilotService.listMessages(next.id)).messages;
      if (version !== selectionVersion.current) return;
      setMessages(loadedMessages);
    } catch (reason) {
      if (version === selectionVersion.current) {
        setError(reason instanceof Error ? reason.message : 'Could not load conversation');
      }
    } finally {
      if (version === selectionVersion.current) setLoadingWorkspace(false);
    }
  }

  async function createConversation() {
    if (!workspace) return;
    setBusy(true);
    setError(null);
    try {
      const created = await copilotService.createConversation(
        workspace.id,
        `Discussion ${conversations.length + 1}`,
      );
      setConversations((current) => [created.conversation, ...current]);
      await selectConversation(created.conversation);
      await refreshWorkspaces();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create conversation');
    } finally {
      setBusy(false);
    }
  }

  async function createWorkspace(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await copilotService.createWorkspace(title.trim(), strategyId);
      setWorkspaces((current) => [created.workspace, ...current]);
      await selectWorkspace(created.workspace);
      setTitle('New strategy idea');
      setStrategyId('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create workspace');
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!conversation || !draft.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await copilotService.createMessage(conversation.id, draft.trim());
      setMessages((current) => [...current, saved.message, saved.acknowledgement]);
      setDraft('');
      await refreshWorkspaces();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save message');
    } finally {
      setBusy(false);
    }
  }

  async function createArtifact(event: FormEvent) {
    event.preventDefault();
    if (!workspace || !artifactTitle.trim() || !artifactContent.trim()) return;
    setBusy(true); setError(null);
    try {
      const created = await copilotService.createArtifact(workspace.id, artifactKind, artifactTitle.trim(), artifactContent.trim());
      const nextArtifacts = [created.artifact, ...artifacts]; setArtifacts(nextArtifacts);
      await refreshReviewState(nextArtifacts);
      setArtifactContent('');
      const lifecycle = await copilotService.lifecycle(workspace.id); setEligibility(lifecycle.eligibility);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save artifact'); } finally { setBusy(false); }
  }

  async function decideCurrentArtifact(artifact: CopilotArtifact, decision: CopilotApproval['decision']) {
    if (!workspace) return;
    setBusy(true); setError(null);
    try {
      const current = reviewState[artifact.id]?.revision;
      if (!current) throw new Error('Current artifact revision was not found');
      await copilotService.decideRevision(artifact.id, current.id, decision, approvalReason.trim());
      await refreshReviewState();
      const lifecycle = await copilotService.lifecycle(workspace.id); setEligibility(lifecycle.eligibility);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not approve artifact'); } finally { setBusy(false); }
  }

  async function advanceLifecycle() {
    if (!workspace) return;
    setBusy(true); setError(null);
    try {
      const result = await copilotService.advanceLifecycle(workspace.id);
      setWorkspace(result.workspace); setWorkspaces((current) => current.map((item) => item.id === result.workspace.id ? result.workspace : item));
      const lifecycle = await copilotService.lifecycle(workspace.id); setEligibility(lifecycle.eligibility);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not advance lifecycle'); } finally { setBusy(false); }
  }

  return (
    <section className="mb-8 overflow-hidden rounded-2xl border border-cyan-400/20 bg-[var(--trader-panel)] shadow-2xl shadow-cyan-950/20">
      <div className="border-b border-white/8 bg-gradient-to-r from-cyan-400/10 via-transparent to-blue-500/10 p-5 sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex gap-3">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-cyan-400 text-slate-950"><Sparkles /></span>
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-cyan-300">Phase O1 · durable workspace</p>
              <h2 className="mt-1 text-2xl font-bold text-white">Build with Copilot</h2>
              <p className="mt-2 max-w-2xl text-sm text-slate-400">Capture strategy ideas and link research to an existing strategy. Messages are saved for a future Supervisor; no model, agent command, or trading action runs in this slice.</p>
            </div>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 bg-emerald-400/10 px-3 py-1.5 text-xs font-semibold text-emerald-300"><ShieldCheck className="h-4 w-4" /> Advisory only</span>
        </div>
      </div>

      <div className="grid lg:grid-cols-[290px_minmax(0,1fr)]">
        <aside className="border-b border-white/8 bg-[var(--trader-panel-muted)] p-4 lg:border-b-0 lg:border-r">
          <form onSubmit={createWorkspace} className="space-y-3 rounded-xl border border-white/8 bg-white/[0.025] p-4">
            <label className="text-xs font-bold uppercase tracking-wider text-slate-400">New workspace</label>
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} aria-label="Workspace title" className="w-full rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2.5 text-sm text-white" />
            <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)} aria-label="Linked strategy" className="w-full rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2.5 text-sm text-white">
              <option value="">Unlinked idea</option>
              {strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}
            </select>
            <button disabled={busy || !title.trim()} className="flex w-full items-center justify-center gap-2 rounded-lg bg-cyan-400 px-3 py-2.5 text-sm font-bold text-slate-950 disabled:opacity-50"><MessageSquarePlus className="h-4 w-4" /> Create workspace</button>
          </form>

          <div className="mt-4 space-y-2">
            {workspaces.map((item) => (
              <button key={item.id} type="button" onClick={() => void selectWorkspace(item)} disabled={loadingWorkspace || busy} className={`w-full rounded-xl border p-3 text-left transition disabled:cursor-wait disabled:opacity-60 ${workspace?.id === item.id ? 'border-cyan-400/35 bg-cyan-400/10' : 'border-white/8 bg-white/[0.02] hover:bg-white/[0.05]'}`}>
                <span className="block truncate text-sm font-semibold text-white">{item.title}</span>
                <span className="mt-1 flex items-center gap-1.5 text-xs text-slate-500"><Link2 className="h-3 w-3" /> {item.strategy_id ? 'Strategy linked' : 'Unlinked idea'} · {item.lifecycle}</span>
              </button>
            ))}
            {!workspaces.length && <p className="px-2 py-5 text-center text-sm text-slate-500">Create your first idea workspace.</p>}
          </div>

          {workspace && <div className="mt-5 border-t border-white/8 pt-4">
            <div className="mb-2 flex items-center justify-between gap-2"><span className="text-xs font-bold uppercase tracking-wider text-slate-400">Discussions</span><button type="button" onClick={() => void createConversation()} disabled={busy || loadingWorkspace} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-cyan-300 hover:bg-cyan-400/10 disabled:opacity-50"><Plus className="h-3.5 w-3.5" /> New</button></div>
            <div className="space-y-1">{conversations.map((item) => <button key={item.id} type="button" onClick={() => void selectConversation(item)} disabled={busy || loadingWorkspace} className={`w-full truncate rounded-lg px-3 py-2 text-left text-xs transition disabled:opacity-50 ${conversation?.id === item.id ? 'bg-cyan-400/10 font-semibold text-cyan-200' : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'}`}>{item.title}</button>)}</div>
          </div>}
        </aside>

        <div className="flex min-h-[430px] flex-col">
          {workspace && !loadingWorkspace && <section className="border-b border-white/8 bg-white/[0.02] p-4 sm:p-5" aria-label="Artifact review workflow">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-wider text-cyan-300">O1b review workflow</p><p className="mt-1 text-sm text-slate-400">Lifecycle: <strong className="text-white">{workspace.lifecycle}</strong>{eligibility?.target ? ` → ${eligibility.target}` : ''}</p></div>{eligibility && <button type="button" onClick={() => void advanceLifecycle()} disabled={!eligibility.eligible || busy} className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-3 py-2 text-sm font-bold text-slate-950 disabled:opacity-40"><CheckCircle2 className="h-4 w-4" /> Advance</button>}</div>
            {eligibility && !eligibility.eligible && <p className="mt-2 text-xs text-amber-300">{eligibility.reason}</p>}
            <form onSubmit={createArtifact} className="mt-4 grid gap-2 sm:grid-cols-2"><select value={artifactKind} onChange={(event) => setArtifactKind(event.target.value as CopilotArtifactKind)} aria-label="Artifact kind" className="rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2 text-sm text-white"><option value="specification">Specification</option><option value="strategy_draft">Strategy draft</option></select><input value={artifactTitle} onChange={(event) => setArtifactTitle(event.target.value)} aria-label="Artifact title" maxLength={120} className="rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2 text-sm text-white" /><textarea value={artifactContent} onChange={(event) => setArtifactContent(event.target.value)} aria-label="Artifact content" placeholder="Document the proposal for human review…" rows={2} maxLength={50000} className="sm:col-span-2 rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2 text-sm text-white" /><button disabled={busy || !artifactContent.trim()} className="rounded-lg border border-cyan-400/35 px-3 py-2 text-sm font-semibold text-cyan-200 disabled:opacity-40"><FileText className="mr-1 inline h-4 w-4" /> Save immutable revision</button></form>
            <div className="mt-3 space-y-2">{artifacts.map((artifact) => {
              const review = reviewState[artifact.id];
              return <div key={artifact.id} className="rounded-lg border border-white/8 px-3 py-2 text-sm"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-slate-200">{artifact.title} <span className="text-slate-500">· {artifact.kind.replaceAll('_', ' ')} · rev {artifact.current_revision}</span></span><div className="flex gap-2"><button type="button" onClick={() => void decideCurrentArtifact(artifact, 'approved')} disabled={busy || approvalReason.trim().length < 3 || !review} className="rounded-md bg-emerald-400/15 px-2 py-1 text-xs font-semibold text-emerald-200 hover:bg-emerald-400/25 disabled:opacity-40">Approve</button><button type="button" onClick={() => void decideCurrentArtifact(artifact, 'rejected')} disabled={busy || approvalReason.trim().length < 3 || !review} className="rounded-md bg-rose-400/10 px-2 py-1 text-xs font-semibold text-rose-200 hover:bg-rose-400/20 disabled:opacity-40">Reject</button></div></div>{review && <p className="mt-1 text-xs text-slate-500">Current hash: <code>{review.revision.content_hash.slice(0, 12)}…</code> · latest decision: <strong className={review.decision?.decision === 'approved' ? 'text-emerald-300' : review.decision?.decision === 'rejected' ? 'text-rose-300' : 'text-amber-300'}>{review.decision?.decision ?? 'pending'}</strong>{review.decision ? ` — ${review.decision.reason}` : ''}</p>}</div>;
            })}</div>
            {artifacts.length > 0 && <input value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} aria-label="Approval reason" maxLength={2000} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2 text-xs text-white" />}
          </section>}
          <div className="flex-1 space-y-4 overflow-y-auto p-5 sm:p-7">
            {!workspace ? (
              <div className="grid h-full min-h-64 place-items-center text-center"><div><Bot className="mx-auto h-10 w-10 text-cyan-300" /><p className="mt-3 font-semibold text-white">Select or create a workspace</p><p className="mt-1 text-sm text-slate-500">Your durable conversation will appear here.</p></div></div>
            ) : loadingWorkspace ? (
              <div className="grid h-full min-h-64 place-items-center text-sm text-slate-500">Loading workspace…</div>
            ) : messages.length ? messages.map((message) => (
              <div key={message.id} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm sm:max-w-[75%] ${message.role === 'user' ? 'bg-cyan-400 text-slate-950' : 'border border-white/8 bg-slate-950/35 text-slate-300'}`}>
                  <p>{message.content}</p>
                  <p className={`mt-2 text-[11px] ${message.role === 'user' ? 'text-slate-700' : 'text-slate-500'}`}>{message.status.replaceAll('_', ' ')}</p>
                </div>
              </div>
            )) : (
              <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-slate-500">Describe the hypothesis, market, timeframe, and risk constraints you want the future Supervisor to review.</div>
            )}
          </div>
          {error && <p className="mx-5 mb-3 rounded-lg border border-red-400/20 bg-red-400/10 px-3 py-2 text-sm text-red-300 sm:mx-7">{error}</p>}
          <form onSubmit={sendMessage} className="border-t border-white/8 p-4 sm:p-5">
            <div className="flex gap-3">
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!conversation || busy || loadingWorkspace} maxLength={10000} rows={2} aria-label="Copilot message" placeholder="Describe a strategy idea…" className="min-h-12 flex-1 resize-none rounded-xl border border-white/10 bg-slate-950/40 px-4 py-3 text-sm text-white placeholder:text-slate-600" />
              <button disabled={!conversation || !draft.trim() || busy || loadingWorkspace} aria-label="Save message" className="self-stretch rounded-xl bg-cyan-400 px-4 text-slate-950 disabled:opacity-40"><Send className="h-5 w-5" /></button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}