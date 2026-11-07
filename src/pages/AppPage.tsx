import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Plane, LogOut, BadgeCheck } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useAuthUser } from "@/components/RequireAuth";
import { usePageMeta } from "@/lib/page-meta";
import {
  PLANS,
  listSubscriptions,
  saveSubscription,
  type Plan,
  type Subscription,
} from "@/lib/flight-api";

export default function AppPage() {
  usePageMeta({
    title: "我的通知 My alerts — Flight Price Notifier",
    description: "Pick a route and a target price — we email you when the fare drops.",
    twitterCard: "summary",
  });
  const user = useAuthUser();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const email = (user.email ?? "").toLowerCase();
  const displayName =
    (user.user_metadata?.["display_name"] as string | undefined) || email.split("@")[0] || "…";

  const subs = useQuery({
    queryKey: ["subscriptions", email],
    queryFn: () => listSubscriptions(email),
    enabled: Boolean(email),
  });

  async function signOut() {
    await qc.cancelQueries();
    qc.clear();
    await supabase.auth.signOut();
    navigate("/sign-in", { replace: true });
  }

  return (
    <div className="min-h-screen bg-hero">
      <header className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
        <Link to="/" className="flex items-center gap-2 font-semibold">
          <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
        </Link>
        <button
          onClick={signOut}
          className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-accent"
        >
          <LogOut className="h-4 w-4" /> 登出 Sign out
        </button>
      </header>

      <main className="mx-auto max-w-4xl space-y-8 px-6 pb-20">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold">嗨，{displayName} 👋</h1>
          <p className="text-muted-foreground">
            選一條航線、設定目標價，票價跌到目標以下時我們會寄信到{" "}
            <span className="font-medium text-foreground">{email}</span>。
          </p>
        </div>

        {subs.isError && (
          <p className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
            無法載入訂閱 Couldn't load subscriptions: {(subs.error as Error).message}
          </p>
        )}

        <div className="grid gap-6 sm:grid-cols-2">
          {PLANS.map((plan) => (
            <PlanCard
              key={plan.name}
              plan={plan}
              email={email}
              loading={subs.isLoading}
              subscription={subs.data?.find((s) => s.route === plan.route)}
            />
          ))}
        </div>

        <p className="text-xs text-muted-foreground">
          每 30 分鐘檢查一次下個月的最低來回票價（TWD）。同一航線 24
          小時內最多通知一次，除非票價再降 20% 或 NT$2,000 以上。
        </p>
      </main>
    </div>
  );
}

function PlanCard({
  plan,
  email,
  subscription,
  loading,
}: {
  plan: Plan;
  email: string;
  subscription: Subscription | undefined;
  loading: boolean;
}) {
  const qc = useQueryClient();
  const [price, setPrice] = useState("");
  const subscribed = Boolean(subscription);

  const save = useMutation({
    mutationFn: (target: number) => saveSubscription(email, plan.name, target),
    onSuccess: (res) => {
      setPrice("");
      toast.success(
        subscribed
          ? `已更新目標價 NT$${res.target_price.toLocaleString()}`
          : `開始追蹤 ${plan.label}！`,
      );
      qc.setQueryData<Subscription[]>(["subscriptions", email], (old = []) => {
        const now = new Date().toISOString();
        const row: Subscription = {
          ...(subscription ?? {
            email,
            route: plan.route,
            plan_name: plan.name,
            origin: plan.route.split("-")[0],
            destination: plan.route.split("-")[1],
            currency: "TWD",
            created_at: now,
          }),
          target_price: res.target_price,
          updated_at: now,
        } as Subscription;
        return [...old.filter((s) => s.route !== plan.route), row];
      });
      qc.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const n = Number(price);
    if (!(n > 0)) {
      toast.error("請輸入有效價格 Enter a valid price");
      return;
    }
    save.mutate(Math.round(n));
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4 rounded-2xl border bg-card p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">{plan.label}</h2>
          <p className="text-sm text-muted-foreground">
            近期最低約 NT${plan.cheapestHint.toLocaleString()}
          </p>
        </div>
        {subscribed && (
          <span className="flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
            <BadgeCheck className="h-3.5 w-3.5" /> 已訂閱
          </span>
        )}
      </div>

      {subscribed && (
        <p className="text-sm">
          目前目標價：
          <span className="font-semibold">NT${subscription!.target_price.toLocaleString()}</span>
        </p>
      )}

      <label className="text-sm">
        <span className="mb-1 block text-muted-foreground">
          {subscribed ? "新的目標價 New target (TWD)" : "目標價 Target price (TWD)"}
        </span>
        <input
          type="number"
          min={1}
          step={1}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder={`例如 ${(Math.ceil(plan.cheapestHint / 1000) * 1000).toLocaleString("en-US", { useGrouping: false })}`}
          className="w-full rounded-lg border bg-muted px-3 py-2"
          required
        />
      </label>

      <button
        disabled={save.isPending || loading || !email}
        className="rounded-lg bg-gradient-primary px-5 py-2 text-sm font-medium text-primary-foreground shadow-glow disabled:opacity-60"
      >
        {save.isPending ? "儲存中…" : subscribed ? "更新目標價" : "開始追蹤"}
      </button>
    </form>
  );
}
