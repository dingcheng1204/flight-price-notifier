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

// ---------- date windows ----------
/** All four are YYYY-MM-DD. */
export interface DateWindow {
  depart_from: string;
  depart_to: string;
  return_from: string;
  return_to: string;
}

/** A window being edited in the form: any field may still be unset. */
export type DraftWindow = { [K in keyof DateWindow]?: string | undefined };

export const MAX_RANGE_DAYS = 60;

/** Local calendar date as YYYY-MM-DD (the app is used in Taiwan; the API checks Taipei time). */
export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Parse YYYY-MM-DD as a local date (not UTC midnight). */
export function parseIsoDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function daysBetween(a: string, b: string): number {
  return Math.round((parseIsoDate(b).getTime() - parseIsoDate(a).getTime()) / 86_400_000);
}

/** Returns an error message, or null when the window is valid. `today` is YYYY-MM-DD. */
export function dateError(w: DraftWindow, today: string): string | null {
  if (!w.depart_from || !w.depart_to) return "請選擇去程日期範圍 Choose departure dates";
  if (!w.return_from || !w.return_to) return "請選擇回程日期範圍 Choose return dates";
  if (w.depart_from < today) return "去程不能早於今天 Departure can't be before today";
  if (w.depart_to < w.depart_from || w.return_to < w.return_from)
    return "結束日不能早於開始日 Each range must end on or after its start";
  if (w.return_from <= w.depart_from)
    return "回程日期必須晚於去程日期 Return must be after departure";
  if (
    daysBetween(w.depart_from, w.depart_to) > MAX_RANGE_DAYS ||
    daysBetween(w.return_from, w.return_to) > MAX_RANGE_DAYS
  )
    return `每個區間最多 ${MAX_RANGE_DAYS} 天 Each range can span at most ${MAX_RANGE_DAYS} days`;
  return null;
}

const WEEKDAY = ["日", "一", "二", "三", "四", "五", "六"];

/** "12/10（四）" */
export function shortDate(s: string): string {
  const d = parseIsoDate(s);
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAY[d.getDay()]}）`;
}

/** "12/10（四）– 12/20（日）" or a single day. */
export function rangeLabel(from?: string, to?: string): string {
  if (!from) return "";
  if (!to || to === from) return shortDate(from);
  return `${shortDate(from)} – ${shortDate(to)}`;
}

export function windowOf(s: DraftWindow): DateWindow | null {
  return s.depart_from && s.depart_to && s.return_from && s.return_to
    ? {
        depart_from: s.depart_from,
        depart_to: s.depart_to,
        return_from: s.return_from,
        return_to: s.return_to,
      }
    : null;
}

export interface Subscription extends Partial<DateWindow> {
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

/**
 * dates: a window to set it, null to clear it, undefined to leave the stored window unchanged
 * (used by the per-row "update target" button).
 */
export async function saveSubscription(
  email: string,
  origin: string,
  destination: string,
  target_price: number,
  dates?: DateWindow | null,
) {
  const body: Record<string, unknown> = { email, origin, destination, target_price };
  if (dates !== undefined) body["dates"] = dates;
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
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
