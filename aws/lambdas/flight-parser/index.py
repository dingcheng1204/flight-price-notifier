"""flight-parser: one route per invocation.

For every subscriber of the route, find the cheapest round trip inside *their* window and
enqueue them if it is at or below their target (TWD gate; USD added when the same flight is
also found in a USD query). M1: no payment-status filter.

Window per subscriber:
  - dated:   departure in [depart_from, depart_to] and return in [return_from, return_to]
             (departure clamped to today; expired windows are skipped)
  - undated: departure any day next month, any return (the original M1 behaviour)

Fares come from Travelpayouts /aviasales/v3/prices_for_dates, queried per
(departure month, return month) pair. It returns cached prices that people searched recently,
so not every date has a price.
"""
import json, os, urllib.request, urllib.parse, urllib.error
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Attr

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
TAIPEI = timezone(timedelta(hours=8))
MAX_PAIRS = 12  # safety cap on month-pair queries per currency per run

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_table = boto3.resource("dynamodb").Table("subscriptions")
_token = None
_qurl = None


def token():
    global _token
    if _token is None:
        _token = json.loads(_sm.get_secret_value(SecretId="flight/travelpayouts")["SecretString"])["token"]
    return _token


def qurl():
    global _qurl
    if _qurl is None:
        _qurl = _sqs.get_queue_url(QueueName=os.environ.get("FARE_QUEUE", "flight-fare-queue"))["QueueUrl"]
    return _qurl


def today_taipei():
    return datetime.now(TAIPEI).date()


def next_month(today):
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(y, m, 1)


def month_end(d):
    nm = next_month(d)
    return nm - timedelta(days=1)


def months_between(a, b):
    """YYYY-MM strings for every month touched by [a, b]."""
    out, cur = [], date(a.year, a.month, 1)
    while cur <= b:
        out.append(cur.strftime("%Y-%m"))
        cur = next_month(cur)
    return out


def window_for(item, today):
    """Return (depart_from, depart_to, return_from|None, return_to|None) or None if expired."""
    if item.get("depart_from"):
        df, dt = date.fromisoformat(item["depart_from"]), date.fromisoformat(item["depart_to"])
        rf, rt = date.fromisoformat(item["return_from"]), date.fromisoformat(item["return_to"])
        df = max(df, today)
        if dt < df:
            return None
        return df, dt, rf, rt
    nm = next_month(today)
    return nm, month_end(nm), None, None


def month_pairs(win):
    df, dt, rf, rt = win
    dms = months_between(df, dt)
    if rf is None:
        return [(m, None) for m in dms]
    rms = months_between(rf, rt)
    return [(dm, rm) for dm in dms for rm in rms if rm >= dm]


def fetch(origin, destination, dep_month, ret_month, tok, currency):
    """List of offers (dicts) for one month pair. [] on any failure."""
    q = {"origin": origin, "destination": destination, "departure_at": dep_month,
         "one_way": "false", "sorting": "price", "limit": 1000, "currency": currency, "token": tok}
    if ret_month:
        q["return_at"] = ret_month
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"travelpayouts {currency} HTTP {e.code} for {origin}-{destination} {dep_month}/{ret_month}")
        return []
    except Exception as e:  # network / timeout / bad JSON
        print(f"travelpayouts {currency} error for {origin}-{destination}: {e}")
        return []
    if not body.get("success"):
        return []
    data = body.get("data") or []
    if isinstance(data, dict):  # defensive: older shapes keyed by city
        data = [o for v in data.values() for o in (v.values() if isinstance(v, dict) else [v])]
    out = []
    for o in data:
        if not isinstance(o, dict) or "price" not in o or not o.get("departure_at"):
            continue
        # exact airport match (TSA vs TPE, HND vs NRT); city codes match legacy TYO/SEL rows
        if origin not in (o.get("origin_airport"), o.get("origin")):
            continue
        if destination not in (o.get("destination_airport"), o.get("destination")):
            continue
        out.append(o)
    return out


def in_window(offer, win):
    df, dt, rf, rt = win
    dep = date.fromisoformat(offer["departure_at"][:10])  # local date at the departure airport
    if not (df <= dep <= dt):
        return False
    if rf is None:
        return True
    if not offer.get("return_at"):
        return False
    ret = date.fromisoformat(offer["return_at"][:10])
    return rf <= ret <= rt and ret > dep


def subscribers(route):
    items, kw = [], {"FilterExpression": Attr("route").eq(route)}
    while True:
        page = _table.scan(**kw)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _fare(o, currency):
    return {"price": o["price"], "currency": currency, "airline": o.get("airline"),
            "depart_date": o.get("departure_at"), "return_date": o.get("return_at")}


def handler(event, context):
    origin, destination = event["origin"], event["destination"]
    route = event.get("route") or f"{origin}-{destination}"
    today = today_taipei()

    subs = []
    for it in subscribers(route):
        win = window_for(it, today)
        if win is None:
            print(f"{route} {it['email']} window expired - skipping")
            continue
        subs.append((it, win))
    if not subs:
        print(f"{route} no active subscribers")
        return {"ok": True, "route": route, "matched": 0}

    pairs = sorted({p for _, w in subs for p in month_pairs(w)}, key=lambda p: (p[0], p[1] or ""))
    if len(pairs) > MAX_PAIRS:
        print(f"{route} {len(pairs)} month pairs, capping at {MAX_PAIRS}")
        pairs = pairs[:MAX_PAIRS]

    tok = token()
    twd = {p: fetch(origin, destination, p[0], p[1], tok, "twd") for p in pairs}
    usd = {}

    matched = 0
    for it, win in subs:
        cands = [o for p in month_pairs(win) for o in twd.get(p, []) if in_window(o, win)]
        label = it["email"] + (f" [{win[0]}..{win[1]} / {win[2]}..{win[3]}]" if win[2] else " [next month]")
        if not cands:
            print(f"{route} {label}: no fare in window")
            continue
        best = min(cands, key=lambda o: o["price"])
        tp = Decimal(str(it.get("target_price", 0)))
        print(f"{route} {label}: cheapest in window {best['price']} TWD ({best.get('airline')} "
              f"{best['departure_at'][:10]}->{(best.get('return_at') or '')[:10]}), target {tp}")
        if tp < Decimal(str(best["price"])):
            continue

        body = {"email": it["email"], "route": route, "target_price": int(tp),
                "cheapest": _fare(best, "TWD")}
        if it.get("plan_name"):
            body["plan_name"] = it["plan_name"]
        if win[2]:
            body["window"] = {"depart_from": win[0].isoformat(), "depart_to": win[1].isoformat(),
                              "return_from": win[2].isoformat(), "return_to": win[3].isoformat()}
        # USD best-effort: same flight (same departure/return timestamps) in a USD query
        pair = (best["departure_at"][:7], (best.get("return_at") or "")[:7] or None)
        if win[2] is None:
            pair = (pair[0], None)
        if pair not in usd:
            usd[pair] = fetch(origin, destination, pair[0], pair[1], tok, "usd")
        same = [o for o in usd[pair] if o.get("departure_at") == best["departure_at"]
                and o.get("return_at") == best.get("return_at")]
        if same:
            body["cheapest_usd"] = _fare(min(same, key=lambda o: o["price"]), "USD")

        _sqs.send_message(QueueUrl=qurl(), MessageBody=json.dumps(body))
        matched += 1
    print(f"{route} enqueued {matched} match(es)")
    return {"ok": True, "route": route, "matched": matched}

# end of flight-parser
