import json, re
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ddb = boto3.resource("dynamodb").Table("subscriptions")


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def handler(event, context):
    qs = event.get("queryStringParameters") or {}
    email = str(qs.get("email", "")).strip().lower()
    if not EMAIL_RE.match(email):
        return {"statusCode": 400, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "invalid email"})}
    items = ddb.query(KeyConditionExpression=Key("email").eq(email)).get("Items", [])
    rows = [{k: _plain(v) for k, v in it.items()} for it in items]
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"subscriptions": rows}, ensure_ascii=False)}
