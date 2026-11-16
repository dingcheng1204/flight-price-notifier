"""flight-status-notification: SQS consumer for subscription status emails (M2).

One consumer for every status change, routed by message["event_type"]:
  welcome  - first charge succeeded, the subscription is active
  cancel   - user cancelled; service continues until current_period_end_date
  expired  - ECPay stopped after 6 failed renewals
Sent once per (event_type, merchant_trade_no): the accounts row remembers what was sent.
429/5xx/network -> raise (SQS redelivers); other 4xx -> log and drop.
"""
import html, json, os, urllib.request, urllib.error
import boto3

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
SITE_URL = os.environ.get("SITE_URL", "https://flight.dinxnai.com").rstrip("/")
_sm = boto3.client("secretsmanager")
_accounts = boto3.resource("dynamodb").Table(os.environ.get("ACCOUNTS_TABLE", "accounts"))
_resend = None


class Transient(Exception):
    pass


def resend_cfg():
    global _resend
    if _resend is None:
        _resend = json.loads(_sm.get_secret_value(SecretId="flight/resend")["SecretString"])
    return _resend


def _page(lines, button=None):
    e = html.escape
    body = "".join(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.6;">{e(l)}</p>' for l in lines)
    btn = (f'<p style="margin:20px 0;"><a href="{e(button[1])}" style="display:inline-block;background:#111;color:#fff;'
           f'text-decoration:none;padding:12px 20px;border-radius:8px;font-size:15px;">{e(button[0])}</a></p>'
           if button else "")
    return (f'<!doctype html><html><body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Helvetica,'
            f'Arial,sans-serif;color:#111;background:#fff;"><div style="max-width:480px;margin:0 auto;">{body}{btn}'
            f'<p style="margin:16px 0 0;font-size:12px;color:#777;">— FlightDin 機票降價通知</p></div></body></html>')


def render(msg):
    """(subject, html, text) for a status message."""
    kind, app = msg["event_type"], f"{SITE_URL}/app"
    if kind == "welcome":
        amt, nxt = msg.get("amount"), msg.get("current_period_end_date")
        lines = ["你的 FlightDin 訂閱已開通，謝謝你的支持！",
                 f"方案：每月 NT${int(amt):,}，以信用卡定期定額扣款。" if amt else "方案：每月定期定額扣款。",
                 f"本期有效至 {nxt}，之後每月自動續訂。" if nxt else "之後每月自動續訂。",
                 "從現在起，你設定的所有航線只要票價達到目標價，就會寄降價通知給你。",
                 "要新增航線、調整目標價或取消訂閱，都可以在網站上操作。"]
        return "✅ FlightDin 訂閱已開通", _page(lines, ("管理我的航線", app)), "\n".join(lines + ["", app])
    if kind == "cancel":
        end = msg.get("current_period_end_date")
        lines = ["你的 FlightDin 訂閱已取消，之後不會再扣款。",
                 f"已付費的這一期仍然有效：{end} 之前你還是會收到降價通知。" if end else "已付費的這一期仍然有效。",
                 "想再回來的話，到期後在網站上重新訂閱即可。"]
        return "FlightDin 訂閱已取消", _page(lines, ("前往網站", app)), "\n".join(lines + ["", app])
    if kind == "expired":
        lines = ["你的 FlightDin 訂閱已結束：信用卡連續扣款失敗，綠界已停止這筆定期定額。",
                 "降價通知已暫停。更新信用卡後在網站上重新訂閱，就會恢復通知。"]
        return "FlightDin 訂閱已結束（扣款失敗）", _page(lines, ("重新訂閱", app)), "\n".join(lines + ["", app])
    raise ValueError(f"unknown event_type {kind!r}")


def send_email(to, subj, html_body, text_body):
    cfg = resend_cfg()
    data = json.dumps({"from": cfg["from"], "to": [to], "subject": subj,
                       "html": html_body, "text": text_body}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=data, method="POST", headers={
        "Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 429 or e.code >= 500:
            raise Transient(f"resend {e.code}: {body}")
        return e.code, body
    except urllib.error.URLError as e:
        raise Transient(f"resend network error: {e}")


def process(msg):
    kind, email = msg.get("event_type"), str(msg.get("email", "")).lower()
    if kind not in ("welcome", "cancel", "expired") or not email:
        print(f"dropped malformed message {msg}")
        return
    mark = f"{kind}_sent_for"
    trade = msg.get("merchant_trade_no") or "-"
    acct = _accounts.get_item(Key={"email": email}).get("Item") or {}
    if acct.get(mark) == trade:
        print(f"skipped {kind} {email} trade={trade}: already sent")
        return
    subj, h, t = render(msg)
    status, body = send_email(email, subj, h, t)
    if 200 <= status < 300:
        _accounts.update_item(Key={"email": email}, UpdateExpression=f"SET {mark}=:t",
                              ExpressionAttributeValues={":t": trade})
        print(f"sent {kind} {email} trade={trade} resend={body}")
    else:
        print(f"dropped (permanent {status}) {kind} {email}: {body}")


def handler(event, context):
    for rec in event.get("Records", []):
        process(json.loads(rec["body"]))
    return {"ok": True}
