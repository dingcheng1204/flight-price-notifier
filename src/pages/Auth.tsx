import { Link, useNavigate } from "react-router";
import { useEffect, useState, type FormEvent } from "react";
import { Plane } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { confirmRedirectUrl } from "@/lib/auth-redirect";
import { usePageMeta } from "@/lib/page-meta";


type Mode = "signin" | "signup" | "forgot";

const inputCls =
  "w-full rounded-lg border bg-muted px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

export default function AuthPage({ initialMode }: { initialMode: "signin" | "signup" }) {
  usePageMeta({
    title: "登入 Sign in — Flight Price Notifier",
    description: "Sign in or create an account to manage your fare-drop alerts.",
    twitterCard: "summary",
  });
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      if (data.user) navigate("/app", { replace: true });
    });
  }, [navigate]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        navigate("/app", { replace: true });
      } else if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: confirmRedirectUrl(), data: { display_name: name } },
        });
        if (error) throw error;
        if (data.session) navigate("/app", { replace: true });
        else setNotice("已寄出確認信，請到信箱點連結完成註冊。Check your email to confirm your account.");
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: window.location.origin + "/reset-password",
        });
        if (error) throw error;
        setNotice("重設密碼連結已寄出。Password reset link sent — check your email.");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  // Sign in / sign up each have their own URL; "forgot" stays on the current page.
  function switchTo(next: "signin" | "signup") {
    setMode(next);
    setNotice(null);
    navigate(next === "signin" ? "/sign-in" : "/sign-up");
  }

  const title = mode === "signin" ? "登入 Sign in" : mode === "signup" ? "建立帳號 Create account" : "忘記密碼 Reset password";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-hero px-4">
      <Link to="/" className="mb-8 flex items-center gap-2 font-semibold">
        <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
      </Link>
      <div className="w-full max-w-sm rounded-2xl border bg-card p-6">
        <h1 className="mb-5 text-xl font-semibold">{title}</h1>
        {notice ? (
          <p className="rounded-lg bg-accent p-3 text-sm">{notice}</p>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            {mode === "signup" && (
              <input className={inputCls} placeholder="顯示名稱 Display name" value={name} onChange={(e) => setName(e.target.value)} required />
            )}
            <input className={inputCls} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            {mode !== "forgot" && (
              <input className={inputCls} type="password" placeholder="密碼 Password" minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} required />
            )}
            <button disabled={busy} className="w-full rounded-lg bg-gradient-primary py-2 text-sm font-medium text-primary-foreground shadow-glow disabled:opacity-60">
              {busy ? "…" : mode === "signin" ? "登入 Sign in" : mode === "signup" ? "註冊 Sign up" : "寄送連結 Send link"}
            </button>
          </form>
        )}
        <div className="mt-5 flex flex-col gap-1 text-center text-sm text-muted-foreground">
          {mode !== "signin" && <button onClick={() => switchTo("signin")} className="hover:text-foreground">已有帳號？登入 Sign in</button>}
          {mode !== "signup" && <button onClick={() => switchTo("signup")} className="hover:text-foreground">還沒有帳號？註冊 Create account</button>}
          {mode === "signin" && <button onClick={() => setMode("forgot")} className="hover:text-foreground">忘記密碼？Forgot password</button>}
        </div>
      </div>
    </div>
  );
}
