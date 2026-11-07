import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { CheckCircle2, Loader2, MailWarning, Plane } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { usePageMeta } from "@/lib/page-meta";
import { CONFIRM_PATH, SESSION_WAIT_MS, confirmRedirectUrl } from "@/lib/auth-redirect";

const REDIRECT_SECONDS = 3;

type State =
  | { kind: "checking" }
  | { kind: "confirmed"; name: string }
  | { kind: "error"; code: string; message: string }
  | { kind: "no-session" };

/** Supabase puts errors in the hash (implicit flow) or the query string (PKCE / older links). */
function readUrlError(): { code: string; message: string } | null {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const query = new URLSearchParams(window.location.search);
  const get = (k: string) => hash.get(k) ?? query.get(k);
  const error = get("error");
  if (!error) return null;
  return { code: get("error_code") ?? error, message: get("error_description") ?? error };
}

function errorText(code: string): string {
  if (code === "otp_expired") return "這個確認連結已過期或已經使用過。";
  if (code === "access_denied") return "這個確認連結無法使用。";
  return "驗證時發生問題。";
}

export default function AuthConfirmed() {
  usePageMeta({
    title: "Email 驗證 Confirm email — Flight Price Notifier",
    description: "Confirming your Flight Price Notifier account.",
    twitterCard: "summary",
  });
  const navigate = useNavigate();
  const [state, setState] = useState<State>(() => {
    const err = readUrlError();
    return err ? { kind: "error", ...err } : { kind: "checking" };
  });
  const [seconds, setSeconds] = useState(REDIRECT_SECONDS);

  // Wait for the session that supabase-js reads from the link, then tidy the URL.
  useEffect(() => {
    if (state.kind !== "checking") return;
    let done = false;
    const confirmed = (user: { email?: string; user_metadata?: Record<string, unknown> }) => {
      if (done) return;
      done = true;
      // don't leave tokens sitting in the address bar / history
      window.history.replaceState(null, "", CONFIRM_PATH);
      const name =
        (user.user_metadata?.["display_name"] as string | undefined) ||
        user.email?.split("@")[0] ||
        "";
      setState({ kind: "confirmed", name });
    };
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session?.user) confirmed(session.user);
    });
    supabase.auth.getSession().then(({ data: s }) => {
      if (s.session?.user) confirmed(s.session.user);
    });
    const timer = setTimeout(() => {
      if (!done) {
        done = true;
        setState({ kind: "no-session" });
      }
    }, SESSION_WAIT_MS);
    return () => {
      clearTimeout(timer);
      data.subscription.unsubscribe();
    };
  }, [state.kind]);

  // Count down, then go to the app.
  useEffect(() => {
    if (state.kind !== "confirmed") return;
    if (seconds <= 0) {
      navigate("/app", { replace: true });
      return;
    }
    const t = setTimeout(() => setSeconds((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [state.kind, seconds, navigate]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-hero px-4">
      <Link to="/" className="mb-8 flex items-center gap-2 font-semibold">
        <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
      </Link>
      <div className="w-full max-w-sm rounded-2xl border bg-card p-6 text-center">
        {state.kind === "checking" && (
          <>
            <Loader2 className="mx-auto mb-3 h-10 w-10 animate-spin text-primary-glow" />
            <h1 className="text-xl font-semibold">驗證中… Confirming…</h1>
          </>
        )}

        {state.kind === "confirmed" && (
          <>
            <CheckCircle2 className="mx-auto mb-3 h-12 w-12 text-emerald-500" />
            <h1 className="text-xl font-semibold">Email 驗證完成 ✅</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {state.name ? `歡迎，${state.name}！` : "歡迎！"}你的帳號已經可以使用。Your email is
              confirmed.
            </p>
            <button
              onClick={() => navigate("/app", { replace: true })}
              className="mt-5 w-full rounded-lg bg-gradient-primary py-2 text-sm font-medium text-primary-foreground shadow-glow"
            >
              前往我的追蹤 Go to my alerts
            </button>
            <p className="mt-3 text-xs text-muted-foreground">{seconds} 秒後自動前往…</p>
          </>
        )}

        {state.kind === "error" && <ExpiredLink code={state.code} detail={state.message} />}

        {state.kind === "no-session" && (
          <>
            <CheckCircle2 className="mx-auto mb-3 h-12 w-12 text-emerald-500" />
            <h1 className="text-xl font-semibold">請登入以繼續</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              如果你剛點了確認連結，帳號應該已經驗證完成，請直接登入。If you just confirmed your
              email, sign in to continue.
            </p>
            <Link
              to="/sign-in"
              className="mt-5 block w-full rounded-lg bg-gradient-primary py-2 text-sm font-medium text-primary-foreground shadow-glow"
            >
              前往登入 Sign in
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

function ExpiredLink({ code, detail }: { code: string; detail: string }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function resend(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const { error } = await supabase.auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: confirmRedirectUrl() },
    });
    setBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    setSent(true);
  }

  return (
    <>
      <MailWarning className="mx-auto mb-3 h-12 w-12 text-amber-500" />
      <h1 className="text-xl font-semibold">確認連結無法使用</h1>
      <p className="mt-2 text-sm text-muted-foreground">{errorText(code)}</p>
      <p className="mt-1 text-xs text-muted-foreground" title={detail}>
        已經驗證過的話，直接登入即可。Already confirmed? Just sign in.
      </p>
      {sent ? (
        <p className="mt-5 rounded-lg bg-accent p-3 text-sm">
          已重新寄出確認信，請到信箱點新的連結。A new confirmation email is on its way.
        </p>
      ) : (
        <form onSubmit={resend} className="mt-5 space-y-3 text-left">
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="註冊用的 Email"
            aria-label="註冊用的 Email"
            className="w-full rounded-lg border bg-muted px-3 py-2 text-sm outline-none focus:border-primary"
          />
          <button
            disabled={busy}
            className="w-full rounded-lg bg-gradient-primary py-2 text-sm font-medium text-primary-foreground shadow-glow disabled:opacity-60"
          >
            {busy ? "寄送中…" : "重新寄送確認信 Resend email"}
          </button>
        </form>
      )}
      <Link
        to="/sign-in"
        className="mt-4 block text-sm text-muted-foreground hover:text-foreground"
      >
        前往登入 Sign in
      </Link>
    </>
  );
}
