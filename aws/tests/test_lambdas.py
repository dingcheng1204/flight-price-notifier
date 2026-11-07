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


# ---------------- save: dates ----------------
D = {"depart_from": "2026-12-10", "depart_to": "2026-12-20", "return_from": "2026-12-15", "return_to": "2026-12-31"}


def call_d(mod, dates, **extra):
    body = {"email": "a@b.com", "origin": "TPE", "destination": "NRT", "target_price": 9000, **extra}
    if dates is not ...:
        body["dates"] = dates
    return call(mod, body)


def test_save_with_dates_then_target_only_update_keeps_them(aws, monkeypatch):
    save = load("flight-save-subscription")
    monkeypatch.setattr(save, "today_taipei", lambda: TODAY)
    code, body = call_d(save, D)
    assert code == 200 and body["dates"] == D
    call_d(save, ..., target_price=8000)  # no "dates" key -> keep
    item = aws.Table("subscriptions").get_item(Key={"email": "a@b.com", "route": "TPE-NRT"})["Item"]
    assert int(item["target_price"]) == 8000 and {k: item[k] for k in D} == D
    call_d(save, None)  # explicit null -> clear
    item = aws.Table("subscriptions").get_item(Key={"email": "a@b.com", "route": "TPE-NRT"})["Item"]
    assert not any(k in item for k in D)


@pytest.mark.parametrize("patch,msg", [
    ({"depart_from": "2026-11-06"}, "before today"),                               # yesterday
    ({"depart_to": "2026-12-09"}, "end on or after"),                              # depart range reversed
    ({"return_to": "2026-12-14"}, "end on or after"),                              # return range reversed
    ({"return_from": "2026-12-10"}, "after the departure"),                        # same day as depart_from
    ({"return_from": "2026-12-05", "return_to": "2026-12-08"}, "after the departure"),  # before departure
    ({"depart_to": "2027-02-10"}, "at most 60 days"),
    ({"depart_from": "2027-11-20", "depart_to": "2027-11-25", "return_from": "2027-11-26", "return_to": "2027-11-30"}, "within 365"),
    ({"depart_from": "2026/12/10"}, "YYYY-MM-DD"),
    ({"return_to": None}, "YYYY-MM-DD"),
])
def test_save_rejects_bad_dates(aws, monkeypatch, patch, msg):
    save = load("flight-save-subscription")
    monkeypatch.setattr(save, "today_taipei", lambda: TODAY)
    code, body = call_d(save, {**D, **patch})
    assert code == 400 and msg in body["error"]


def test_save_accepts_today_and_overlapping_ranges(aws, monkeypatch):
    save = load("flight-save-subscription")
    monkeypatch.setattr(save, "today_taipei", lambda: TODAY)
    # depart today..Nov 20, return Nov 8..Nov 25 (ranges overlap; each fare still needs return > depart)
    code, _ = call_d(save, {"depart_from": "2026-11-07", "depart_to": "2026-11-20",
                            "return_from": "2026-11-08", "return_to": "2026-11-25"})
    assert code == 200
    assert call_d(save, "not-an-object")[0] == 400


# ---------------- parser ----------------
class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


TODAY = __import__("datetime").date(2026, 11, 7)


def v3(dep, ret, price, o="TSA", d="HND", airline="CI", city_o="TPE", city_d="TYO"):
    """One /v3/prices_for_dates item. dep/ret are YYYY-MM-DD."""
    return {"origin": city_o, "origin_airport": o, "destination": city_d, "destination_airport": d,
            "price": price, "airline": airline, "departure_at": f"{dep}T18:25:00+08:00",
            "return_at": f"{ret}T07:55:00+09:00" if ret else None, "transfers": 0}


def fake_v3(table, calls=None):
    """table: {(currency, dep_month, ret_month_or_None): [items]}"""
    from urllib.parse import parse_qs, urlparse

    def _open(req, timeout=10):
        q = {k: v[0] for k, v in parse_qs(urlparse(req.full_url).query).items()}
        key = (q["currency"], q["departure_at"], q.get("return_at"))
        if calls is not None:
            calls.append(key)
        items = table.get(key)
        body = {"success": True, "data": items or [], "currency": q["currency"]}
        return FakeResp(json.dumps(body).encode())
    return _open


def queued():
    q = boto3.client("sqs")
    url = q.get_queue_url(QueueName="flight-fare-queue")["QueueUrl"]
    return [json.loads(m["Body"]) for m in q.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])]


def sub(t, email, o, d, target, dates=None):
    item = {"email": email, "route": f"{o}-{d}", "origin": o, "destination": d, "target_price": target}
    if dates:
        item.update(dict(zip(["depart_from", "depart_to", "return_from", "return_to"], dates)))
    t.put_item(Item=item)


def test_parser_undated_uses_next_month_and_exact_airports(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    t = aws.Table("subscriptions")
    sub(t, "hi@x.com", "TSA", "HND", 15000)
    sub(t, "lo@x.com", "TSA", "HND", 10000)
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({
        ("twd", "2026-12", None): [v3("2026-12-17", "2026-12-22", 14935),
                                   v3("2026-12-03", "2026-12-08", 9000, o="TPE"),   # wrong airport (TPE, not TSA)
                                   v3("2026-12-04", "2026-12-09", 9500, d="NRT")],  # wrong airport (NRT, not HND)
        ("usd", "2026-12", None): [v3("2026-12-17", "2026-12-22", 470)]}, calls))
    r = parser.handler({"origin": "TSA", "destination": "HND", "route": "TSA-HND"}, None)
    assert r["matched"] == 1
    msgs = queued()
    assert [m["email"] for m in msgs] == ["hi@x.com"]
    assert msgs[0]["cheapest"]["price"] == 14935 and msgs[0]["cheapest_usd"]["price"] == 470
    assert "window" not in msgs[0]
    assert ("twd", "2026-12", None) in calls


def test_parser_respects_date_window(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    t = aws.Table("subscriptions")
    # wants to leave Dec 10-20 and come back Dec 20-31
    sub(t, "win@x.com", "TPE", "NRT", 10000, ("2026-12-10", "2026-12-20", "2026-12-20", "2026-12-31"))
    items = [v3("2026-12-01", "2026-12-17", 8063, o="TPE", d="NRT"),   # cheapest overall but departs Dec 1 -> outside
             v3("2026-12-17", "2026-12-22", 8790, o="TPE", d="NRT"),   # inside
             v3("2026-12-12", "2026-12-13", 7000, o="TPE", d="NRT"),   # departs inside, returns Dec 13 -> outside
             v3("2026-12-16", "2026-12-28", 13054, o="TPE", d="NRT")]  # inside but pricier
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({("twd", "2026-12", "2026-12"): items}))
    assert parser.handler({"origin": "TPE", "destination": "NRT"}, None)["matched"] == 1
    m = queued()[0]
    assert m["cheapest"]["price"] == 8790 and m["cheapest"]["depart_date"].startswith("2026-12-17")
    assert m["window"] == {"depart_from": "2026-12-10", "depart_to": "2026-12-20",
                           "return_from": "2026-12-20", "return_to": "2026-12-31"}
    assert "cheapest_usd" not in m  # USD query returned nothing -> TWD only


def test_parser_window_cheapest_above_target_is_not_sent(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "a@x.com", "TPE", "NRT", 8500,
        ("2026-12-10", "2026-12-20", "2026-12-20", "2026-12-31"))
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({("twd", "2026-12", "2026-12"): [
        v3("2026-12-01", "2026-12-17", 8063, o="TPE", d="NRT"),     # below target but outside window
        v3("2026-12-17", "2026-12-22", 8790, o="TPE", d="NRT")]}))  # in window but above target
    assert parser.handler({"origin": "TPE", "destination": "NRT"}, None)["matched"] == 0
    assert queued() == []


def test_parser_queries_every_month_pair_across_months(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "a@x.com", "ICN", "HKG", 9500,
        ("2026-12-25", "2027-01-05", "2026-12-30", "2027-01-10"))
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({
        ("twd", "2026-12", "2027-01"): [v3("2026-12-31", "2027-01-02", 9264, o="ICN", d="HKG", city_o="SEL", city_d="HKG")]},
        calls))
    assert parser.handler({"origin": "ICN", "destination": "HKG"}, None)["matched"] == 1
    twd_calls = sorted(c for c in calls if c[0] == "twd")
    # dep Dec+Jan x ret Dec+Jan, skipping return-month-before-departure-month (Jan/Dec)
    assert twd_calls == [("twd", "2026-12", "2026-12"), ("twd", "2026-12", "2027-01"), ("twd", "2027-01", "2027-01")]


def test_parser_skips_expired_and_clamps_to_today(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    t = aws.Table("subscriptions")
    sub(t, "old@x.com", "TSA", "HND", 99999, ("2026-10-01", "2026-10-10", "2026-10-05", "2026-10-15"))   # expired
    sub(t, "now@x.com", "TSA", "HND", 99999, ("2026-11-01", "2026-11-20", "2026-11-10", "2026-11-30"))   # partly past
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({("twd", "2026-11", "2026-11"): [
        v3("2026-11-05", "2026-11-12", 5000),    # departs before today (Nov 7) -> ignored
        v3("2026-11-09", "2026-11-15", 6000)]}, calls))
    assert parser.handler({"origin": "TSA", "destination": "HND"}, None)["matched"] == 1
    m = queued()[0]
    assert m["email"] == "now@x.com" and m["cheapest"]["price"] == 6000
    assert all(c[1] != "2026-10" for c in calls)


def test_parser_api_errors_are_noop(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "a@x.com", "HKG", "TSA", 99999)

    def boom(req, timeout=10):
        raise parser.urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
    monkeypatch.setattr(parser.urllib.request, "urlopen", boom)
    assert parser.handler({"origin": "HKG", "destination": "TSA"}, None)["matched"] == 0


def test_parser_legacy_city_rows_match_city_codes(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "a@x.com", "TPE", "TYO", 9000)
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3({("twd", "2026-12", None): [
        v3("2026-12-01", "2026-12-17", 8063, o="TPE", d="NRT")]}))  # destination city TYO
    assert parser.handler({"origin": "TPE", "destination": "TYO"}, None)["matched"] == 1


def fake_v3_full(table, calls=None):
    """table: {(origin, destination, one_way, dep_month, ret_month_or_None): [items]} (TWD only)."""
    from urllib.parse import parse_qs, urlparse

    def _open(req, timeout=10):
        q = {k: v[0] for k, v in parse_qs(urlparse(req.full_url).query).items()}
        key = (q["origin"], q["destination"], q["one_way"] == "true", q["departure_at"], q.get("return_at"))
        if calls is not None:
            calls.append(key)
        items = table.get(key) if q["currency"] == "twd" else None
        return FakeResp(json.dumps({"success": True, "data": items or []}).encode())
    return _open


def ow(day, price, o="TSA", d="HND", airline="TW"):
    city = {"TSA": "TPE", "TPE": "TPE", "HND": "TYO", "NRT": "TYO", "HKG": "HKG", "ICN": "SEL"}
    return {"origin": city[o], "origin_airport": o, "destination": city[d], "destination_airport": d,
            "price": price, "airline": airline, "departure_at": f"{day}T08:00:00+08:00", "return_at": None}


LONG_TRIP = ("2026-11-15", "2026-11-22", "2026-12-26", "2027-01-03")


def test_one_way_pair_when_no_round_trip_in_window(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "me@x.com", "TSA", "HND", 25000, LONG_TRIP)
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3_full({
        # round trips exist, but none return Dec 26 - Jan 3
        ("TSA", "HND", False, "2026-11", "2026-12"): [v3("2026-11-27", "2026-12-01", 16204, airline="BR")],
        ("TSA", "HND", True, "2026-11", None): [ow("2026-11-18", 8729), ow("2026-11-12", 7000),   # Nov 12 outside
                                               ow("2026-11-15", 13820, airline="AE")],
        ("HND", "TSA", True, "2026-12", None): [ow("2026-12-31", 12407, "HND", "TSA", "BR"),
                                               ow("2026-12-20", 8123, "HND", "TSA", "BR")],  # Dec 20 outside
        ("HND", "TSA", True, "2027-01", None): [ow("2027-01-02", 13000, "HND", "TSA", "CI")],
    }, calls))
    assert parser.handler({"origin": "TSA", "destination": "HND"}, None)["matched"] == 1
    m = queued()[0]
    assert m["kind"] == "one_way_pair"
    assert m["cheapest"]["price"] == 8729 + 12407 and m["cheapest"]["airline"] == "TW + BR"
    assert [l["price"] for l in m["legs"]] == [8729, 12407]
    assert m["legs"][0]["depart_date"].startswith("2026-11-18") and m["legs"][1]["depart_date"].startswith("2026-12-31")
    assert m["window"]["return_to"] == "2027-01-03"
    # one-way queries: outbound Nov; inbound Dec + Jan (reversed direction)
    assert ("TSA", "HND", True, "2026-11", None) in calls
    assert ("HND", "TSA", True, "2026-12", None) in calls and ("HND", "TSA", True, "2027-01", None) in calls


def test_one_way_pair_above_target_is_not_sent(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "me@x.com", "TSA", "HND", 20000, LONG_TRIP)
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3_full({
        ("TSA", "HND", True, "2026-11", None): [ow("2026-11-18", 8729)],
        ("HND", "TSA", True, "2026-12", None): [ow("2026-12-31", 12407, "HND", "TSA", "BR")]}))
    assert parser.handler({"origin": "TSA", "destination": "HND"}, None)["matched"] == 0
    assert queued() == []


def test_one_way_pair_requires_inbound_after_outbound(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    # overlapping ranges: depart Dec 10-20, return Dec 12-25
    sub(aws.Table("subscriptions"), "me@x.com", "TPE", "NRT", 30000, ("2026-12-10", "2026-12-20", "2026-12-12", "2026-12-25"))
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3_full({
        ("TPE", "NRT", True, "2026-12", None): [ow("2026-12-18", 4000, "TPE", "NRT"), ow("2026-12-11", 6000, "TPE", "NRT")],
        ("NRT", "TPE", True, "2026-12", None): [ow("2026-12-13", 3000, "NRT", "TPE"),   # before the Dec 18 outbound
                                               ow("2026-12-22", 5000, "NRT", "TPE")]}))
    assert parser.handler({"origin": "TPE", "destination": "NRT"}, None)["matched"] == 1
    m = queued()[0]
    # candidates: 18->22 = 9000, 11->13 = 9000, 11->22 = 11000; 18->13 is invalid
    assert m["cheapest"]["price"] == 9000
    d0, d1 = m["legs"][0]["depart_date"][:10], m["legs"][1]["depart_date"][:10]
    assert d1 > d0


def test_round_trip_in_window_wins_over_one_way_fallback(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "me@x.com", "TPE", "NRT", 10000, ("2026-12-10", "2026-12-20", "2026-12-20", "2026-12-31"))
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3_full({
        ("TPE", "NRT", False, "2026-12", "2026-12"): [v3("2026-12-17", "2026-12-22", 8790, o="TPE", d="NRT")],
        ("TPE", "NRT", True, "2026-12", None): [ow("2026-12-15", 1000, "TPE", "NRT")],
        ("NRT", "TPE", True, "2026-12", None): [ow("2026-12-25", 1000, "NRT", "TPE")]}, calls))
    assert parser.handler({"origin": "TPE", "destination": "NRT"}, None)["matched"] == 1
    m = queued()[0]
    assert "kind" not in m and m["cheapest"]["price"] == 8790
    assert not any(c[2] for c in calls)  # no one-way queries were needed


def test_undated_subscription_never_uses_one_way_fallback(aws, monkeypatch):
    parser = load("flight-parser")
    monkeypatch.setattr(parser, "today_taipei", lambda: TODAY)
    sub(aws.Table("subscriptions"), "me@x.com", "HKG", "TSA", 99999)
    calls = []
    monkeypatch.setattr(parser.urllib.request, "urlopen", fake_v3_full({}, calls))
    assert parser.handler({"origin": "HKG", "destination": "TSA"}, None)["matched"] == 0
    assert not any(c[2] for c in calls)


def test_one_way_pair_email(aws, monkeypatch):
    n = load("flight-fare-notification")
    sent = []
    monkeypatch.setattr(n, "send_email", lambda to, subj, h, t: (sent.append((subj, h, t)), (200, '{"id":"x"}'))[1])
    msg = {"email": "me@x.com", "route": "TSA-HND", "target_price": 25000, "kind": "one_way_pair",
           "cheapest": {"price": 21136, "currency": "TWD", "airline": "TW + BR",
                        "depart_date": "2026-11-18T08:00:00+08:00", "return_date": "2026-12-31T08:00:00+09:00"},
           "legs": [{"price": 8729, "currency": "TWD", "airline": "TW", "depart_date": "2026-11-18T08:00:00+08:00"},
                    {"price": 12407, "currency": "TWD", "airline": "BR", "depart_date": "2026-12-31T08:00:00+09:00"}],
           "window": dict(zip(["depart_from", "depart_to", "return_from", "return_to"], LONG_TRIP))}
    n.process(msg)
    subj, html_body, text = sent[0]
    assert subj == "✈️ 台北松山 → 東京羽田 降價通知！NT$21,136 已達標（兩張單程合計）"
    assert "兩張單程票合計，可能是不同航空公司，需要分開訂購" in text and "兩張單程票合計" in html_body
    assert "去程（台北松山 → 東京羽田）：2026-11-18　TW　NT$8,729" in text
    assert "回程（東京羽田 → 台北松山）：2026-12-31　BR　NT$12,407" in text
    assert "https://www.aviasales.com/search/TSA1811HND1" in text and "https://www.aviasales.com/search/HND3112TSA1" in text
    assert "訂購去程" in html_body and "訂購回程" in html_body
    # recorded in history at the combined price -> dedup works on the sum
    h = aws.Table("notification_history").scan()["Items"]
    assert [int(i["price"]) for i in h if i["sent_at"] != "0000-lock"] == [21136]


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
    win = {"depart_from": "2026-12-10", "depart_to": "2026-12-20", "return_from": "2026-12-20", "return_to": "2026-12-31"}
    line = "你的日期區間：去程 2026-12-10 ~ 2026-12-20，回程 2026-12-20 ~ 2026-12-31"
    assert line in n.render_text(fare, 15000, window=win) and line in n.render_html(fare, 15000, window=win)
    assert "日期區間" not in n.render_text(fare, 15000)


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
