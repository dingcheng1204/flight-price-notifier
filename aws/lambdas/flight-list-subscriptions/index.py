import json, os, re
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ddb = boto3.resource("dynamodb").Table("subscriptions")


def require_jwt():
    return os.environ.get("REQUIRE_JWT", "false").lower() == "true"


def auth_email(event, fallback=None):
    """Email of the signed-in user, set by flight-authorizer from the Supabase access token. Until REQUIRE_JWT=true
    the client-supplied email is still accepted, so the old front end keeps working."""
    authz = (event.get("requestContext") or {}).get("authorizer") or {}
    # flight-authorizer (Lambda) puts it in authorizer.lambda; a JWT authorizer would use jwt.claims
    email = str((authz.get("lambda") or {}).get("email")
                or ((authz.get("jwt") or {}).get("claims") or {}).get("email") or "").strip().lower()
    if not email and not require_jwt():
        email = str(fallback or "").strip().lower()
    return email if EMAIL_RE.match(email) else None


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def handler(event, context):
    qs = event.get("queryStringParameters") or {}
    email = auth_email(event, qs.get("email"))
    if not email:
        return {"statusCode": 401 if require_jwt() else 400, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "sign in required" if require_jwt() else "invalid email"})}
    items = ddb.query(KeyConditionExpression=Key("email").eq(email)).get("Items", [])
    rows = [{k: _plain(v) for k, v in it.items()} for it in items]
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"subscriptions": rows}, ensure_ascii=False)}
