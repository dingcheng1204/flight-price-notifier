# AWS backend (Milestone 1)

Source of the Lambdas behind the fare notifier. Region `us-east-1`. Supabase is auth-only; all app data lives in DynamoDB. M1 has **no payment guard** (`subscription_status` arrives in M2).

```
Product site (Vercel) ──POST /subscribe, GET /subscriptions──▶ API Gateway (flight-api)
                                                                 ├─▶ flight-save-subscription ─▶ DynamoDB subscriptions
                                                                 └─▶ flight-list-subscriptions ◀─┘
EventBridge rate(30 min) ─▶ flight-parser-wrapper ─(reads S3 flight-routes.json, async fan-out)─▶ flight-parser ×N
flight-parser: Travelpayouts cheapest (TWD gate, USD best-effort) → scan subscriptions → SQS flight-fare-queue
flight-fare-notification (SQS, BatchSize 1): dedup vs notification_history → Resend email → write history
```

| Lambda | Trigger | Notes |
|---|---|---|
| `flight-save-subscription` | `POST /subscribe` `{email, plan_name: tokyo\|seoul, target_price}` | Upsert row (PK `email`, SK `route`), keeps `created_at` |
| `flight-list-subscriptions` | `GET /subscriptions?email=` | Query by email. **No auth** — trusts the client email (fine for the course, not production) |
| `flight-parser-wrapper` | EventBridge `flight-price-check` | env `CONFIG_BUCKET` |
| `flight-parser` | async from wrapper | next month `YYYY-MM`; enqueues `{email, route, target_price, cheapest(TWD), cheapest_usd?}` |
| `flight-fare-notification` | SQS `flight-fare-queue` | env `NOTIFY_FLOOR_HOURS=24`, `REALERT_PCT=20`, `REALERT_ABS_TWD=2000` |
| `flight-seed` | manual invoke | deploy bridge only (base64 → S3) |

**Secrets** (Secrets Manager): `flight/travelpayouts` `{token, marker?}`, `flight/resend` `{api_key, from, test_to}`.
**Shared role**: `flight-lambda-role`, inline policy `flight-data` — `put-role-policy` replaces the whole policy, so always read-merge-write.

## Dedup

`notification_history`: PK `pk = "{email}#{route}"`, SK `sent_at` (ISO-8601 UTC, millisecond precision).
Send if no history, or last send is older than the floor, or the new price dropped ≥20% / ≥NT$2,000.
Concurrent SQS workers are serialized per `pk` by a lock row (`sent_at = "0000-lock"`, conditional put, 90 s expiry); history reads use `sent_at > "1"` to skip it. History is written only after a Resend 2xx. 429/5xx re-raise (SQS redelivers); other 4xx are logged and dropped.

## Redeploy a function

The AWS MCP sandbox can't build zips, and the shell can't reach S3, so code goes through `flight-seed`:

1. `python3 aws/build_zip.py flight-parser` → prints `md5` + base64.
2. Invoke `flight-seed` with `{"bucket":"flight-config-<ACCOUNT_ID>","key":"lambda/parser.zip","b64":"<base64>","ct":"application/zip"}`.
3. `s3api head-object` → **ETag must equal the md5** (a size check misses same-length corruption).
4. `lambda update-function-code --function-name flight-parser --s3-bucket flight-config-<ACCOUNT_ID> --s3-key lambda/parser.zip`.

S3 keys in use: `lambda/save_subscription.zip`, `lambda/list_subscriptions.zip`, `lambda/parser.zip`, `lambda/wrapper.zip`, `lambda/notification.zip`, `flight-routes.json` (copy of `aws/flight-routes.json`).
With a normal AWS CLI you can skip the bridge: `aws lambda update-function-code --zip-file fileb://aws/build/<name>.zip`.
