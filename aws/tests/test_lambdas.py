"""Local tests for the Lambdas, using moto (no real AWS calls).

    pip install "moto[dynamodb,secretsmanager,sqs]" boto3 pytest
    python3 -m pytest aws/tests -q
"""
import importlib.util
import io
import json
import os
import pathlib
import threading

import boto3
import pytest
from moto import mock_aws

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
LAMBDAS = pathlib.Path(__file__).resolve().parents[1] / "lambdas"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), LAMBDAS / name / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def aws():
    with mock_aws():
        d = boto3.client("dynamodb")
        for t, pk, sk in [("subscriptions", "email", "route"), ("notification_history", "pk", "sent_at")]:
            d.create_table(TableName=t, BillingMode="PAY_PER_REQUEST",
                           AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"},
                                                 {"AttributeName": sk, "AttributeType": "S"}],
                           KeySchema=[{"AttributeName": pk, "KeyType": "HASH"},
                                      {"AttributeName": sk, "KeyType": "RANGE"}])
        sm = boto3.client("secretsmanager")
        sm.create_secret(Name="flight/travelpayouts", SecretString=json.dumps({"token": "t"}))
        sm.create_secret(Name="flight/resend", SecretString=json.dumps({"api_key": "k", "from": "f"}))
        boto3.client("sqs").create_queue(QueueName="flight-fare-queue")
        yield boto3.resource("dynamodb")


# ---------------- save / delete ----------------
def call(mod, body, method="POST"):
    r = mod.handler({"body": json.dumps(body), "requestContext": {"http": {"method": method}}}, None)
    return r["statusCode"], json.loads(r["body"])


@pytest.mark.parametrize("o,d", [("TSA", "HND"), ("HND", "TSA"), ("TPE", "NRT"), ("NRT", "TPE"),
                                 ("HKG", "ICN"), ("ICN", "HKG"), ("TSA", "HKG"), ("NRT", "ICN")])
def test_save_valid_routes(aws, o, d):
    save = load("flight-save-subscription")
    code, body = call(save, {"email": "A@B.com ", "origin": o.lower(), "destination": d, "target_price": "9999.6"})
    assert code == 200 and body["route"] == f"{o}-{d}" and body["target_price"] == 10000
    item = aws.Table("subscriptions").get_item(Key={"email": "a@b.com", "route": f"{o}-{d}"})["Item"]
    assert item["origin"] == o and item["destination"] == d and item["currency"] == "TWD"
    assert "subscription_status" not in item


@pytest.mark.parametrize("o,d,msg", [("TSA", "TSA", "different"), ("TSA", "TPE", "different"),
                                     ("HND", "NRT", "different"), ("TPE", "KIX", "one of"),
                                     ("", "HND", "one of"), ("TSA", None, "one of")])
def test_save_rejects_bad_routes(aws, o, d, msg):
    save = load("flight-save-subscription")
    code, body = call(save, {"email": "a@b.com", "origin": o, "destination": d, "target_price": 5000})
    assert code == 400 and msg in body["error"]


@pytest.mark.parametrize("tp", [0, -1, "abc", None, "NaN", "Infinity", 2000000])
def test_save_rejects_bad_price(aws, tp):
    save = load("flight-save-subscription")
    code, _ = call(save, {"email": "a@b.com", "origin": "TSA", "destination": "HND", "target_price": tp})
    assert code == 400


def test_save_rejects_bad_email_and_json(aws):
    save = load("flight-save-subscription")
    assert call(save, {"email": "nope", "origin": "TSA", "destination": "HND", "target_price": 1})[0] == 400
    r = save.handler({"body": "{not json"}, None)
    assert r["statusCode"] == 400
    r = save.handler({"body": "[1,2]"}, None)
    assert r["statusCode"] == 400


def test_update_keeps_created_at(aws):
    save = load("flight-save-subscription")
    call(save, {"email": "a@b.com", "origin": "TSA", "destination": "HND", "target_price": 9000})
    t = aws.Table("subscriptions")
    t.update_item(Key={"email": "a@b.com", "route": "TSA-HND"}, UpdateExpression="SET created_at=:c",
                  ExpressionAttributeValues={":c": "2000-01-01T00:00:00+00:00"})
    call(save, {"email": "a@b.com", "origin": "TSA", "destination": "HND", "target_price": 7000})
    item = t.get_item(Key={"email": "a@b.com", "route": "TSA-HND"})["Item"]
    assert item["created_at"] == "2000-01-01T00:00:00+00:00" and int(item["target_price"]) == 7000


def test_legacy_plan_and_city_codes_still_work(aws):
    save = load("flight-save-subscription")
    assert call(save, {"email": "a@b.com", "plan_name": "tokyo", "target_price": 9000})[1]["route"] == "TPE-TYO"
    # updating an existing legacy row from the new UI sends origin/destination
    assert call(save, {"email": "a@b.com", "origin": "TPE", "destination": "SEL", "target_price": 7000})[0] == 200


def test_delete(aws):
    save = load("flight-save-subscription")
    call(save, {"email": "a@b.com", "origin": "TSA", "destination": "HND", "target_price": 9000})
    code, body = call(save, {"email": "a@b.com", "route": "tsa-hnd"}, method="DELETE")
    assert code == 200 and body["deleted"] == "TSA-HND"
    assert "Item" not in aws.Table("subscriptions").get_item(Key={"email": "a@b.com", "route": "TSA-HND"})
    assert call(save, {"email": "a@b.com", "route": "bad"}, method="DELETE")[0] == 400


# ---------------- parser ----------------
class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(responses):
    def _open(req, timeout=10):
        cur = "usd" if "currency=usd" in req.full_url else "twd"
        return FakeResp(json.dumps(responses[cur]).encode())
    return _open


def test_parser_reads_city_keyed_response_and_matches(aws, monkeypatch):
    parser = load("flight-parser")
    t = aws.Table("subscriptions")
    t.put_item(Item={"email": "hi@x.com", "route": "TSA-HND", "origin": "TSA", "destination": "HND", "target_price": 15000})
    t.put_item(Item={"email": "lo@x.com", "route": "TSA-HND", "origin": "TSA", "destination": "HND", "target_price": 10000})
    t.put_item(Item={"email": "other@x.com", "route": "TPE-NRT", "origin": "TPE", "destination": "NRT", "target_price": 99999})
    offer = {"airline": "CI", "departure_at": "2026-12-17T18:25:00+08:00", "return_at": "2026-12-22T07:55:00+09:00"}
    # destination asked: HND, but the API keys the result by city "TYO"
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_urlopen({
        "twd": {"success": True, "data": {"TYO": {"0": dict(offer, price=14935), "1": dict(offer, price=16000)}}},
        "usd": {"success": True, "data": {"TYO": {"0": dict(offer, price=470)}}}}))
    r = parser.handler({"origin": "TSA", "destination": "HND", "route": "TSA-HND"}, None)
    assert r == {"ok": True, "route": "TSA-HND", "matched": 1, "cheapest_twd": 14935}
    q = boto3.client("sqs")
    msgs = q.receive_message(QueueUrl=q.get_queue_url(QueueName="flight-fare-queue")["QueueUrl"],
                             MaxNumberOfMessages=10)["Messages"]
    assert len(msgs) == 1
    body = json.loads(msgs[0]["Body"])
    assert body["email"] == "hi@x.com" and body["cheapest"]["price"] == 14935 and body["cheapest_usd"]["price"] == 470


def test_parser_usd_failure_still_sends_twd_only(aws, monkeypatch):
    parser = load("flight-parser")
    aws.Table("subscriptions").put_item(Item={"email": "a@x.com", "route": "ICN-HKG", "origin": "ICN",
                                              "destination": "HKG", "target_price": 9000})
    offer = {"airline": "UO", "departure_at": "2026-12-10T15:45:00+09:00", "return_at": "2026-12-14T21:05:00+08:00"}
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_urlopen({
        "twd": {"success": True, "data": {"HKG": {"0": dict(offer, price=6179)}}},
        "usd": {"success": False, "data": {}}}))
    assert parser.handler({"origin": "ICN", "destination": "HKG"}, None)["matched"] == 1
    q = boto3.client("sqs")
    m = q.receive_message(QueueUrl=q.get_queue_url(QueueName="flight-fare-queue")["QueueUrl"])["Messages"][0]
    body = json.loads(m["Body"])
    assert "cheapest_usd" not in body and body["route"] == "ICN-HKG"


def test_parser_no_fare_is_noop(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_urlopen({
        "twd": {"success": True, "data": {}}, "usd": {"success": True, "data": {}}}))
    assert parser.handler({"origin": "HKG", "destination": "TSA"}, None)["matched"] == 0


# ---------------- wrapper ----------------
def test_wrapper_dispatches_distinct_subscribed_routes(aws, monkeypatch):
    wrapper = load("flight-parser-wrapper")
    t = aws.Table("subscriptions")
    for e, o, d in [("a@x", "TSA", "HND"), ("b@x", "TSA", "HND"), ("a@x", "HND", "TSA"), ("c@x", "TPE", "TYO")]:
        t.put_item(Item={"email": e, "route": f"{o}-{d}", "origin": o, "destination": d, "target_price": 1})
    calls = []
    monkeypatch.setattr(wrapper, "_lambda", type("L", (), {"invoke": lambda self, **kw: calls.append(json.loads(kw["Payload"]))})())
    r = wrapper.handler({}, None)
    assert r["dispatched"] == 3
    assert sorted(c["route"] for c in calls) == ["HND-TSA", "TPE-TYO", "TSA-HND"]


# ---------------- notification ----------------
def test_notification_labels_and_booking_link():
    n = load("flight-fare-notification")
    fare = {"price": 14935, "airline": "CI", "origin": "TSA", "destination": "HND",
            "depart_date": "2026-12-17T18:25:00+08:00", "return_date": "2026-12-22T07:55:00+09:00"}
    assert n.subject(fare) == "✈️ 台北松山 → 東京羽田 降價通知！NT$14,935 已達標"
    assert n.booking_url(fare) == "https://www.aviasales.com/search/TSA1712HND22121"
    assert "約 US$470" in n.render_html(fare, 15000, usd_price=470)
    assert "US$" not in n.render_text(fare, 15000)


def test_notification_dedup_lock_under_concurrency(aws, monkeypatch):
    n = load("flight-fare-notification")
    sent, lock = [], threading.Lock()

    def fake_send(*a):
        import time
        time.sleep(0.2)
        with lock:
            sent.append(a)
        return 200, '{"id":"x"}'

    monkeypatch.setattr(n, "send_email", fake_send)
    msg = {"email": "a@x.com", "route": "HND-TSA", "target_price": 15000,
           "cheapest": {"price": 14055, "currency": "TWD", "airline": "CI",
                        "depart_date": "2026-12-10T07:55:00+09:00", "return_date": "2026-12-17T18:25:00+08:00"}}
    ths = [threading.Thread(target=n.process, args=(msg,)) for _ in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert len(sent) == 1
    n.process(msg)
    assert len(sent) == 1  # within 24h floor
    n.process(dict(msg, cheapest=dict(msg["cheapest"], price=11000)))
    assert len(sent) == 2  # >= NT$2,000 drop re-alerts
