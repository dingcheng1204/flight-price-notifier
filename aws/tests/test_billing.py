"""M2 tests: per-user ECPay subscription (flight-billing, flight-status-notification) and the paywall
in wrapper / parser / fare-notification. moto only, no network.

    python3 -m pytest aws/tests -q
"""
import json
import pathlib
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from test_lambdas import load, fake_v3, v3, sub, queued, TODAY  # noqa: F401  (also sets AWS env vars)

STAGE = {"merchant_id": "3002607", "hash_key": "pwFHCqoQZGmho4w6", "hash_iv": "EkRm7iFT261dpevs",
         "env": "stage", "amount": "300"}
VECTORS = pathlib.Path(__file__).resolve().parents[2] / ".claude/skills/ecpay/test-vectors/checkmacvalue.json"
TS = "%Y-%m-%dT%H:%M:%SZ"
API = "abc.execute-api.us-east-1.amazonaws.com"


def ts(dt):
    return dt.astimezone(timezone.utc).strftime(TS)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PAYWALL", "on")
    monkeypatch.delenv("REQUIRE_JWT", raising=False)
    with mock_aws():
        d = boto3.client("dynamodb")
        for t, pk, sk in [("subscriptions", "email", "route"), ("notification_history", "pk", "sent_at")]:
            d.create_table(TableName=t, BillingMode="PAY_PER_REQUEST",
                           AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"},
                                                 {"AttributeName": sk, "AttributeType": "S"}],
                           KeySchema=[{"AttributeName": pk, "KeyType": "HASH"},
                                      {"AttributeName": sk, "KeyType": "RANGE"}])
        d.create_table(TableName="accounts", BillingMode="PAY_PER_REQUEST",
                       AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
                       KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}])
        sm = boto3.client("secretsmanager")
        sm.create_secret(Name="flight/ecpay", SecretString=json.dumps(STAGE))
        sm.create_secret(Name="flight/travelpayouts", SecretString=json.dumps({"token": "t"}))
        sm.create_secret(Name="flight/resend", SecretString=json.dumps({"api_key": "k", "from": "f"}))
        sqs = boto3.client("sqs")
        sqs.create_queue(QueueName="flight-status-queue")
        sqs.create_queue(QueueName="flight-fare-queue")
        yield boto3.resource("dynamodb")


def status_msgs():
    q = boto3.client("sqs")
    url = q.get_queue_url(QueueName="flight-status-queue")["QueueUrl"]
    return [json.loads(m["Body"]) for m in q.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])]


def acct(env, email):
    return env.Table("accounts").get_item(Key={"email": email}).get("Item")


def user_event(route, body=None, email="me@x.com", qs=None):
    ev = {"routeKey": route, "requestContext": {"domainName": API, "http": {"method": route.split()[0]}},
          "body": json.dumps(body or {})}
    if email:
        ev["requestContext"]["authorizer"] = {"jwt": {"claims": {"email": email, "sub": "u1"}}}
    if qs:
        ev["queryStringParameters"] = qs
    return ev


def signed_callback(b, route, **fields):
    """A form-POST callback exactly as ECPay sends it, incl. the empty CustomField2..4."""
    p = {"MerchantID": "3002607", "CustomField2": "", "CustomField3": "", "CustomField4": "", **fields}
    p["CheckMacValue"] = b.gen_cmv(p, STAGE["hash_key"], STAGE["hash_iv"])
    return {"routeKey": route, "requestContext": {"domainName": API}, "isBase64Encoded": False,
            "body": urllib.parse.urlencode(p)}


# ---------------- CheckMacValue ----------------
def test_cmv_matches_official_vectors():
    b = load("flight-billing")
    vecs = [v for v in json.loads(VECTORS.read_text())["vectors"] if v["method"] == "SHA256" and "params" in v]
    assert len(vecs) >= 5
    for v in vecs:
        assert b.gen_cmv(v["params"], v["hashKey"], v["hashIV"]) == v["expected"], v["name"]


def test_cmv_keeps_empty_fields():
    b = load("flight-billing")
    p = {"MerchantID": "3002607", "RtnCode": "1", "CustomField3": ""}
    assert b.gen_cmv(p, "k", "i") != b.gen_cmv({k: v for k, v in p.items() if v}, "k", "i")


def test_add_period_clamps_month_end():
    b = load("flight-billing")
    u = timezone.utc
    assert b.add_period(datetime(2026, 1, 31, 3, tzinfo=u)) == datetime(2026, 2, 28, 3, tzinfo=u)
    assert b.add_period(datetime(2026, 12, 15, tzinfo=u)) == datetime(2027, 1, 15, tzinfo=u)
    assert b.add_period(datetime(2026, 12, 15, tzinfo=u), "D", 1) == datetime(2026, 12, 16, tzinfo=u)


# ---------------- checkout ----------------
def test_checkout_returns_signed_recurring_form(env):
    b = load("flight-billing")
    r = b.handler(user_event("POST /checkout", {"return_origin": "https://flight-price-notifier-git-m2-x.vercel.app"}), None)
    assert r["statusCode"] == 200 and r["headers"]["Content-Type"].startswith("text/html")
    page = r["body"]
    assert 'action="https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"' in page
    fields = dict(__import__("re").findall(r'name="([^"]+)" value="([^"]*)"', page))
    fields = {k: __import__("html").unescape(v) for k, v in fields.items()}
    assert fields["TotalAmount"] == fields["PeriodAmount"] == "300"
    assert fields["PeriodType"] == "M" and fields["ExecTimes"] == "999" and fields["ChoosePayment"] == "Credit"
    assert fields["CustomField1"] == "me@x.com"
    assert fields["ReturnURL"] == f"https://{API}/ecpay-return"
    assert fields["OrderResultURL"] == f"https://{API}/ecpay-result"
    assert len(fields["MerchantTradeNo"]) <= 20 and fields["MerchantTradeNo"].isalnum()
    assert b.gen_cmv(fields, STAGE["hash_key"], STAGE["hash_iv"]) == fields["CheckMacValue"]
    a = acct(env, "me@x.com")
    assert a["subscription_status"] == "pending_payment" and a["pending_trade_no"] == fields["MerchantTradeNo"]
    assert a["return_origin"] == "https://flight-price-notifier-git-m2-x.vercel.app"


def test_checkout_rejects_unknown_origin_and_requires_login(env, monkeypatch):
    b = load("flight-billing")
    b.handler(user_event("POST /checkout", {"return_origin": "https://evil.example"}), None)
    assert acct(env, "me@x.com")["return_origin"] == "https://flight.dinxnai.com"
    monkeypatch.setenv("REQUIRE_JWT", "true")
    r = b.handler(user_event("POST /checkout", {"email": "victim@x.com"}, email=None), None)
    assert r["statusCode"] == 401


def test_checkout_409_when_already_paid(env):
    b = load("flight-billing")
    later = ts(datetime.now(timezone.utc) + timedelta(days=10))
    env.Table("accounts").put_item(Item={"email": "me@x.com", "subscription_status": "cancelled",
                                         "current_period_end": later})
    assert b.handler(user_event("POST /checkout"), None)["statusCode"] == 409


# ---------------- ReturnURL ----------------
def test_return_activates_once_and_queues_welcome(env):
    b = load("flight-billing")
    b.handler(user_event("POST /checkout"), None)
    trade = acct(env, "me@x.com")["pending_trade_no"]
    ev = signed_callback(b, "POST /ecpay-return", MerchantTradeNo=trade, RtnCode="1", RtnMsg="交易成功",
                         TradeAmt="300", PaymentDate="2026/11/16 15:00:00", CustomField1="me@x.com", Gwsr="")
    r = b.handler(ev, None)
    assert r["body"] == "1|OK" and r["headers"]["Content-Type"].startswith("text/plain")
    a = acct(env, "me@x.com")
    assert a["subscription_status"] == "active" and a["merchant_trade_no"] == trade
    assert a["current_period_end"] == "2026-12-16T07:00:00Z" and a["current_period_end_date"] == "2026-12-16"
    assert "pending_trade_no" not in a
    assert b.handler(ev, None)["body"] == "1|OK"  # ECPay resend
    msgs = status_msgs()
    assert [m["event_type"] for m in msgs] == ["welcome"]
    assert msgs[0]["amount"] == 300 and msgs[0]["current_period_end_date"] == "2026-12-16"


def test_return_rejects_bad_cmv_and_ignores_simulate_and_failures(env):
    b = load("flight-billing")
    ev = signed_callback(b, "POST /ecpay-return", MerchantTradeNo="T1", RtnCode="1", CustomField1="me@x.com")
    ev["body"] = ev["body"].replace("RtnCode=1", "RtnCode=1&Extra=x")
    r = b.handler(ev, None)
    assert r["statusCode"] == 400 and r["body"].startswith("0|")
    sim = signed_callback(b, "POST /ecpay-return", MerchantTradeNo="T1", RtnCode="1", SimulatePaid="1",
                          CustomField1="me@x.com")
    assert b.handler(sim, None)["body"] == "1|OK"
    fail = signed_callback(b, "POST /ecpay-return", MerchantTradeNo="T2", RtnCode="10100058", CustomField1="me@x.com")
    assert b.handler(fail, None)["body"] == "1|OK"
    assert acct(env, "me@x.com") is None and status_msgs() == []


def test_return_handles_base64_body(env):
    b = load("flight-billing")
    ev = signed_callback(b, "POST /ecpay-return", MerchantTradeNo="T9", RtnCode="1", CustomField1="me@x.com")
    ev["body"] = __import__("base64").b64encode(ev["body"].encode()).decode()
    ev["isBase64Encoded"] = True
    assert b.handler(ev, None)["body"] == "1|OK"
    assert acct(env, "me@x.com")["subscription_status"] == "active"


# ---------------- PeriodReturnURL ----------------
def _active(env, end="2026-12-16T07:00:00Z", **extra):
    env.Table("accounts").put_item(Item={"email": "me@x.com", "subscription_status": "active",
                                         "merchant_trade_no": "T1", "current_period_end": end,
                                         "total_success_times": 1, "failed_times": 0, "period_type": "M",
                                         "frequency": 1, **extra})


def test_period_renewal_extends_and_is_idempotent(env):
    b = load("flight-billing")
    _active(env)
    ev = signed_callback(b, "POST /ecpay-period", MerchantTradeNo="T1", RtnCode="1", TotalSuccessTimes="2",
                         ProcessDate="2026/12/16 15:00:00", CustomField1="me@x.com", Gwsr="123")
    assert b.handler(ev, None)["body"] == "1|OK"
    a = acct(env, "me@x.com")
    assert a["current_period_end"] == "2027-01-16T07:00:00Z" and a["total_success_times"] == 2
    b.handler(ev, None)
    assert acct(env, "me@x.com")["current_period_end"] == "2027-01-16T07:00:00Z"


def test_period_six_failures_expire(env):
    b = load("flight-billing")
    _active(env, failed_times=4)
    ev = signed_callback(b, "POST /ecpay-period", MerchantTradeNo="T1", RtnCode="0", CustomField1="me@x.com")
    b.handler(ev, None)
    assert acct(env, "me@x.com")["subscription_status"] == "active"  # 5th failure: ECPay retries
    b.handler(ev, None)
    assert acct(env, "me@x.com")["subscription_status"] == "expired"
    assert [m["event_type"] for m in status_msgs()] == ["expired"]


def test_period_for_other_order_is_ignored(env):
    b = load("flight-billing")
    _active(env)
    ev = signed_callback(b, "POST /ecpay-period", MerchantTradeNo="OLD", RtnCode="1", TotalSuccessTimes="5",
                         CustomField1="me@x.com")
    b.handler(ev, None)
    assert acct(env, "me@x.com")["current_period_end"] == "2026-12-16T07:00:00Z"


# ---------------- cancel ----------------
def test_cancel_keeps_paid_period(env, monkeypatch):
    b = load("flight-billing")
    _active(env)
    calls = []
    monkeypatch.setattr(b, "ecpay_cancel", lambda t: (calls.append(t), {"RtnCode": "1", "RtnMsg": "成功"})[1])
    r = b.handler(user_event("POST /cancel"), None)
    body = json.loads(r["body"])
    assert r["statusCode"] == 200 and calls == ["T1"]
    a = acct(env, "me@x.com")
    assert a["subscription_status"] == "cancelled" and a["current_period_end"] == "2026-12-16T07:00:00Z"
    assert body["status"] in ("cancelled", "expired")
    assert [(m["event_type"], m["current_period_end_date"]) for m in status_msgs()] == [("cancel", "2026-12-16")]
    assert b.handler(user_event("POST /cancel"), None)["statusCode"] == 409


def test_cancel_refused_in_prod_keeps_active(env, monkeypatch):
    b = load("flight-billing")
    _active(env)
    monkeypatch.setattr(b, "ecpay_cfg", lambda: dict(STAGE, env="prod"))
    monkeypatch.setattr(b, "ecpay_cancel", lambda t: {"RtnCode": "10100050", "RtnMsg": "x"})
    assert b.handler(user_event("POST /cancel"), None)["statusCode"] == 502
    assert acct(env, "me@x.com")["subscription_status"] == "active"


def test_cancel_stage_unknown_order_still_cancels(env, monkeypatch):
    b = load("flight-billing")
    _active(env, current_period_end=None)
    env.Table("accounts").update_item(Key={"email": "me@x.com"}, UpdateExpression="REMOVE current_period_end")
    monkeypatch.setattr(b, "ecpay_cancel", lambda t: {"RtnCode": "90100150", "RtnMsg": "不存在的訂單編號"})
    assert b.handler(user_event("POST /cancel"), None)["statusCode"] == 200
    a = acct(env, "me@x.com")
    assert a["subscription_status"] == "cancelled" and a["current_period_end"] > ts(datetime.now(timezone.utc))


# ---------------- account / result ----------------
def test_account_route(env):
    b = load("flight-billing")
    body = json.loads(b.handler(user_event("GET /account"), None)["body"])
    assert body == {"status": "none", "served": False, "current_period_end": None, "current_period_end_date": None,
                    "amount": 300, "currency": "TWD", "env": "stage", "email": "me@x.com"}
    env.Table("accounts").put_item(Item={"email": "me@x.com", "subscription_status": "cancelled",
                                         "current_period_end": "2020-01-01T00:00:00Z"})
    body = json.loads(b.handler(user_event("GET /account"), None)["body"])
    assert body["status"] == "expired" and body["served"] is False


def test_result_redirects_to_stored_origin(env):
    b = load("flight-billing")
    env.Table("accounts").put_item(Item={"email": "me@x.com",
                                         "return_origin": "https://flight-price-notifier-git-m2-x.vercel.app"})
    ev = {"routeKey": "ANY /ecpay-result", "body": "RtnCode=1&CustomField1=me%40x.com", "requestContext": {}}
    r = b.handler(ev, None)
    assert r["statusCode"] == 302
    assert r["headers"]["Location"] == "https://flight-price-notifier-git-m2-x.vercel.app/app?purchase=success"
    r = b.handler({"routeKey": "ANY /ecpay-result", "body": "RtnCode=0", "requestContext": {}}, None)
    assert r["headers"]["Location"] == "https://flight.dinxnai.com/app?purchase=failed"


# ---------------- status emails ----------------
def test_status_welcome_sent_once(env, monkeypatch):
    n = load("flight-status-notification")
    sent = []
    monkeypatch.setattr(n, "send_email", lambda to, s, h, t: (sent.append((to, s, t)), (200, '{"id":"e"}'))[1])
    msg = {"event_type": "welcome", "email": "me@x.com", "merchant_trade_no": "T1", "amount": 300,
           "current_period_end_date": "2026-12-16"}
    n.handler({"Records": [{"body": json.dumps(msg)}, {"body": json.dumps(msg)}]}, None)
    assert len(sent) == 1
    to, subj, text = sent[0]
    assert to == "me@x.com" and "已開通" in subj and "NT$300" in text and "2026-12-16" in text
    n.handler({"Records": [{"body": json.dumps(dict(msg, event_type="cancel"))}]}, None)
    assert len(sent) == 2 and "取消" in sent[1][1]


def test_status_transient_error_raises(env, monkeypatch):
    n = load("flight-status-notification")

    def boom(*a):
        raise n.Transient("resend 429")
    monkeypatch.setattr(n, "send_email", boom)
    with pytest.raises(n.Transient):
        n.handler({"Records": [{"body": json.dumps({"event_type": "welcome", "email": "me@x.com"})}]}, None)


# ---------------- paywall ----------------
def _accounts(env):
    now = datetime.now(timezone.utc)
    t = env.Table("accounts")
    t.put_item(Item={"email": "paid@x.com", "subscription_status": "active", "current_period_end": ts(now + timedelta(days=20))})
    t.put_item(Item={"email": "grace@x.com", "subscription_status": "cancelled", "current_period_end": ts(now + timedelta(days=3))})
    t.put_item(Item={"email": "lapsed@x.com", "subscription_status": "cancelled", "current_period_end": ts(now - timedelta(days=1))})
    t.put_item(Item={"email": "unpaid@x.com", "subscription_status": "pending_payment"})


def test_wrapper_dispatches_only_paid_routes_and_expires_lapsed(env, monkeypatch):
    w = load("flight-parser-wrapper")
    _accounts(env)
    t = env.Table("subscriptions")
    for e, o, d in [("paid@x.com", "TSA", "HND"), ("unpaid@x.com", "TSA", "HND"), ("grace@x.com", "HND", "TSA"),
                    ("lapsed@x.com", "TPE", "NRT"), ("unpaid@x.com", "HKG", "ICN"), ("nobody@x.com", "ICN", "HKG")]:
        t.put_item(Item={"email": e, "route": f"{o}-{d}", "origin": o, "destination": d, "target_price": 1})
    calls = []
    monkeypatch.setattr(w, "_lambda", type("L", (), {"invoke": lambda self, **kw: calls.append(json.loads(kw["Payload"]))})())
    r = w.handler({}, None)
    assert sorted(c["route"] for c in calls) == ["HND-TSA", "TSA-HND"] and r["skipped_unpaid"] == 3
    assert acct(env, "lapsed@x.com")["subscription_status"] == "expired"
    assert acct(env, "grace@x.com")["subscription_status"] == "cancelled"


def test_parser_skips_unpaid_users(env, monkeypatch):
    p = load("flight-parser")
    monkeypatch.setattr(p, "today_taipei", lambda: TODAY)
    _accounts(env)
    t = env.Table("subscriptions")
    for e in ["paid@x.com", "grace@x.com", "lapsed@x.com", "unpaid@x.com", "nobody@x.com"]:
        sub(t, e, "TSA", "HND", 20000)
    monkeypatch.setattr(p.urllib.request, "urlopen", fake_v3({("twd", "2026-12", None): [v3("2026-12-17", "2026-12-22", 14935)]}))
    assert p.handler({"origin": "TSA", "destination": "HND", "route": "TSA-HND"}, None)["matched"] == 2
    assert sorted(m["email"] for m in queued()) == ["grace@x.com", "paid@x.com"]


def test_fare_notification_rechecks_paywall(env, monkeypatch):
    n = load("flight-fare-notification")
    _accounts(env)
    sent = []
    monkeypatch.setattr(n, "send_email", lambda to, s, h, t: (sent.append(to), (200, '{"id":"x"}'))[1])
    fare = {"price": 9000, "currency": "TWD", "airline": "CI", "depart_date": "2026-12-17T18:25:00+08:00",
            "return_date": "2026-12-22T07:55:00+09:00"}
    recs = [{"body": json.dumps({"email": e, "route": "TSA-HND", "target_price": 10000, "cheapest": fare})}
            for e in ["paid@x.com", "lapsed@x.com", "unpaid@x.com"]]
    n.handler({"Records": recs}, None)
    assert sent == ["paid@x.com"]


def test_save_and_list_use_jwt_email(env, monkeypatch):
    save, lst = load("flight-save-subscription"), load("flight-list-subscriptions")
    ev = user_event("POST /subscribe", {"email": "victim@x.com", "origin": "TSA", "destination": "HND", "target_price": 9000})
    assert save.handler(ev, None)["statusCode"] == 200
    assert env.Table("subscriptions").get_item(Key={"email": "me@x.com", "route": "TSA-HND"}).get("Item")
    out = json.loads(lst.handler(user_event("GET /subscriptions", qs={"email": "victim@x.com"}), None)["body"])
    assert [s["email"] for s in out["subscriptions"]] == ["me@x.com"]
    monkeypatch.setenv("REQUIRE_JWT", "true")
    ev = user_event("POST /subscribe", {"email": "victim@x.com", "origin": "TSA", "destination": "HND", "target_price": 1}, email=None)
    assert save.handler(ev, None)["statusCode"] == 401
    assert lst.handler(user_event("GET /subscriptions", qs={"email": "victim@x.com"}, email=None), None)["statusCode"] == 401
