import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Plane, BellRing, CircleOff } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Flight Price Notifier — 機票降價通知" },
      { name: "description", content: "Set a route and a target price — we email you when the fare drops." },
      { property: "og:title", content: "Flight Price Notifier — 機票降價通知" },
      { property: "og:description", content: "Set a route and a target price — we email you when the fare drops." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

const features = [
  { icon: Plane, title: "盯緊熱門航線 (Always-on route watching)", body: "持續監控台北出發的熱門航線（東京、首爾），自動抓最低票價。" },
  { icon: BellRing, title: "達標自動通知 (Target-price email alerts)", body: "低於你設定的目標價，就寄 email 提醒你，附上立即訂購連結。" },
  { icon: CircleOff, title: "隨時取消 (Cancel anytime)", body: "月訂閱制，不想用隨時停，沒有綁約。" },
];

function Index() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSignedIn(!!data.session));
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSignedIn(!!s));
    return () => data.subscription.unsubscribe();
  }, []);
  return (
    <div className="min-h-screen bg-hero">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <span className="flex items-center gap-2 font-semibold">
          <Plane className="h-5 w-5 text-primary-glow" /> Flight Price Notifier
        </span>
        <Link
          to={signedIn ? "/app" : "/auth"}
          className="rounded-lg bg-gradient-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-glow transition-transform hover:scale-105"
        >
          {signedIn ? "我的通知 My alerts" : "Sign in / 登入"}
        </Link>
      </header>

      <main>
        <section className="mx-auto max-w-4xl px-6 pb-20 pt-20 text-center sm:pt-28">
          <h1 className="fade-up text-5xl font-bold tracking-tight sm:text-7xl">
            <span className="text-gradient">Flight Price Notifier</span>
          </h1>
          <p className="fade-up mt-6 text-xl font-medium sm:text-2xl [animation-delay:120ms]">
            設定航線與目標價，機票降價就通知你
          </p>
          <p className="fade-up mt-3 text-muted-foreground [animation-delay:220ms]">
            Set a route and a target price — we email you when the fare drops.
          </p>
        </section>

        <section className="mx-auto grid max-w-6xl gap-6 px-6 pb-24 md:grid-cols-3">
          {features.map((f, i) => (
            <article
              key={f.title}
              className="fade-up rounded-2xl border bg-card p-6 transition-colors hover:border-primary/50"
              style={{ animationDelay: `${300 + i * 120}ms` }}
            >
              <div className="mb-4 inline-flex rounded-xl bg-accent p-3">
                <f.icon className="h-6 w-6 text-primary-glow" />
              </div>
              <h2 className="text-lg font-semibold">{f.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
            </article>
          ))}
        </section>
      </main>

      <footer className="border-t py-8 text-center text-sm text-muted-foreground">
        © 2026 Flight Price Notifier
      </footer>
    </div>
  );
}
