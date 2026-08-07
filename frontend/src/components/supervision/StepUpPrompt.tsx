import { useState, useRef, useEffect } from 'react';
import { ShieldCheck, X } from 'lucide-react';

interface StepUpPromptProps {
  onSubmit: (code: string) => void;
  onCancel: () => void;
  loading?: boolean;
  error?: string | null;
}

export default function StepUpPrompt({ onSubmit, onCancel, loading, error }: StepUpPromptProps) {
  const [code, setCode] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length >= 6) {
      onSubmit(code);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-amber-300" />
          <span className="text-xs font-semibold text-amber-200">Step-up authentication required</span>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="text-slate-500 hover:text-slate-300"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <p className="mt-1.5 text-[11px] text-slate-500">
        Enter your 6-digit TOTP authenticator code to authorize this high-risk action.
      </p>
      <form onSubmit={handleSubmit} className="mt-2.5 flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={8}
          placeholder="000000"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
          className="w-32 rounded-lg border border-white/10 bg-[#0b1727] px-3 py-1.5 font-mono text-sm tracking-widest text-slate-200 outline-none focus:border-amber-400/40"
        />
        <button
          type="submit"
          disabled={code.length < 6 || loading}
          className="rounded-lg border border-amber-400/25 bg-amber-400/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? 'Verifying…' : 'Verify'}
        </button>
      </form>
      {error && (
        <p className="mt-2 text-[11px] text-rose-300">{error}</p>
      )}
    </div>
  );
}
