import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/integrations/supabase/client", () => ({ supabase: { auth: { signOut: vi.fn() } } }));
vi.mock("@/components/RequireAuth", () => ({
  useAuthUser: () => ({
    id: "u1",
    email: "Me@Example.com",
    user_metadata: { display_name: "Ding" },
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import AppPage from "@/pages/AppPage";
import { AIRPORTS, airportLabel, routeError } from "@/lib/flight-api";
import { toast } from "sonner";

// ---- in-memory fake of the AWS API ----
type Row = {
  email: string;
  route: string;
  origin: string;
  destination: string;
  target_price: number;
};
let rows: Row[];
const calls: { method: string; url: string; body?: unknown }[] = [];

function fakeFetch(input: RequestInfo | URL, init?: RequestInit) {
  const url = String(input);
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  calls.push({ method, url, body });
  const json = (status: number, data: unknown) =>
    Promise.resolve(new Response(JSON.stringify(data), { status }));
  if (method === "GET") {
    const email = new URL(url).searchParams.get("email");
    return json(200, {
      subscriptions: rows
        .filter((r) => r.email === email)
        .map((r) => ({
          ...r,
          currency: "TWD",
          created_at: "2026-11-07T00:00:00Z",
          updated_at: "2026-11-07T00:00:00Z",
        })),
    });
  }
  if (method === "POST") {
    const route = `${body.origin}-${body.destination}`;
    rows = rows.filter((r) => !(r.email === body.email && r.route === route));
    rows.push({
      email: body.email,
      route,
      origin: body.origin,
      destination: body.destination,
      target_price: body.target_price,
    });
    return json(200, { ok: true, route, target_price: body.target_price });
  }
  if (method === "DELETE") {
    rows = rows.filter((r) => !(r.email === body.email && r.route === body.route));
    return json(200, { ok: true, deleted: body.route });
  }
  return json(405, { error: "nope" });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AppPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  rows = [
    {
      email: "me@example.com",
      route: "TPE-TYO",
      origin: "TPE",
      destination: "TYO",
      target_price: 9000,
    },
  ];
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

describe("airport helpers", () => {
  it("offers exactly the six airports with Chinese + English labels", () => {
    expect(AIRPORTS.map((a) => a.code)).toEqual(["TSA", "TPE", "HND", "NRT", "HKG", "ICN"]);
    expect(airportLabel("TSA")).toBe("台北松山 Taipei Songshan (TSA)");
    expect(airportLabel("ICN")).toBe("首爾仁川 Seoul Incheon (ICN)");
    expect(airportLabel("TYO")).toBe("東京（所有機場） Tokyo (all airports) (TYO)");
  });

  it("allows every cross-city pair in both directions and blocks same-city pairs", () => {
    const valid: string[] = [];
    for (const o of AIRPORTS)
      for (const d of AIRPORTS) if (!routeError(o.code, d.code)) valid.push(`${o.code}-${d.code}`);
    // 6×5 = 30 ordered pairs of different airports, minus TSA⇄TPE and HND⇄NRT (same city) = 26
    expect(valid).toHaveLength(26);
    for (const r of ["TSA-HND", "HND-TSA", "TPE-NRT", "NRT-TPE", "HKG-ICN", "ICN-HKG"])
      expect(valid).toContain(r);
    expect(routeError("TSA", "TSA")).toMatch(/不能相同/);
    expect(routeError("TSA", "TPE")).toMatch(/同一城市/);
    expect(routeError("NRT", "HND")).toMatch(/同一城市/);
  });
});

describe("AppPage", () => {
  it("lists existing alerts, including legacy city-level rows", async () => {
    renderPage();
    expect(await screen.findByText(/東京（所有機場）/)).toBeInTheDocument();
    expect(screen.getByText(/NT\$9,000/)).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ method: "GET" });
    expect(calls[0]?.url).toContain("email=me%40example.com");
  });

  it("adds TSA → HND, then HND → TSA (both directions), then updates and deletes", async () => {
    renderPage();
    await screen.findByText(/東京（所有機場）/);
    const from = screen.getByLabelText("出發地 From") as HTMLSelectElement;
    const to = screen.getByLabelText("目的地 To") as HTMLSelectElement;
    const price = screen.getByPlaceholderText("例如 8000");

    // default is TSA → HND
    expect(from.value).toBe("TSA");
    expect(to.value).toBe("HND");
    fireEvent.change(price, { target: { value: "15000" } });
    fireEvent.click(screen.getByRole("button", { name: /開始追蹤 Track/ }));
    await waitFor(() => expect(rows.some((r) => r.route === "TSA-HND")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      email: "me@example.com",
      origin: "TSA",
      destination: "HND",
      target_price: 15000,
    });
    expect(
      await screen.findByText(/台北松山 Taipei Songshan \(TSA\) → 東京羽田 Tokyo Haneda \(HND\)/),
    ).toBeInTheDocument();

    // swap → HND → TSA
    fireEvent.click(screen.getByRole("button", { name: /交換/ }));
    expect(from.value).toBe("HND");
    expect(to.value).toBe("TSA");
    fireEvent.change(price, { target: { value: "14000" } });
    fireEvent.click(screen.getByRole("button", { name: /開始追蹤 Track/ }));
    expect(
      await screen.findByText(/東京羽田 Tokyo Haneda \(HND\) → 台北松山 Taipei Songshan \(TSA\)/),
    ).toBeInTheDocument();
    expect(rows.map((r) => r.route).sort()).toEqual(["HND-TSA", "TPE-TYO", "TSA-HND"]);

    // update TSA-HND's target from its row
    const rowInput = screen.getByLabelText("新目標價 TSA-HND");
    fireEvent.change(rowInput, { target: { value: "12000" } });
    const row = rowInput.closest("form")!;
    fireEvent.click(within(row).getByRole("button", { name: "更新目標價" }));
    await waitFor(() => expect(rows.find((r) => r.route === "TSA-HND")?.target_price).toBe(12000));
    expect(await within(row).findByText(/NT\$12,000/)).toBeInTheDocument();

    // delete HND-TSA
    fireEvent.click(screen.getByRole("button", { name: "取消追蹤 HND-TSA" }));
    await waitFor(() => expect(rows.some((r) => r.route === "HND-TSA")).toBe(false));
    await waitFor(() =>
      expect(screen.queryByLabelText("新目標價 HND-TSA")).not.toBeInTheDocument(),
    );
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({
      email: "me@example.com",
      route: "HND-TSA",
    });
  });

  it("blocks same-airport and same-city routes before calling the API", async () => {
    renderPage();
    await screen.findByText(/東京（所有機場）/);
    fireEvent.change(screen.getByLabelText("出發地 From"), { target: { value: "HND" } });
    fireEvent.change(screen.getByLabelText("目的地 To"), { target: { value: "NRT" } });
    expect(screen.getByText(/同一城市/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /開始追蹤 Track/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("目的地 To"), { target: { value: "HND" } });
    expect(screen.getByText(/不能相同/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("switches the button to Update when the route is already tracked", async () => {
    rows.push({
      email: "me@example.com",
      route: "TSA-HND",
      origin: "TSA",
      destination: "HND",
      target_price: 15000,
    });
    renderPage();
    expect(await screen.findByRole("button", { name: /更新目標價 Update/ })).toBeInTheDocument();
    expect(screen.getByText(/你已經在追蹤這條航線/)).toBeInTheDocument();
  });
});
