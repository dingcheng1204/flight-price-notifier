import json, re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import boto3

PLANS = {"tokyo": {"origin": "TPE", "destination": "TYO"},
         "seoul": {"origin": "TPE", "destination": "SEL"}}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ddb = boto3.resource("dynamodb").Table("subscriptions")


def resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context):
    try:
        raw = event.get("body") or "{}"
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return resp(400, {"error": "invalid JSON"})

    email = str(data.get("email", "")).strip().lower()
    plan = str(data.get("plan_name", "")).strip().lower()
    if not EMAIL_RE.match(email):
        return resp(400, {"error": "invalid email"})
    if plan not in PLANS:
        return resp(400, {"error": "plan_name must be tokyo or seoul"})
    try:
        tp = Decimal(str(data.get("target_price")))
    except (InvalidOperation, TypeError):
        return resp(400, {"error": "target_price must be a number"})
    if not tp.is_finite() or tp <= 0 or tp > 1000000:
        return resp(400, {"error": "target_price must be a positive TWD amount"})
    tp = tp.quantize(Decimal("1"))

    p = PLANS[plan]
    route = f"{p['origin']}-{p['destination']}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ddb.update_item(
        Key={"email": email, "route": route},
        UpdateExpression=("SET plan_name=:pl, origin=:o, destination=:d, target_price=:t, "
                          "currency=:c, updated_at=:n, created_at=if_not_exists(created_at, :n)"),
        ExpressionAttributeValues={":pl": plan, ":o": p["origin"], ":d": p["destination"],
                                   ":t": tp, ":c": "TWD", ":n": now})
    return resp(200, {"ok": True, "email": email, "route": route, "plan_name": plan,
                      "target_price": int(tp), "currency": "TWD"})
