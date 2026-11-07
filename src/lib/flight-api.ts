// Client for the AWS HTTP API (API Gateway → Lambda → DynamoDB).
// Supabase is auth-only; all subscription data lives in DynamoDB.
// The API URL is public (no secrets in the browser), so a fallback default is fine.
export const FLIGHT_API_URL: string = (
  import.meta.env["VITE_FLIGHT_API_URL"] ?? "https://vs9im442v3.execute-api.us-east-1.amazonaws.com"
).replace(/\/$/, "");

export type PlanName = "tokyo" | "seoul";

export interface Plan {
  name: PlanName;
  label: string;
  route: string;
  cheapestHint: number; // rough recent cheapest fare (TWD), to help pick a sane budget
}

export const PLANS: Plan[] = [
  { name: "tokyo", label: "台北 ✈ 東京", route: "TPE-TYO", cheapestHint: 8100 },
  { name: "seoul", label: "台北 ✈ 首爾", route: "TPE-SEL", cheapestHint: 6000 },
];

export interface Subscription {
  email: string;
  route: string;
  plan_name: PlanName;
  origin: string;
  destination: string;
  target_price: number;
  currency: "TWD";
  created_at: string;
  updated_at: string;
}

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `HTTP ${res.status}`);
  return body as T;
}

export async function listSubscriptions(email: string): Promise<Subscription[]> {
  const res = await fetch(`${FLIGHT_API_URL}/subscriptions?email=${encodeURIComponent(email)}`);
  return (await parse<{ subscriptions: Subscription[] }>(res)).subscriptions;
}

export async function saveSubscription(email: string, plan_name: PlanName, target_price: number) {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, plan_name, target_price }),
  });
  return parse<{ ok: true; route: string; target_price: number }>(res);
}
