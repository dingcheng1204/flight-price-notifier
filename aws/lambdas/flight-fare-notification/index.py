"""flight-fare-notification: SQS consumer. Dedup against notification_history,
render a TWD-first email (optional ~USD line), send via Resend, record history only after 2xx."""
import json, os, html, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
CITY = {"TPE": "台北", "TYO": "東京", "SEL": "首爾"}
_sm = boto3.client("secretsmanager")
_hist = boto3.resource("dynamodb").Table("notification_history")
_resend = None


class Transient(Exception):
    """Re-raised so SQS redelivers (429 / 5xx / network)."""


def resend_cfg():
    global _resend
    if _resend is None:
        _resend = json.loads(_sm.get_secret_value(SecretId="flight/resend")["SecretString"])
    return _resend


def env_num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# ---------- rendering ----------
def _split(route):
    o, d = route.split("-", 1)
    return o, d


def _ddmm(iso):
    # "2026-12-01T06:50:00+08:00" -> "0112"
    return iso[8:10] + iso[5:7] if iso and len(iso) >= 10 else ""


def _date(iso):
    return iso[:10] if iso else "—"


def booking_url(fare, marker=None):
    o, d = fare["origin"], fare["destination"]
    path = f"{o}{_ddmm(fare.get('depart_date'))}{d}{_ddmm(fare.get('return_date'))}1"
    url = f"https://www.aviasales.com/search/{path}"
    return url + (f"?marker={urllib.parse.quote(str(marker))}" if marker else "")


def route_label(fare):
    return f"{CITY.get(fare['origin'], fare['origin'])} → {CITY.get(fare['destination'], fare['destination'])}"


def subject(fare):
    return f"✈️ {route_label(fare)} 降價通知！NT${fare['price']:,} 已達標"


def render_text(fare, target_price, marker=None, usd_price=None):
    lines = [f"{route_label(fare)} 機票降價了！",
             f"目前最低價：NT${fare['price']:,}" + (f"（約 US${usd_price:,}）" if usd_price else ""),
             f"你的目標價：NT${int(target_price):,}",
             f"航空公司：{fare.get('airline') or '—'}",
             f"去程：{_date(fare.get('depart_date'))}　回程：{_date(fare.get('return_date'))}",
             "", f"立即訂購：{booking_url(fare, marker)}",
             "", "票價隨時變動，以訂購頁面為準。— Flight Price Notifier"]
    return "\n".join(lines)


def render_html(fare, target_price, marker=None, usd_price=None):
    e = html.escape
    usd = (f'<p style="margin:4px 0 0;color:#555;font-size:14px;">約 US${usd_price:,}</p>'
           if usd_price else "")
    return f"""<!doctype html><html><body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;background:#fff;">
<div style="max-width:480px;margin:0 auto;">
<p style="margin:0 0 8px;font-size:15px;">{e(route_label(fare))} 機票降價了！</p>
<p style="margin:0;font-size:32px;font-weight:700;">NT${fare['price']:,}</p>{usd}
<p style="margin:16px 0 0;font-size:14px;">你的目標價：NT${int(target_price):,}</p>
<p style="margin:4px 0 0;font-size:14px;">航空公司：{e(str(fare.get('airline') or '—'))}</p>
<p style="margin:4px 0 0;font-size:14px;">去程：{e(_date(fare.get('depart_date')))}　回程：{e(_date(fare.get('return_date')))}</p>
<p style="margin:24px 0;"><a href="{e(booking_url(fare, marker))}" style="display:inline-block;background:#111;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-size:15px;">立即訂購</a></p>
<p style="margin:0;font-size:12px;color:#777;">票價隨時變動，以訂購頁面為準。— Flight Price Notifier</p>
</div></body></html>"""


# ---------- dedup ----------
# History rows use ISO-8601 sent_at ("2026-..."); the per-pk lock row uses LOCK_SK, which
# sorts before any timestamp and is excluded from history reads by the "> 1" range bound.
LOCK_SK = "0000-lock"
LOCK_SECONDS = 90


def acquire_lock(pk):
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _hist.put_item(Item={"pk": pk, "sent_at": LOCK_SK, "lock_until": now + LOCK_SECONDS},
                       ConditionExpression="attribute_not_exists(pk) OR lock_until < :now",
                       ExpressionAttributeValues={":now": now})
        return True
    except _hist.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def release_lock(pk):
    _hist.delete_item(Key={"pk": pk, "sent_at": LOCK_SK})


def should_send(pk, new_price):
    q = _hist.query(KeyConditionExpression=Key("pk").eq(pk) & Key("sent_at").gt("1"),
                    ScanIndexForward=False, Limit=1)
    items = q.get("Items", [])
    if not items:
        return True, "first alert"
    last = items[0]
    last_at = datetime.fromisoformat(last["sent_at"].replace("Z", "+00:00"))
    floor = timedelta(hours=env_num("NOTIFY_FLOOR_HOURS", 24))
    if datetime.now(timezone.utc) - last_at >= floor:
        return True, "outside floor"
    last_price = Decimal(str(last.get("price", 0)))
    new = Decimal(str(new_price))
    pct = Decimal(str(env_num("REALERT_PCT", 20)))
    absd = Decimal(str(env_num("REALERT_ABS_TWD", 2000)))
    if new <= last_price * (1 - pct / 100) or (last_price - new) >= absd:
        return True, f"big drop {last_price} -> {new}"
    return False, f"last {last_price} at {last['sent_at']}"


# ---------- send ----------
def send_email(to, subj, html_body, text_body):
    cfg = resend_cfg()
    data = json.dumps({"from": cfg["from"], "to": [to], "subject": subj,
                       "html": html_body, "text": text_body}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=data, method="POST", headers={
        "Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 429 or e.code >= 500:
            raise Transient(f"resend {e.code}: {body}")
        return e.code, body  # permanent (401/403/422...) -> drop
    except urllib.error.URLError as e:
        raise Transient(f"resend network error: {e}")


def process(msg):
    email, route = msg["email"], msg["route"]
    tw = msg["cheapest"]
    o, d = _split(route)
    fare = dict(tw, origin=o, destination=d)
    pk = f"{email}#{route}"

    ok, why = should_send(pk, tw["price"])
    if not ok:
        print(f"skipped (deduped) {pk} NT${tw['price']} - {why}")
        return
    if not acquire_lock(pk):
        print(f"skipped (deduped, send in progress elsewhere) {pk} NT${tw['price']}")
        return
    try:
        ok, why = should_send(pk, tw["price"])  # re-check: another worker may have just sent
        if not ok:
            print(f"skipped (deduped) {pk} NT${tw['price']} - {why}")
            return
        _send(msg, email, route, fare, tw, pk, why)
    finally:
        release_lock(pk)


def _send(msg, email, route, fare, tw, pk, why):
    usd = (msg.get("cheapest_usd") or {}).get("price")
    marker = None
    try:
        marker = json.loads(_sm.get_secret_value(SecretId="flight/travelpayouts")["SecretString"]).get("marker")
    except Exception:
        pass
    status, body = send_email(email, subject(fare),
                              render_html(fare, msg["target_price"], marker, usd),
                              render_text(fare, msg["target_price"], marker, usd))
    if 200 <= status < 300:
        _hist.put_item(Item={"pk": pk, "sent_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                             "email": email, "route": route, "price": Decimal(str(tw["price"])),
                             "currency": "TWD"})
        print(f"sent {pk} NT${tw['price']} ({why}) resend={body}")
    else:
        print(f"dropped (permanent {status}) {pk}: {body}")


def handler(event, context):
    for rec in event.get("Records", []):
        process(json.loads(rec["body"]))
    return {"ok": True}
