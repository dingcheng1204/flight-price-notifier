"""flight-parser-wrapper: EventBridge target.
Builds the set of routes people actually subscribe to (distinct origin/destination in the
subscriptions table) and fans out one async flight-parser invocation per route.

M2 paywall (PAYWALL=on): only routes watched by at least one paying user are dispatched.
A user is served while their `accounts` row is active, or cancelled and still inside the paid
period; cancelled rows whose period has ended are flipped to expired here (lazily)."""
import json, os
from datetime import datetime, timezone
import boto3

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table("subscriptions")
_lambda = boto3.client("lambda")


def paywall_on():
    return os.environ.get("PAYWALL", "on").lower() == "on"


def now_ts():
    return datetime.now(timezone.utc).strftime(TS_FMT)


def load_accounts(emails):
    """{email: item} for the given emails (BatchGetItem, 100 keys per call)."""
    name = os.environ.get("ACCOUNTS_TABLE", "accounts")
    out, emails = {}, sorted(set(emails))
    for i in range(0, len(emails), 100):
        req = {name: {"Keys": [{"email": e} for e in emails[i:i + 100]]}}
        while req:
            r = _ddb.batch_get_item(RequestItems=req)
            for it in r.get("Responses", {}).get(name, []):
                out[it["email"]] = it
            req = r.get("UnprocessedKeys") or None
    return out


def is_served(acct, now):
    if not acct:
        return False
    st, end = acct.get("subscription_status"), acct.get("current_period_end")
    return st == "active" or (st == "cancelled" and bool(end) and end >= now)


def expire_lapsed(accounts, now):
    """cancelled + period over -> expired. Conditional, so a concurrent re-subscribe wins."""
    tbl = _ddb.Table(os.environ.get("ACCOUNTS_TABLE", "accounts"))
    for email, a in accounts.items():
        end = a.get("current_period_end")
        if a.get("subscription_status") == "cancelled" and end and end < now:
            try:
                tbl.update_item(Key={"email": email},
                                UpdateExpression="SET subscription_status=:x, updated_at=:n",
                                ConditionExpression="subscription_status = :c AND current_period_end < :n",
                                ExpressionAttributeValues={":x": "expired", ":c": "cancelled", ":n": now})
                print("expired", email, "period ended", end)
            except tbl.meta.client.exceptions.ConditionalCheckFailedException:
                pass


def subscribed_routes():
    """{route: (origin, destination, {emails})}"""
    routes, kw = {}, {"ProjectionExpression": "origin, destination, email"}
    while True:
        page = _table.scan(**kw)
        for it in page.get("Items", []):
            o, d = it.get("origin"), it.get("destination")
            if o and d:
                routes.setdefault(f"{o}-{d}", (o, d, set()))[2].add(it.get("email"))
        if "LastEvaluatedKey" not in page:
            return routes
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def handler(event, context):
    routes = subscribed_routes()
    skipped = 0
    if paywall_on():
        now = now_ts()
        accounts = load_accounts(e for _, _, es in routes.values() for e in es if e)
        expire_lapsed(accounts, now)
        paying = {e for e, a in accounts.items() if is_served(a, now)}
        for route in list(routes):
            if not routes[route][2] & paying:
                print("skip", route, "- no paying subscriber")
                del routes[route]
                skipped += 1
    for route, (o, d, _) in sorted(routes.items()):
        payload = {"origin": o, "destination": d, "route": route}
        _lambda.invoke(FunctionName=os.environ.get("PARSER_FN", "flight-parser"),
                       InvocationType="Event", Payload=json.dumps(payload).encode())
        print("dispatched", route)
    return {"ok": True, "dispatched": len(routes), "skipped_unpaid": skipped}
