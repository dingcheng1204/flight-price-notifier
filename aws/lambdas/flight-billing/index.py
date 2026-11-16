"""flight-billing: the per-user ECPay 信用卡定期定額 subscription (M2).

One subscription per USER (DynamoDB `accounts`, PK email). While it is paid, every route the
user watches in `subscriptions` gets alerts; the routes themselves are free to add.

Routes (HTTP API v2, routeKey):
  GET  /account       [JWT] status + price
  POST /checkout      [JWT] -> pending_payment + auto-submit HTML form to the ECPay cashier
  POST /cancel        [JWT] -> ECPay CreditCardPeriodAction Action=Cancel, status cancelled (grace)
  POST /ecpay-return  ECPay S2S, first charge     -> active      (source of truth)
  POST /ecpay-period  ECPay S2S, 2nd charge on    -> extend / count failures / expired after 6
  ANY  /ecpay-result  ECPay browser POST (OrderResultURL) -> 302 back to the site (no state change)

Lifecycle: pending_payment -> active -> cancelled (still served until current_period_end) -> expired.
All timestamps compared as strings use TS_FMT (UTC, fixed width) so lexicographic order == time order.
stdlib + boto3 only (CheckMacValue is hashlib).
"""
import base64, hashlib, hmac, json, os, re, secrets, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
import html as _html
import boto3

TAIPEI = timezone(timedelta(hours=8))
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SITE_URL = os.environ.get("SITE_URL", "https://flight.dinxnai.com").rstrip("/")
# where /ecpay-result may send the browser back to (prod site, Vercel previews, local dev)
RETURN_ORIGIN_RE = re.compile(os.environ.get(
    "RETURN_ORIGIN_RE",
    r"^(https://flight\.dinxnai\.com|https://flight-price-notifier-[a-z0-9-]+\.vercel\.app|http://localhost:\d+)$"))
ITEM_NAME = "FlightDin 機票降價通知月費"
TRADE_DESC = "FlightDin monthly subscription"

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_accounts = boto3.resource("dynamodb").Table(os.environ.get("ACCOUNTS_TABLE", "accounts"))
_cfg = None
_status_q = None


# ---------- config ----------
def ecpay_cfg():
    global _cfg
    if _cfg is None:
        _cfg = json.loads(_sm.get_secret_value(SecretId="flight/ecpay")["SecretString"])
    return _cfg


def ecpay_host():
    return "payment.ecpay.com.tw" if ecpay_cfg().get("env") == "prod" else "payment-stage.ecpay.com.tw"


def amount():
    return int(ecpay_cfg()["amount"])


def status_queue():
    global _status_q
    if _status_q is None:
        _status_q = _sqs.get_queue_url(QueueName=os.environ.get("STATUS_QUEUE", "flight-status-queue"))["QueueUrl"]
    return _status_q


def period_settings():
    """PeriodType/Frequency/ExecTimes. Default monthly; set PERIOD_TYPE=D EXEC_TIMES=2 to test renewals."""
    return (os.environ.get("PERIOD_TYPE", "M"), int(os.environ.get("FREQUENCY", "1")),
            int(os.environ.get("EXEC_TIMES", "999")))


# ---------- time ----------
def now_utc():
    return datetime.now(timezone.utc)


def ts(dt):
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def parse_ts(s):
    return datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc)


def taipei_date(s):
    return parse_ts(s).astimezone(TAIPEI).date().isoformat()


def add_period(dt, period_type="M", frequency=1):
    """dt + one billing period. Months clamp to the last day (ECPay bills the 31st on the 30th/28th)."""
    if period_type == "D":
        return dt + timedelta(days=frequency)
    months = frequency * (12 if period_type == "Y" else 1)
    m = dt.month - 1 + months
    y, m = dt.year + m // 12, m % 12 + 1
    last = (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return dt.replace(year=y, month=m, day=min(dt.day, last))


def parse_process_date(s):
    """ECPay 'yyyy/MM/dd HH:mm:ss' (Taipei) -> aware datetime, or None."""
    try:
        return datetime.strptime(s, "%Y/%m/%d %H:%M:%S").replace(tzinfo=TAIPEI)
    except (TypeError, ValueError):
        return None


# ---------- CheckMacValue (AIO, SHA256) ----------
def ecpay_url_encode(s):
    e = urllib.parse.quote_plus(str(s)).replace("~", "%7E").lower()
    for o, n in (("%2d", "-"), ("%5f", "_"), ("%2e", "."), ("%21", "!"), ("%2a", "*"), ("%28", "("), ("%29", ")")):
        e = e.replace(o, n)
    return e


def gen_cmv(params, hash_key, hash_iv):
    # keep empty-string fields (ECPay signs CustomField3= / CustomField4=); drop only CheckMacValue
    items = {k: v for k, v in params.items() if k != "CheckMacValue"}
    body = "&".join(f"{k}={items[k]}" for k in sorted(items, key=str.lower))
    raw = f"HashKey={hash_key}&{body}&HashIV={hash_iv}"
    return hashlib.sha256(ecpay_url_encode(raw).encode()).hexdigest().upper()


def verify_cmv(params):
    cfg = ecpay_cfg()
    got = str(params.get("CheckMacValue", "")).upper()
    return hmac.compare_digest(got, gen_cmv(params, cfg["hash_key"], cfg["hash_iv"]))


# ---------- http helpers ----------
def _json(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def _text(body, code=200):
    return {"statusCode": code, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": body}


def _raw_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return raw


def form_params(event):
    return {k: v[0] for k, v in urllib.parse.parse_qs(_raw_body(event), keep_blank_values=True).items()}


def json_body(event):
    try:
        data = json.loads(_raw_body(event) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def auth_email(event, fallback=None):
    """Email from the API Gateway JWT authorizer (Supabase access token).
    Until REQUIRE_JWT=true, fall back to a client-supplied email (transition period only)."""
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    email = str(claims.get("email") or "").strip().lower()
    if not email and os.environ.get("REQUIRE_JWT", "false").lower() != "true":
        email = str(fallback or "").strip().lower()
    return email if EMAIL_RE.match(email) else None


def api_base(event):
    host = (event.get("requestContext") or {}).get("domainName")
    return os.environ.get("API_URL", f"https://{host}").rstrip("/")


# ---------- account helpers ----------
def get_account(email):
    return _accounts.get_item(Key={"email": email}).get("Item")


def is_served(acct, now=None):
    """True while alerts should go out: active, or cancelled and still inside the paid period."""
    if not acct:
        return False
    st = acct.get("subscription_status")
    if st == "active":
        return True
    end = acct.get("current_period_end")
    return st == "cancelled" and bool(end) and end >= ts(now or now_utc())


def public_account(acct):
    a = acct or {}
    st = a.get("subscription_status") or "none"
    end = a.get("current_period_end")
    if st == "cancelled" and end and end < ts(now_utc()):
        st = "expired"  # the wrapper flips it lazily; show the truth right away
    return {"status": st, "served": is_served(acct), "current_period_end": end,
            "current_period_end_date": a.get("current_period_end_date"),
            "amount": amount(), "currency": "TWD", "env": ecpay_cfg().get("env", "stage")}


def enqueue(event_type, email, **extra):
    _sqs.send_message(QueueUrl=status_queue(), MessageBody=json.dumps(
        dict(extra, event_type=event_type, email=email), ensure_ascii=False))


def new_trade_no():
    # <= 20 alphanumerics, unique forever at ECPay: FD + yymmddHHMMSS + 6 hex
    return "FD" + now_utc().astimezone(TAIPEI).strftime("%y%m%d%H%M%S") + secrets.token_hex(3)


# ---------- user routes ----------
def get_account_route(event):
    email = auth_email(event, (event.get("queryStringParameters") or {}).get("email"))
    if not email:
        return _json(401, {"error": "sign in required"})
    return _json(200, dict(public_account(get_account(email)), email=email))


def checkout(event):
    data = json_body(event)
    email = auth_email(event, data.get("email"))
    if not email:
        return _json(401, {"error": "sign in required"})
    if len(email) > 50:  # CustomField1 is String(50)
        return _json(400, {"error": "email too long for ECPay (max 50 characters)"})
    acct = get_account(email)
    if is_served(acct):
        return _json(409, {"error": "already subscribed", "account": public_account(acct)})

    cfg = ecpay_cfg()
    origin = str(data.get("return_origin") or "").rstrip("/")
    origin = origin if RETURN_ORIGIN_RE.match(origin) else SITE_URL
    ptype, freq, times = period_settings()
    amt = amount()
    trade_no = new_trade_no()
    base = api_base(event)
    params = {
        "MerchantID": cfg["merchant_id"], "MerchantTradeNo": trade_no,
        "MerchantTradeDate": now_utc().astimezone(TAIPEI).strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio", "ChoosePayment": "Credit", "EncryptType": "1",
        "TotalAmount": str(amt), "PeriodAmount": str(amt),
        "PeriodType": ptype, "Frequency": str(freq), "ExecTimes": str(times),
        "ItemName": ITEM_NAME, "TradeDesc": TRADE_DESC,
        "ReturnURL": f"{base}/ecpay-return", "PeriodReturnURL": f"{base}/ecpay-period",
        "OrderResultURL": f"{base}/ecpay-result",
        "CustomField1": email,
    }
    params["CheckMacValue"] = gen_cmv(params, cfg["hash_key"], cfg["hash_iv"])

    n = ts(now_utc())
    _accounts.update_item(
        Key={"email": email},
        UpdateExpression=("SET subscription_status=:p, pending_trade_no=:t, amount=:a, period_type=:pt, "
                          "frequency=:f, return_origin=:o, updated_at=:n, created_at=if_not_exists(created_at, :n)"),
        ExpressionAttributeValues={":p": "pending_payment", ":t": trade_no, ":a": amt, ":pt": ptype,
                                   ":f": freq, ":o": origin, ":n": n})
    print(f"checkout {email} trade={trade_no} amount={amt} {ptype}x{freq}/{times}")

    inputs = "".join(f'<input type="hidden" name="{_html.escape(k)}" value="{_html.escape(str(v))}">'
                     for k, v in params.items())
    page = (f'<!doctype html><html><head><meta charset="utf-8"><title>前往綠界付款…</title></head>'
            f'<body><p>正在前往綠界付款頁面…</p>'
            f'<form id="ecpay" method="post" action="https://{ecpay_host()}/Cashier/AioCheckOut/V5">{inputs}'
            f'<noscript><button type="submit">前往付款</button></noscript></form>'
            f'<script>document.getElementById("ecpay").submit()</script></body></html>')
    return {"statusCode": 200, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": page}


def ecpay_cancel(trade_no):
    """Call CreditCardPeriodAction Action=Cancel. Returns the parsed response dict."""
    cfg = ecpay_cfg()
    params = {"MerchantID": cfg["merchant_id"], "MerchantTradeNo": trade_no, "Action": "Cancel",
              "TimeStamp": str(int(now_utc().timestamp()))}
    params["CheckMacValue"] = gen_cmv(params, cfg["hash_key"], cfg["hash_iv"])
    req = urllib.request.Request(f"https://{ecpay_host()}/Cashier/CreditCardPeriodAction",
                                 data=urllib.parse.urlencode(params).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "Mozilla/5.0 (compatible; flight-notifier/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8", "replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(text, keep_blank_values=True).items()} or {"raw": text}


def cancel(event):
    data = json_body(event)
    email = auth_email(event, data.get("email"))
    if not email:
        return _json(401, {"error": "sign in required"})
    acct = get_account(email)
    if not acct or acct.get("subscription_status") != "active":
        return _json(409, {"error": "no active subscription", "account": public_account(acct)})
    trade_no = acct.get("merchant_trade_no")
    try:
        res = ecpay_cancel(trade_no)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"cancel {email} trade={trade_no}: ECPay unreachable: {e}")
        return _json(502, {"error": "payment provider unreachable, please retry"})
    code = str(res.get("RtnCode", ""))
    print(f"cancel {email} trade={trade_no}: ECPay {res}")
    # 90100150 = order not found (e.g. a stage synthetic order) -> nothing is scheduled, safe to cancel locally.
    if code != "1" and code != "90100150" and ecpay_cfg().get("env") == "prod":
        return _json(502, {"error": f"ECPay refused the cancel: {res.get('RtnMsg') or code}"})

    end = acct.get("current_period_end")
    if not end:  # activated before period tracking: grant one period from now
        end = ts(add_period(now_utc(), acct.get("period_type", "M"), int(acct.get("frequency", 1))))
    n = ts(now_utc())
    new = _accounts.update_item(
        Key={"email": email},
        UpdateExpression="SET subscription_status=:c, cancelled_at=:n, updated_at=:n, current_period_end=:e, "
                         "current_period_end_date=:d",
        ConditionExpression="subscription_status = :a",
        ExpressionAttributeValues={":c": "cancelled", ":a": "active", ":n": n, ":e": end, ":d": taipei_date(end)},
        ReturnValues="ALL_NEW")["Attributes"]
    enqueue("cancel", email, merchant_trade_no=trade_no, current_period_end_date=taipei_date(end))
    return _json(200, dict(public_account(new), email=email))


# ---------- ECPay callbacks ----------
def _verified(event, label):
    p = form_params(event)
    if p.get("MerchantID") != ecpay_cfg()["merchant_id"] or not verify_cmv(p):
        print(f"{label} REJECT CheckMacValue/MerchantID mismatch: "
              f"trade={p.get('MerchantTradeNo')} merchant={p.get('MerchantID')}")
        return None
    return p


def ecpay_return(event):
    p = _verified(event, "ecpay-return")
    if p is None:
        return _text("0|CheckMacValueInvalid", 400)
    trade, email = p.get("MerchantTradeNo"), str(p.get("CustomField1", "")).strip().lower()
    print(f"ecpay-return trade={trade} email={email} RtnCode={p.get('RtnCode')} RtnMsg={p.get('RtnMsg')} "
          f"simulate={p.get('SimulatePaid', '')}")
    if p.get("SimulatePaid") == "1":
        return _text("1|OK")  # stage 模擬付款: acknowledge, never grant
    if p.get("RtnCode") != "1" or not EMAIL_RE.match(email):
        return _text("1|OK")  # first auth failed -> order never enters the schedule; row stays pending
    acct = get_account(email) or {}
    if acct.get("subscription_status") == "active" and acct.get("merchant_trade_no") not in (None, trade):
        # two checkouts paid (e.g. two tabs): the older order keeps billing until cancelled in the 後台
        print(f"ecpay-return WARNING {email} already active on {acct.get('merchant_trade_no')}; "
              f"switching to {trade} - cancel the old order manually")
    ptype, freq = acct.get("period_type", "M"), int(acct.get("frequency", 1))
    charged = parse_process_date(p.get("PaymentDate")) or now_utc()
    end = ts(add_period(charged, ptype, freq))
    n = ts(now_utc())
    try:
        _accounts.update_item(
            Key={"email": email},
            UpdateExpression=("SET subscription_status=:a, merchant_trade_no=:t, current_period_end=:e, "
                              "current_period_end_date=:d, activated_at=:n, last_charged_at=:n, updated_at=:n, "
                              "total_success_times=:one, failed_times=:zero, period_type=if_not_exists(period_type, :pt), "
                              "frequency=if_not_exists(frequency, :f) REMOVE pending_trade_no, cancelled_at"),
            ConditionExpression="attribute_not_exists(merchant_trade_no) OR merchant_trade_no <> :t",
            ExpressionAttributeValues={":a": "active", ":t": trade, ":e": end, ":d": taipei_date(end), ":n": n,
                                       ":one": 1, ":zero": 0, ":pt": ptype, ":f": freq})
    except _accounts.meta.client.exceptions.ConditionalCheckFailedException:
        print(f"ecpay-return trade={trade} already processed")
        return _text("1|OK")
    print(f"ecpay-return {email} ACTIVE until {end}")
    enqueue("welcome", email, merchant_trade_no=trade, amount=int(p.get("TradeAmt") or acct.get("amount") or amount()),
            current_period_end_date=taipei_date(end))
    return _text("1|OK")


def ecpay_period(event):
    p = _verified(event, "ecpay-period")
    if p is None:
        return _text("0|CheckMacValueInvalid", 400)
    trade, email = p.get("MerchantTradeNo"), str(p.get("CustomField1", "")).strip().lower()
    print(f"ecpay-period trade={trade} email={email} RtnCode={p.get('RtnCode')} RtnMsg={p.get('RtnMsg')} "
          f"success={p.get('TotalSuccessTimes')} simulate={p.get('SimulatePaid', '')}")
    if p.get("SimulatePaid") == "1":
        return _text("1|OK")
    acct = get_account(email) if EMAIL_RE.match(email) else None
    if not acct or acct.get("merchant_trade_no") != trade:
        print(f"ecpay-period trade={trade}: not the account's current order - ignored")
        return _text("1|OK")
    n = ts(now_utc())
    if p.get("RtnCode") == "1":
        times = int(p.get("TotalSuccessTimes") or 0)
        if times and times <= int(acct.get("total_success_times", 0)):
            print(f"ecpay-period trade={trade} charge #{times} already processed")
            return _text("1|OK")
        charged = parse_process_date(p.get("ProcessDate")) or now_utc()
        end = ts(add_period(charged, acct.get("period_type", "M"), int(acct.get("frequency", 1))))
        end = max(end, acct.get("current_period_end") or end)
        # a renewal never resurrects a cancelled/expired row; it only extends the paid-through date
        _accounts.update_item(
            Key={"email": email},
            UpdateExpression=("SET current_period_end=:e, current_period_end_date=:d, last_charged_at=:n, "
                              "updated_at=:n, total_success_times=:s, failed_times=:zero"),
            ExpressionAttributeValues={":e": end, ":d": taipei_date(end), ":n": n, ":s": times or
                                       int(acct.get("total_success_times", 0)) + 1, ":zero": 0})
        print(f"ecpay-period {email} renewed until {end}")
        return _text("1|OK")
    fails = int(acct.get("failed_times", 0)) + 1
    if fails >= 6 and acct.get("subscription_status") in ("active", "cancelled"):
        _accounts.update_item(Key={"email": email},
                              UpdateExpression="SET subscription_status=:x, failed_times=:f, updated_at=:n",
                              ExpressionAttributeValues={":x": "expired", ":f": fails, ":n": n})
        print(f"ecpay-period {email} EXPIRED after {fails} failed charges")
        enqueue("expired", email, merchant_trade_no=trade)
    else:
        _accounts.update_item(Key={"email": email}, UpdateExpression="SET failed_times=:f, updated_at=:n",
                              ExpressionAttributeValues={":f": fails, ":n": n})
        print(f"ecpay-period {email} charge failed ({fails}/6) - ECPay will retry")
    return _text("1|OK")


def ecpay_result(event):
    """OrderResultURL arrives as a browser POST; a static SPA would answer 405. Redirect only."""
    p = form_params(event) if event.get("body") else (event.get("queryStringParameters") or {})
    ok = p.get("RtnCode") == "1"
    origin = SITE_URL
    email = str(p.get("CustomField1", "")).strip().lower()
    if EMAIL_RE.match(email):
        stored = (get_account(email) or {}).get("return_origin", "")
        if RETURN_ORIGIN_RE.match(stored):
            origin = stored
    return {"statusCode": 302, "headers": {"Location": f"{origin}/app?purchase={'success' if ok else 'failed'}"},
            "body": ""}


ROUTES = {
    "GET /account": get_account_route,
    "POST /checkout": checkout,
    "POST /cancel": cancel,
    "POST /ecpay-return": ecpay_return,
    "POST /ecpay-period": ecpay_period,
    "ANY /ecpay-result": ecpay_result,
    "POST /ecpay-result": ecpay_result,
    "GET /ecpay-result": ecpay_result,
}


def handler(event, context):
    fn = ROUTES.get(event.get("routeKey", ""))
    if fn is None:
        return _json(404, {"error": "not found"})
    return fn(event)
