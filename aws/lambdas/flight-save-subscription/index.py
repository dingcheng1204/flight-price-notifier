"""flight-save-subscription
POST   /subscribe  {email, origin, destination, target_price, dates?}  -> upsert one route subscription
DELETE /subscribe  {email, route}                                      -> remove it

dates (optional): {"depart_from","depart_to","return_from","return_to"} as YYYY-MM-DD.
  - key absent  -> existing dates are kept (e.g. a target-only update)
  - null        -> dates removed (any departure next month)
  - object      -> validated and stored
(Legacy: POST {email, plan_name: tokyo|seoul, target_price} still maps to TPE-TYO / TPE-SEL.)
"""
import json, re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import boto3

TAIPEI = timezone(timedelta(hours=8))  # Taiwan has no DST
MAX_RANGE_DAYS = 60    # each range spans at most 60 days (bounds API calls per run)
MAX_AHEAD_DAYS = 365   # Travelpayouts has little data further out
DATE_KEYS = ("depart_from", "depart_to", "return_from", "return_to")

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


def today_taipei():
    return datetime.now(TAIPEI).date()


def validate_dates(raw, today=None):
    """Return (dates_dict, None) or (None, error). raw must be a dict with the four keys."""
    today = today or today_taipei()
    if not isinstance(raw, dict):
        return None, "dates must be an object"
    try:
        d = {k: date.fromisoformat(str(raw.get(k, ""))) for k in DATE_KEYS}
    except ValueError:
        return None, "dates must be YYYY-MM-DD: depart_from, depart_to, return_from, return_to"
    if d["depart_from"] < today:
        return None, "departure can't be before today"
    if d["depart_to"] < d["depart_from"] or d["return_to"] < d["return_from"]:
        return None, "each range must end on or after its start"
    if d["return_from"] <= d["depart_from"]:
        return None, "return dates must be after the departure date"
    if (d["depart_to"] - d["depart_from"]).days > MAX_RANGE_DAYS or \
       (d["return_to"] - d["return_from"]).days > MAX_RANGE_DAYS:
        return None, f"each range can span at most {MAX_RANGE_DAYS} days"
    if (d["depart_to"] - today).days > MAX_AHEAD_DAYS or (d["return_to"] - today).days > MAX_AHEAD_DAYS + MAX_RANGE_DAYS:
        return None, f"dates must be within {MAX_AHEAD_DAYS} days from today"
    return {k: v.isoformat() for k, v in d.items()}, None


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

    dates = None
    if "dates" in data and data["dates"] is not None:
        dates, err = validate_dates(data["dates"])
        if err:
            return resp(400, {"error": err})

    route = f"{origin}-{dest}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sets = ("origin=:o, destination=:d, target_price=:t, currency=:c, "
            "updated_at=:n, created_at=if_not_exists(created_at, :n)")
    values = {":o": origin, ":d": dest, ":t": tp, ":c": "TWD", ":n": now}
    expr = "SET " + sets
    if dates:
        expr += ", " + ", ".join(f"{k}=:{k}" for k in DATE_KEYS)
        values.update({f":{k}": v for k, v in dates.items()})
    elif "dates" in data:  # explicit null -> clear the window
        expr += " REMOVE " + ", ".join(DATE_KEYS)
    ddb.update_item(Key={"email": email, "route": route}, UpdateExpression=expr,
                    ExpressionAttributeValues=values)
    out = {"ok": True, "email": email, "route": route, "origin": origin,
           "destination": dest, "target_price": int(tp), "currency": "TWD"}
    if dates:
        out["dates"] = dates
    return resp(200, out)
