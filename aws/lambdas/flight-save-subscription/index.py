"""flight-save-subscription
POST   /subscribe  {email, origin, destination, target_price}  -> upsert one route subscription
DELETE /subscribe  {email, route}                              -> remove it
(Legacy: POST {email, plan_name: tokyo|seoul, target_price} still maps to TPE-TYO / TPE-SEL.)
"""
import json, re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import boto3

# airport -> metro area; two airports in the same metro can't form a route
AIRPORTS = {"TSA": "TPE", "TPE": "TPE", "HND": "TYO", "NRT": "TYO", "HKG": "HKG", "ICN": "SEL"}
# city codes used by M1's original two plans; accepted so existing rows can still be updated
LEGACY = {"TYO": "TYO", "SEL": "SEL"}
LEGACY_PLANS = {"tokyo": ("TPE", "TYO"), "seoul": ("TPE", "SEL")}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROUTE_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")
ddb = boto3.resource("dynamodb").Table("subscriptions")


def resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def _method(event):
    return (event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod") or "POST").upper()


def _metro(code):
    return AIRPORTS.get(code) or LEGACY.get(code)


def handler(event, context):
    try:
        raw = event.get("body") or "{}"
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return resp(400, {"error": "invalid JSON"})

    email = str(data.get("email", "")).strip().lower()
    if not EMAIL_RE.match(email):
        return resp(400, {"error": "invalid email"})

    if _method(event) == "DELETE":
        route = str(data.get("route", "")).strip().upper()
        if not ROUTE_RE.match(route):
            return resp(400, {"error": "route must look like TSA-HND"})
        ddb.delete_item(Key={"email": email, "route": route})
        return resp(200, {"ok": True, "deleted": route})

    plan = str(data.get("plan_name", "")).strip().lower()
    if plan in LEGACY_PLANS and not data.get("origin"):
        origin, dest = LEGACY_PLANS[plan]
    else:
        origin = str(data.get("origin", "")).strip().upper()
        dest = str(data.get("destination", "")).strip().upper()
    if not _metro(origin) or not _metro(dest):
        return resp(400, {"error": f"origin/destination must be one of {', '.join(AIRPORTS)}"})
    if _metro(origin) == _metro(dest):
        return resp(400, {"error": "origin and destination must be in different cities"})

    try:
        tp = Decimal(str(data.get("target_price")))
    except (InvalidOperation, TypeError):
        return resp(400, {"error": "target_price must be a number"})
    if not tp.is_finite() or tp <= 0 or tp > 1000000:
        return resp(400, {"error": "target_price must be a positive TWD amount"})
    tp = tp.quantize(Decimal("1"))

    route = f"{origin}-{dest}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ddb.update_item(
        Key={"email": email, "route": route},
        UpdateExpression=("SET origin=:o, destination=:d, target_price=:t, currency=:c, "
                          "updated_at=:n, created_at=if_not_exists(created_at, :n)"),
        ExpressionAttributeValues={":o": origin, ":d": dest, ":t": tp, ":c": "TWD", ":n": now})
    return resp(200, {"ok": True, "email": email, "route": route, "origin": origin,
                      "destination": dest, "target_price": int(tp), "currency": "TWD"})
