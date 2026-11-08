import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Plane, LogOut, BadgeCheck, ArrowRightLeft, Trash2, CalendarDays } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useAuthUser } from "@/components/RequireAuth";
import { usePageMeta } from "@/lib/page-meta";
import { DateRangeField } from "@/components/DateRangeField";
import { Switch } from "@/components/ui/switch";
import {
  AIRPORTS,
  airportLabel,
  dateError,
  isoDate,
  parseIsoDate,
  rangeLabel,
  windowOf,
  type DraftWindow,
  deleteSubscription,
  listSubscriptions,
  routeError,
  saveSubscription,
  shortLabel,
  type Subscription,
} from "@/lib/flight-api";

const selectClass = "w-full rounded-lg border bg-muted px-3 py-2";

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

  const sorted = [...(subs.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  return (
    <div className="min-h-screen bg-hero">
      <header className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
        <Link to="/" className="flex items-center gap-2 font-semibold">
          <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
        </Link>
        <button
          onClick={signOut}
          className="flex items-center gap-2 rounded-lg border border-white/40 px-3 py-2 text-sm hover:bg-white/10"
        >
          <LogOut className="h-4 w-4" /> 登出 Sign out
        </button>
      </header>

      <main className="mx-auto max-w-4xl space-y-8 px-6 pb-20">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold">嗨，{displayName} 👋</h1>
          <p className="text-muted-foreground">
            選擇出發地、目的地和目標價，票價跌到目標以下時我們會寄信到{" "}
            <span className="font-medium text-foreground">{email}</span>。
          </p>
        </div>

        <NewAlertForm email={email} existing={subs.data ?? []} />

        <section className="space-y-3">
          <h2 className="text-lg font-semibold">我的追蹤 My alerts</h2>
          {subs.isLoading && <p className="text-sm text-muted-foreground">載入中… Loading…</p>}
          {subs.isError && (
            <p className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
              無法載入訂閱 Couldn't load subscriptions: {(subs.error as Error).message}
            </p>
          )}
          {subs.data?.length === 0 && (
            <p className="text-sm text-muted-foreground">
              還沒有追蹤任何航線，從上面新增第一條吧！No alerts yet.
            </p>
          )}
          {sorted.map((s) => (
            <SubscriptionRow key={s.route} email={email} sub={s} />
          ))}
        </section>

        <p className="text-xs text-muted-foreground">
          每 30 分鐘檢查一次下個月的最低來回票價（TWD）。同一航線 24
          小時內最多通知一次，除非票價再降 20% 或 NT$2,000 以上。
        </p>
      </main>
    </div>
  );
}

function NewAlertForm({ email, existing }: { email: string; existing: Subscription[] }) {
  const qc = useQueryClient();
  const [origin, setOrigin] = useState("TSA");
  const [destination, setDestination] = useState("HND");
  const [price, setPrice] = useState("");
  const [useDates, setUseDates] = useState(false);
  const [win, setWin] = useState<DraftWindow>({});
  const today = isoDate(new Date());
  const tomorrowOf = (s: string) => {
    const d = parseIsoDate(s);
    d.setDate(d.getDate() + 1);
    return isoDate(d);
  };
  const routeErr = routeError(origin, destination);
  const datesErr = useDates ? dateError(win, today) : null;
  const error = routeErr ?? datesErr;
  const route = `${origin}-${destination}`;
  const already = existing.some((s) => s.route === route);

  function setDepart(from?: string, to?: string) {
    setWin((w) => {
      const next = { ...w, depart_from: from, depart_to: to };
      // a return range that now starts on/before the departure start is no longer valid
      if (from && w.return_from && w.return_from <= from) {
        next.return_from = undefined;
        next.return_to = undefined;
      }
      return next;
    });
  }

  const save = useMutation({
    mutationFn: (target: number) =>
      saveSubscription(email, origin, destination, target, useDates ? windowOf(win) : null),
    onSuccess: (res) => {
      setPrice("");
      toast.success(
        already
          ? `已更新 ${shortLabel(origin)} → ${shortLabel(destination)} 目標價 NT$${res.target_price.toLocaleString()}`
          : `開始追蹤 ${shortLabel(origin)} → ${shortLabel(destination)}！`,
      );
      setUseDates(false);
      setWin({});
      qc.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (error) {
      toast.error(error);
      return;
    }
    const n = Number(price);
    if (!(n > 0)) {
      toast.error("請輸入有效價格 Enter a valid price");
      return;
    }
    save.mutate(Math.round(n));
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-2xl border bg-card p-6">
      <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-end">
        <label className="text-sm">
          <span className="mb-1 block text-muted-foreground">出發地 From</span>
          <select
            aria-label="出發地 From"
            value={origin}
            onChange={(e) => setOrigin(e.target.value)}
            className={selectClass}
          >
            {AIRPORTS.map((a) => (
              <option key={a.code} value={a.code}>
                {airportLabel(a.code)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => {
            setOrigin(destination);
            setDestination(origin);
          }}
          aria-label="交換出發地與目的地 Swap"
          title="交換 Swap"
          className="mx-auto rounded-lg border p-2 hover:bg-accent"
        >
          <ArrowRightLeft className="h-4 w-4" />
        </button>
        <label className="text-sm">
          <span className="mb-1 block text-muted-foreground">目的地 To</span>
          <select
            aria-label="目的地 To"
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            className={selectClass}
          >
            {AIRPORTS.map((a) => (
              <option key={a.code} value={a.code}>
                {airportLabel(a.code)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <label className="text-sm">
          <span className="mb-1 block text-muted-foreground">目標價 Target price (TWD)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="例如 8000"
            className="w-full rounded-lg border bg-muted px-3 py-2"
            required
          />
        </label>
        <button
          disabled={save.isPending || !email || Boolean(error)}
          className="rounded-lg bg-gradient-primary px-5 py-2 text-sm font-medium text-primary-foreground shadow-glow disabled:opacity-60"
        >
          {save.isPending ? "儲存中… Saving…" : already ? "更新目標價 Update" : "開始追蹤 Track"}
        </button>
      </div>

      <div className="space-y-3 rounded-xl border border-dashed p-4">
        <label className="flex cursor-pointer items-center gap-3 text-sm">
          <Switch
            checked={useDates}
            onCheckedChange={setUseDates}
            aria-label="指定日期 Specific dates"
          />
          <span>
            指定日期 Specific dates
            <span className="ml-2 text-xs text-muted-foreground">
              {useDates
                ? "只通知去程與回程都落在區間內、且低於目標價的票"
                : "不指定：追蹤下個月任何日期的最低價"}
            </span>
          </span>
        </label>
        {useDates && (
          <div className="grid gap-3 sm:grid-cols-2">
            <DateRangeField
              label="去程日期 Departure"
              from={win.depart_from}
              to={win.depart_to}
              minDate={today}
              onChange={setDepart}
              invalid={Boolean(datesErr && !win.depart_to)}
            />
            <DateRangeField
              label="回程日期 Return"
              from={win.return_from}
              to={win.return_to}
              minDate={win.depart_from ? tomorrowOf(win.depart_from) : tomorrowOf(today)}
              defaultMonth={win.depart_to ?? win.depart_from}
              onChange={(f, t) => setWin((w) => ({ ...w, return_from: f, return_to: t }))}
              invalid={Boolean(datesErr && win.depart_to && !win.return_to)}
            />
          </div>
        )}
      </div>

      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        already && (
          <p className="text-sm text-muted-foreground">
            你已經在追蹤這條航線，送出會更新目標價與日期設定。You already track this route —
            submitting updates its target and dates.
          </p>
        )
      )}
    </form>
  );
}

function SubscriptionRow({ email, sub }: { email: string; sub: Subscription }) {
  const qc = useQueryClient();
  const [price, setPrice] = useState("");

  const update = useMutation({
    mutationFn: (target: number) => saveSubscription(email, sub.origin, sub.destination, target),
    onSuccess: (res) => {
      setPrice("");
      toast.success(`已更新目標價 NT$${res.target_price.toLocaleString()}`);
      qc.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: () => deleteSubscription(email, sub.route),
    onSuccess: () => {
      toast.success(`已取消追蹤 ${shortLabel(sub.origin)} → ${shortLabel(sub.destination)}`);
      qc.setQueryData<Subscription[]>(["subscriptions", email], (old = []) =>
        old.filter((s) => s.route !== sub.route),
      );
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
    update.mutate(Math.round(n));
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-xl border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 font-medium">
          {airportLabel(sub.origin)} → {airportLabel(sub.destination)}
          <span className="flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground">
            <BadgeCheck className="h-3 w-3" /> 已訂閱
          </span>
        </p>
        <p className="text-sm text-muted-foreground">
          目標價 Target ≤ NT${sub.target_price.toLocaleString()}
        </p>
        <WindowLine sub={sub} />
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={1}
          step={1}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="新目標價"
          aria-label={`新目標價 ${sub.route}`}
          className="w-28 rounded-lg border bg-muted px-3 py-2 text-sm"
        />
        <button
          disabled={update.isPending || !price}
          className="whitespace-nowrap rounded-lg border px-3 py-2 text-sm hover:bg-accent disabled:opacity-60"
        >
          更新目標價
        </button>
        <button
          type="button"
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          aria-label={`取消追蹤 ${sub.route}`}
          title="取消追蹤 Delete"
          className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-60"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </form>
  );
}

function WindowLine({ sub }: { sub: Subscription }) {
  const w = windowOf(sub);
  if (!w) {
    return (
      <p className="text-xs text-muted-foreground">日期：下個月任何日期 Any date next month</p>
    );
  }
  const expired = w.depart_to < isoDate(new Date());
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <CalendarDays className="h-3.5 w-3.5" />
      <span>去程 {rangeLabel(w.depart_from, w.depart_to)}</span>
      <span>回程 {rangeLabel(w.return_from, w.return_to)}</span>
      {expired && (
        <span className="rounded-full bg-destructive/15 px-2 py-0.5 font-medium text-destructive">
          已過期 Expired
        </span>
      )}
    </p>
  );
}
