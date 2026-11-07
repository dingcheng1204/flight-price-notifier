"""flight-parser: one route per invocation.
Fetch next month's cheapest fare (TWD gate, USD best-effort), scan subscribers
for that route, enqueue every subscriber whose target is met. M1: no status filter."""
import json, os, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Attr

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
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


def next_month():
    now = datetime.now(timezone.utc)
    y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return f"{y:04d}-{m:02d}"


def fetch_cheapest(origin, destination, month, tok, currency):
    q = urllib.parse.urlencode({"origin": origin, "destination": destination, "depart_date": month,
                                "currency": currency, "token": tok})
    req = urllib.request.Request(f"https://api.travelpayouts.com/v1/prices/cheap?{q}",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"travelpayouts {currency} HTTP {e.code} for {origin}-{destination}")
        return None
    except Exception as e:  # network / timeout
        print(f"travelpayouts {currency} error for {origin}-{destination}: {e}")
        return None
    if not body.get("success") or not body.get("data"):
        return None
    # The response is keyed by the destination *city* (HND/NRT -> "TYO", TSA -> "TPE"),
    # not the airport we asked for, so collect offers under every key.
    offers = {}
    for k, v in body["data"].items():
        for idx, o in (v or {}).items():
            if isinstance(o, dict) and "price" in o:
                offers[f"{k}:{idx}"] = o
    if not offers:
        return None
    best = min(offers.values(), key=lambda o: o["price"])
    return {"price": best["price"], "currency": currency.upper(), "airline": best.get("airline"),
            "depart_date": best.get("departure_at"), "return_date": best.get("return_at")}


def subscribers(route):
    items, kw = [], {"FilterExpression": Attr("route").eq(route)}
    while True:
        page = _table.scan(**kw)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def handler(event, context):
    origin, destination = event["origin"], event["destination"]
    route = event.get("route") or f"{origin}-{destination}"
    month = next_month()
    tok = token()

    tw = fetch_cheapest(origin, destination, month, tok, "twd")
    if not tw:
        print("no TWD fare for", route, month, "(empty/429) - skipping")
        return {"ok": True, "route": route, "matched": 0}
    us = fetch_cheapest(origin, destination, month, tok, "usd")  # may be None - never block on it
    print(f"{route} {month} cheapest {tw['price']} TWD ({tw['airline']})"
          + (f" / {us['price']} USD" if us else " / USD n/a"))

    matched = 0
    for it in subscribers(route):
        tp = it.get("target_price")
        if tp is None or Decimal(tp) < Decimal(str(tw["price"])):
            continue
        body = {"email": it["email"], "route": route, "plan_name": it.get("plan_name"),
                "target_price": int(tp),
                "cheapest": {"price": tw["price"], "currency": "TWD", "airline": tw["airline"],
                             "depart_date": tw["depart_date"], "return_date": tw["return_date"]}}
        if us:
            body["cheapest_usd"] = {"price": us["price"], "currency": "USD", "airline": us["airline"],
                                    "depart_date": us["depart_date"], "return_date": us["return_date"]}
        _sqs.send_message(QueueUrl=qurl(), MessageBody=json.dumps(body))
        matched += 1
    print(f"{route} enqueued {matched} match(es)")
    return {"ok": True, "route": route, "matched": matched, "cheapest_twd": tw["price"]}
