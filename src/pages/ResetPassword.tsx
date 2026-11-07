import { useNavigate } from "react-router";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { usePageMeta } from "@/lib/page-meta";


export default function ResetPassword() {
  usePageMeta({
    title: "重設密碼 Reset password — Flight Price Notifier",
    description: "Choose a new password for your Flight Price Notifier account.",
    twitterCard: "summary",
  });
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) { toast.error(error.message); return; }
    toast.success("密碼已更新 Password updated");
    navigate("/app", { replace: true });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-hero px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-3 rounded-2xl border bg-card p-6">
        <h1 className="mb-2 text-xl font-semibold">設定新密碼 New password</h1>
        <input
          type="password"
          minLength={6}
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="新密碼 New password"
          className="w-full rounded-lg border bg-muted px-3 py-2 text-sm outline-none focus:border-primary"
        />
        <button disabled={busy} className="w-full rounded-lg bg-gradient-primary py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">
          更新密碼 Update password
        </button>
      </form>
    </div>
  );
}
