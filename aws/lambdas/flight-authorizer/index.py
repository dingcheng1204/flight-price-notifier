"""flight-authorizer: HTTP API Lambda authorizer (payload 2.0, simple responses).

API Gateway's built-in JWT authorizer only accepts RS256, but Supabase signs access tokens with
ES256. So this authorizer asks Supabase who the token belongs to (GET /auth/v1/user) and passes
the user's email to the route Lambdas as requestContext.authorizer.lambda.email.
API Gateway caches the answer per Authorization header (ResultTtlInSeconds on the authorizer).

env: SUPABASE_URL, SUPABASE_KEY (the publishable key - public by design)
"""
import json, os, urllib.request, urllib.error

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"


def deny(why):
    print("deny:", why)
    return {"isAuthorized": False}


def handler(event, context):
    auth = (event.get("headers") or {}).get("authorization") or ""
    if not auth.lower().startswith("bearer ") or len(auth) < 20:
        return deny("no bearer token")
    req = urllib.request.Request(
        os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/user",
        headers={"Authorization": auth, "apikey": os.environ["SUPABASE_KEY"], "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            user = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return deny(f"supabase {e.code}")  # 401/403: expired or invalid token
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return deny(f"supabase unreachable: {e}")
    email = str(user.get("email") or "").strip().lower()
    if not email or user.get("aud") != "authenticated":
        return deny("no email / wrong audience")
    return {"isAuthorized": True, "context": {"email": email, "sub": str(user.get("id", ""))}}
