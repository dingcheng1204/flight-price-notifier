import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Plane, Trash2, LogOut } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/_authenticated/app")({
  head: () => ({
    meta: [
      { title: "我的通知 My alerts — Flight Price Notifier" },
      { name: "description", content: "Manage your flight routes and target prices." },
      { property: "og:title", content: "我的通知 My alerts — Flight Price Notifier" },
      { property: "og:description", content: "Manage your flight routes and target prices." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AppPage,
});

const DESTINATIONS = [
  { code: "NRT", label: "東京成田 Tokyo Narita" },
  { code: "HND", label: "東京羽田 Tokyo Haneda" },
  { code: "ICN", label: "首爾仁川 Seoul Incheon" },
  { code: "GMP", label: "首爾金浦 Seoul Gimpo" },
];
const labelFor = (c: string) => DESTINATIONS.find((d) => d.code === c)?.label ?? c;

function AppPage() {
  const { user } = Route.useRouteContext();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [destination, setDestination] = useState("NRT");
  const [price, setPrice] = useState("");

  const profile = useQuery({
    queryKey: ["profile", user.id],
    queryFn: async () => {
      const { data } = await supabase.from("profiles").select("display_name").eq("id", user.id).maybeSingle();
      return data;
    },
  });

  const alerts = useQuery({
    queryKey: ["alerts", user.id],
    queryFn: async () => {
      const { data, error } = await supabase.from("price_alerts").select("*").order("created_at", { ascending: false });
      if (error) throw error;
      return data;
    },
  });

  const add = useMutation({
    mutationFn: async () => {
      const { error } = await supabase
        .from("price_alerts")
        .insert({ user_id: user.id, origin: "TPE", destination, target_price: Number(price) });
      if (error) throw error;
    },
    onSuccess: () => {
      setPrice("");
      toast.success("已新增通知 Alert added");
      qc.invalidateQueries({ queryKey: ["alerts", user.id] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const toggle = useMutation({
    mutationFn: async ({ id, active }: { id: string; active: boolean }) => {
      const { error } = await supabase.from("price_alerts").update({ active }).eq("id", id);
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts", user.id] }),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await supabase.from("price_alerts").delete().eq("id", id);
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts", user.id] }),
  });

  async function signOut() {
    await qc.cancelQueries();
    qc.clear();
    await supabase.auth.signOut();
    navigate({ to: "/auth", replace: true });
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!(Number(price) > 0)) return toast.error("請輸入有效價格 Enter a valid price");
    add.mutate();
  }

  return (
    <div className="min-h-screen bg-hero">
      <header className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
        <Link to="/" className="flex items-center gap-2 font-semibold">
          <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
        </Link>
        <button onClick={signOut} className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-accent">
          <LogOut className="h-4 w-4" /> 登出 Sign out
        </button>
      </header>

      <main className="mx-auto max-w-4xl space-y-8 px-6 pb-20">
        <h1 className="text-3xl font-bold">嗨，{profile.data?.display_name ?? "…"} 👋</h1>

        <form onSubmit={submit} className="grid gap-3 rounded-2xl border bg-card p-6 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">台北 TPE → 目的地 Destination</span>
            <select value={destination} onChange={(e) => setDestination(e.target.value)} className="w-full rounded-lg border bg-muted px-3 py-2">
              {DESTINATIONS.map((d) => (
                <option key={d.code} value={d.code}>{d.label}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">目標價 Target price (TWD)</span>
            <input type="number" min={1} value={price} onChange={(e) => setPrice(e.target.value)} placeholder="例如 8000" className="w-full rounded-lg border bg-muted px-3 py-2" required />
          </label>
          <button disabled={add.isPending} className="rounded-lg bg-gradient-primary px-5 py-2 text-sm font-medium text-primary-foreground shadow-glow disabled:opacity-60">
            新增通知 Add alert
          </button>
        </form>

        <section className="space-y-3">
          <h2 className="text-lg font-semibold">我的通知 My alerts</h2>
          {alerts.isLoading && <p className="text-sm text-muted-foreground">載入中…</p>}
          {alerts.data?.length === 0 && <p className="text-sm text-muted-foreground">還沒有通知，新增第一個吧！No alerts yet.</p>}
          {alerts.data?.map((a) => (
            <div key={a.id} className="flex items-center justify-between rounded-xl border bg-card p-4">
              <div>
                <p className="font-medium">{a.origin} → {labelFor(a.destination)}</p>
                <p className="text-sm text-muted-foreground">目標價 ≤ NT$ {a.target_price.toLocaleString()}</p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => toggle.mutate({ id: a.id, active: !a.active })}
                  className={`rounded-full px-3 py-1 text-xs font-medium ${a.active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
                >
                  {a.active ? "啟用中 Active" : "已暫停 Paused"}
                </button>
                <button onClick={() => remove.mutate(a.id)} aria-label="Delete alert" className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </section>
      </main>
    </div>
  );
}
