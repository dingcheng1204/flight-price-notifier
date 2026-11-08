# AWS backend

Source of the Lambdas behind the fare notifier. Region `us-east-1`. Supabase is auth-only; all app data lives in DynamoDB. M1 has **no payment guard** (`subscription_status` arrives in M2).

```
Product site (Vercel) ──POST/DELETE /subscribe, GET /subscriptions──▶ API Gateway (flight-api)
                                                                        ├─▶ flight-save-subscription ─▶ DynamoDB subscriptions
                                                                        └─▶ flight-list-subscriptions ◀─┘
EventBridge rate(10 min) ─▶ flight-parser-wrapper ─(distinct routes in subscriptions, async fan-out)─▶ flight-parser ×N
flight-parser: per-subscriber window → Travelpayouts month-pair queries (TWD gate, USD best-effort,
               one-way pair fallback) → SQS flight-fare-queue
flight-fare-notification (SQS, BatchSize 1): lock + dedup vs notification_history → Resend email → write history
```

| Lambda                      | Trigger                                                                                                      | Notes                                                                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `flight-save-subscription`  | `POST /subscribe` `{email, origin, destination, target_price, dates?}`; `DELETE /subscribe` `{email, route}` | Upsert row (PK `email`, SK `route` e.g. `TSA-HND`), keeps `created_at`. Validates airports (TSA/TPE/HND/NRT/HKG/ICN, no same-city pairs) and the date window (each range ≤ `MAX_RANGE_DAYS`=60, ≤ 365 days ahead, return after departure). `dates` absent = keep, `null` = clear. Changing target/dates clears that route's alert history. Legacy `plan_name: tokyo\|seoul` still maps to `TPE-TYO` / `TPE-SEL` |
| `flight-list-subscriptions` | `GET /subscriptions?email=`                                                                                  | Query by email. **No auth** — trusts the client email (fine for the course, not production)                                                                                                                                                                                                                                                                                                                     |
| `flight-parser-wrapper`     | EventBridge `flight-price-check`, `rate(10 minutes)`                                                         | Scans `subscriptions` for distinct `origin`/`destination` and async-invokes `flight-parser` once per route                                                                                                                                                                                                                                                                                                      |
| `flight-parser`             | async from wrapper `{origin, destination, route}`                                                            | Window per subscriber: dated (departure clamped to today, expired skipped) or undated = any departure next month. One `prices_for_dates` call per (departure month, return month) pair, `MAX_PAIRS`=12. No round trip in a dated window → cheapest one-way out + later one-way back. Enqueues `{email, route, target_price, cheapest, cheapest_usd?, window?, kind?, legs?}`                                    |
| `flight-fare-notification`  | SQS `flight-fare-queue`                                                                                      | env `NOTIFY_FLOOR_HOURS=24`, `REALERT_PCT=20`, `REALERT_ABS_TWD=2000`                                                                                                                                                                                                                                                                                                                                           |
| `flight-seed`               | manual invoke                                                                                                | deploy bridge only (base64 → S3)                                                                                                                                                                                                                                                                                                                                                                                |

**Secrets** (Secrets Manager): `flight/travelpayouts` `{token, marker?}`, `flight/resend` `{api_key, from, test_to}`.
**Shared role**: `flight-lambda-role`, inline policy `flight-data` — `put-role-policy` replaces the whole policy, so always read-merge-write.

## Dedup

`notification_history`: PK `pk = "{email}#{route}"`, SK `sent_at` (ISO-8601 UTC, millisecond precision).
Send if no history, or last send is older than the floor, or the new price dropped ≥20% / ≥NT$2,000.
Concurrent SQS workers are serialized per `pk` by a lock row (`sent_at = "0000-lock"`, conditional put, 90 s expiry); history reads use `sent_at > "1"` to skip it. History is written only after a Resend 2xx. 429/5xx re-raise (SQS redelivers); other 4xx are logged and dropped.

## Tests

```sh
pip install "moto[dynamodb,secretsmanager,sqs]" boto3 pytest
python3 -m pytest aws/tests -q
```

## Read the deployed code back

`GetObject` in the AWS MCP sandbox returns the zip body as a base64 string. Copy it out, then `base64 -d > x.zip && unzip x.zip` locally. That's how you check that this folder matches what's live.

## Redeploy a function

The AWS MCP sandbox can't build zips, and the shell can't reach S3, so code goes through `flight-seed`:

1. `python3 aws/build_zip.py flight-parser` → prints `md5` + base64.
2. Invoke `flight-seed` with `{"bucket":"flight-config-<ACCOUNT_ID>","key":"lambda/parser.zip","b64":"<base64>","ct":"application/zip"}`.
3. `s3api head-object` → **ETag must equal the md5** (a size check misses same-length corruption).
4. `lambda update-function-code --function-name flight-parser --s3-bucket flight-config-<ACCOUNT_ID> --s3-key lambda/parser.zip`.

S3 keys in use: `lambda/save_subscription.zip`, `lambda/list_subscriptions.zip`, `lambda/parser.zip`, `lambda/wrapper.zip`, `lambda/notification.zip`. (`flight-routes.json` / `aws/flight-routes.json` is the M1 two-plan list; the wrapper no longer reads it.)
With a normal AWS CLI you can skip the bridge: `aws lambda update-function-code --zip-file fileb://aws/build/<name>.zip`.
