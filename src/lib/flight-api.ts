// Client for the AWS HTTP API (API Gateway → Lambda → DynamoDB).
// Supabase is auth-only; all subscription data lives in DynamoDB.
// The API URL is public (no secrets in the browser), so a fallback default is fine.
export const FLIGHT_API_URL: string = (
  import.meta.env["VITE_FLIGHT_API_URL"] ?? "https://vs9im442v3.execute-api.us-east-1.amazonaws.com"
).replace(/\/$/, "");

export interface Airport {
  code: string;
  zh: string;
  en: string;
  city: string; // metro code — two airports in the same city can't form a route
}

export const AIRPORTS: Airport[] = [
  { code: "TSA", zh: "台北松山", en: "Taipei Songshan", city: "TPE" },
  { code: "TPE", zh: "台北桃園", en: "Taipei Taoyuan", city: "TPE" },
  { code: "HND", zh: "東京羽田", en: "Tokyo Haneda", city: "TYO" },
  { code: "NRT", zh: "東京成田", en: "Tokyo Narita", city: "TYO" },
  { code: "HKG", zh: "香港", en: "Hong Kong", city: "HKG" },
  { code: "ICN", zh: "首爾仁川", en: "Seoul Incheon", city: "SEL" },
];

// City codes used by M1's original two plans (TPE-TYO / TPE-SEL), still shown for old rows.
const LEGACY: Record<string, { zh: string; en: string }> = {
  TYO: { zh: "東京（所有機場）", en: "Tokyo (all airports)" },
  SEL: { zh: "首爾（所有機場）", en: "Seoul (all airports)" },
};

const byCode = new Map(AIRPORTS.map((a) => [a.code, a]));

export function airportLabel(code: string): string {
  const a = byCode.get(code) ?? LEGACY[code];
  return a ? `${a.zh} ${a.en} (${code})` : code;
}

export function shortLabel(code: string): string {
  const a = byCode.get(code) ?? LEGACY[code];
  return a ? `${a.zh} ${code}` : code;
}

/** Returns an error message, or null when origin → destination is a valid route. */
export function routeError(origin: string, destination: string): string | null {
  const o = byCode.get(origin);
  const d = byCode.get(destination);
  if (!o || !d) return "請選擇出發地與目的地 Choose both airports";
  if (o.code === d.code) return "出發地與目的地不能相同 Origin and destination must differ";
  if (o.city === d.city) return "同一城市的兩個機場之間沒有航班 Pick airports in different cities";
  return null;
}

export interface Subscription {
  email: string;
  route: string;
  origin: string;
  destination: string;
  target_price: number;
  currency: "TWD";
  created_at: string;
  updated_at: string;
  plan_name?: string;
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

export async function saveSubscription(
  email: string,
  origin: string,
  destination: string,
  target_price: number,
) {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, origin, destination, target_price }),
  });
  return parse<{ ok: true; route: string; target_price: number }>(res);
}

export async function deleteSubscription(email: string, route: string) {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, route }),
  });
  return parse<{ ok: true; deleted: string }>(res);
}
