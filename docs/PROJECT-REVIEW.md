# Flight Price Notifier 專案盤點與建議

> 盤點日期：2026-11-08（M1 完成、M2 尚未開始）。這份文件只做紀錄，不含任何程式或雲端設定的修改。
> 架構流程圖（系統總覽、註冊、訂閱、輪詢寄信、DynamoDB 資料模型、部署路徑）在 [README › Architecture](../README.md#architecture)。

目錄

1. [用了哪些服務](#1-用了哪些服務)
2. [用過哪些 Skill](#2-用過哪些-skill)
3. [用過哪些 MCP / 工具](#3-用過哪些-mcp--工具)
4. [第三方服務與替代方案](#4-第三方服務與替代方案)
5. [架構流程圖 → README](#5-架構流程圖)
6. [建議加入的 MCP](#6-建議加入的-mcp)
7. [目前進度與建議修改](#7-目前進度與建議修改)
8. [從 0 開始重建的詳細步驟](#8-從-0-開始重建的詳細步驟)

---

## 1. 用了哪些服務

| 服務                                                       | 用途                                                    | 方案                               | 關鍵識別                                                                                                   |
| ---------------------------------------------------------- | ------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **GitHub**                                                 | 原始碼、Vercel 自動部署來源                             | Free                               | `dingcheng1204/flight-price-notifier`（main 分支 = 正式站）                                                |
| **Vercel**                                                 | 靜態 SPA 託管、HTTPS、預覽部署                          | Hobby                              | 專案 `prj_5YmQjHvmQgvK9IggDWNgXoHQmG1e`；網域 `flight.dinxnai.com`、`flight-price-notifier-ten.vercel.app` |
| **Supabase**                                               | **只做 Auth**（email + 密碼、確認信、重設密碼、JWT）    | Free（org `asmrbelqgbhpdjnjhkvh`） | 專案 `yzqirjnhldwqjjakxeko`；`public` schema 是空的；Custom SMTP 走 Resend                                 |
| **AWS**（us-east-1，帳號 `281178523875`）                  | 整個後端                                                | 用量極小，大多在免費額度內         | 見下表                                                                                                     |
| **Cloudflare**                                             | 網域註冊（`dinxnai.com`）+ DNS                          | Free                               | `flight` CNAME → Vercel（DNS only）、Resend 的 SPF/MX/DKIM、DMARC                                          |
| **Resend**                                                 | 寄降價通知信（API）＋ Supabase 註冊／重設密碼信（SMTP） | Free                               | 寄件網域 `flight.dinxnai.com`，寄件人 `FlightDin <alerts@flight.dinxnai.com>`                              |
| **Travelpayouts**（Aviasales Data API）                    | 票價來源，`v3/prices_for_dates`（48 小時快取）          | Free                               | Token 存在 `flight/travelpayouts`                                                                          |
| **Lovable**                                                | M0 時產生初版 landing page（**已完全移除**）            | —                                  | 待你手動：撤銷 Lovable GitHub App、刪除 Lovable 專案與舊 Lovable Cloud 後端                                |
| **Claude**（claude.ai Project + 遠端 Claude Code session） | 開發、部署、記錄                                        | —                                  | Project「flight-price-notifier」，文件 `claude/m1-aws-resources.md`                                        |

AWS 細項：

| AWS 服務                    | 資源                                                                                         | 備註（2026-11-08 唯讀盤點）                                                |
| --------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| API Gateway v2（HTTP API）  | `flight-api`，3 條 route                                                                     | 全部 `AuthorizationType: NONE`；`$default` stage 沒設 throttling；CORS `*` |
| Lambda                      | 6 個：save-subscription、list-subscriptions、parser-wrapper、parser、fare-notification、seed | 全部 Python 3.12、128 MB、x86_64；timeout 10–60 s                          |
| DynamoDB                    | `subscriptions`、`notification_history`                                                      | On-demand、沒有 GSI、沒有 TTL                                              |
| SQS                         | `flight-fare-queue`                                                                          | VisibilityTimeout 180 s、保留 4 天、SSE 開啟、**沒有 DLQ**                 |
| EventBridge                 | 規則 `flight-price-check`                                                                    | `rate(10 minutes)` → wrapper                                               |
| Secrets Manager             | `flight/travelpayouts`、`flight/resend`                                                      | 每個每月約 US$0.40                                                         |
| S3                          | `flight-config-281178523875`                                                                 | Lambda zip、舊的 `flight-routes.json`                                      |
| IAM                         | 角色 `flight-lambda-role`，inline policy `flight-data`                                       | 6 個 Lambda 共用一個角色                                                   |
| CloudWatch Logs             | 7 個 log group                                                                               | **保留期限都是「永不過期」**；`flight-resend-test` 是測試留下的            |
| CloudWatch Alarms / Budgets | 無                                                                                           | **沒有任何告警、沒有預算通知**                                             |
| CloudFormation              | `flight-seed` stack                                                                          | 只有部署橋接 Lambda 用 CFN 建，其他資源都是手動建立                        |

## 2. 用過哪些 Skill

來源都是課程 repo [`uopsdod/claude-2-flight-price-notifier`](https://github.com/uopsdod/claude-2-flight-price-notifier)，各 milestone 放在不同分支。

| Skill                                                                | 分支 / 位置                    | 用途                                                              | 狀態                                                     |
| -------------------------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------- | -------------------------------------------------------- |
| `m0-landing-and-signin`                                              | 本 repo `.claude/skills/`      | Lovable 產生 landing page → GitHub → Vercel → 換成自己的 Supabase | ✅ 完成（之後改成純 Vite SPA、移除 Lovable）             |
| `m0-landing-and-signin-checklist`                                    | 本 repo                        | M0 驗收                                                           | ✅                                                       |
| `supabase-best-practice`                                             | 本 repo                        | 規則：Supabase **只做登入**，不放 app 資料                        | ✅ 遵守（Lovable 時期的 `profiles`/`price_alerts` 已刪） |
| `m1-flight-price-checker-prerequisites`                              | `m1-flight-price-checker` 分支 | 檢查 Travelpayouts / Resend 金鑰、AWS 帳號                        | ✅                                                       |
| `m1-flight-price-checker`                                            | 同上                           | 建 DynamoDB、5 個 Lambda、API、S3、SQS、EventBridge、Resend 寄信  | ✅ 完成，並超出 skill（見 §7）                           |
| `m1-flight-price-checker-checklist`                                  | 同上                           | M1 驗收（A1…L3）                                                  | ✅ 全部通過（H3 USD 失敗路徑無法實際觸發）               |
| `m1-3-email-on-target-prerequisites`                                 | `m1-3-email-on-target` 分支    | 舊版拆分的 M1.3 前置檢查                                          | ✅ 跑過檢查；主 skill 已被 M1 涵蓋，不需再跑             |
| `aws-best-practice`、`resend-best-practice`、`lovable-best-practice` | `m1-flight-price-checker` 分支 | M1 skill 參照的規則文件                                           | 參照用；`lovable-best-practice` 已從本 repo 刪除         |
| M2（paywall／付款）                                                  | 課程 repo 後續分支             | 加 `subscription_status`、付款                                    | ⏳ 尚未開始                                              |

> 注意：本 repo 目前只保留 M0 的兩個 skill 和 `supabase-best-practice`。M1 的 skill 只存在課程 repo；要重跑請從那個分支取得。

## 3. 用過哪些 MCP / 工具

| MCP / 工具                                                 | 做過的事                                                                                                                                |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **AWS MCP**（`run_script`，boto3 沙盒）                    | 建立／查詢所有 AWS 資源、invoke Lambda、讀 CloudWatch Logs、`flight-seed` 部署橋接。限制：沙盒擋 `zipfile`/`base64`，所以才有 seed 橋接 |
| **Supabase MCP**                                           | 套用 migration（`drop_unused_lovable_tables`）、security advisors、產生 TypeScript 型別、查 auth 使用者數                               |
| **Vercel MCP**                                             | 綁定網域、查部署狀態、設定 env（Production/Preview/Development）                                                                        |
| **GitHub**（Claude Code 遠端 session 的 `add_repo` + git） | clone、commit、push（Vercel 自動部署）                                                                                                  |
| **claude.ai Projects**                                     | 專案文件 `claude/m1-aws-resources.md`（資源清單、部署筆記）                                                                             |
| **Memory**                                                 | 跨對話記住專案決策                                                                                                                      |
| WebSearch / WebFetch（內建，非 MCP）                       | 測 Travelpayouts token、查替代票價來源                                                                                                  |
| 內建瀏覽器 / Claude in Chrome                              | 本專案**沒用到**（Cloudflare、Resend、Supabase Dashboard 設定都是你手動做的）                                                           |
| Notion MCP                                                 | 已連線但本專案沒用到                                                                                                                    |

## 4. 第三方服務與替代方案

| 現在用的                                            | 可替代                                                                                                                                                                                                                                                                                                      | 取捨                                                                                                                      |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **Travelpayouts**（快取票價，免費，可一次掃整個月） | Ignav（即時，前 1,000 次免費，之後約 US$2 / 1,000 次）、Duffel（即時，但為售票商設計，不成交的搜尋要付費）、SerpApi Google Flights（免費每月 250 次）、開源 `fast-flights`（Google Flights 爬蟲，違反 ToS、容易被擋）。Amadeus Self-Service 已於 2026-07 關閉、Skyscanner 只給合作夥伴（11/8 先前對話查證） | 「日期區間」掃描只有 Travelpayouts 免費撐得住。建議**兩層**：Travelpayouts 找候選 → 即時 API 只驗證命中的那一組日期再寄信 |
| **Resend**（寄信 API + SMTP）                       | **AWS SES**（同帳號、最便宜、可用 IAM 不用 API key；需申請離開 sandbox）、Postmark、SendGrid、Mailgun、Brevo                                                                                                                                                                                                | 全搬到 SES 可少一個廠商、少一把金鑰；Resend 的優點是 DX 好、Cloudflare 一鍵驗證網域                                       |
| **Supabase Auth**                                   | **AWS Cognito**（跟 API Gateway JWT authorizer 原生整合，全部在 AWS）、Clerk、Auth0、Firebase Auth、自架 Supabase                                                                                                                                                                                           | 留 Supabase 最省事；M2 若要「全 AWS」可評估 Cognito，但要重做登入 UI 與遷移使用者                                         |
| **Vercel**（靜態託管）                              | **Cloudflare Pages**（網域已在 Cloudflare，少一個平台）、AWS Amplify Hosting 或 S3 + CloudFront、Netlify、GitHub Pages                                                                                                                                                                                      | 純靜態 SPA，任何一家都可；Vercel 的預覽部署和 MCP 整合目前最順                                                            |
| **Cloudflare DNS**                                  | Route 53（約 US$0.50/月/zone）                                                                                                                                                                                                                                                                              | 沒必要換                                                                                                                  |
| **Lovable**（已移除）                               | v0、Bolt，或直接用 Claude Code                                                                                                                                                                                                                                                                              | 已不依賴                                                                                                                  |
| （自管）手動建立的 AWS 資源                         | SAM / CDK / Terraform / SST                                                                                                                                                                                                                                                                                 | 見 §7「基礎設施即程式碼」                                                                                                 |

## 5. 架構流程圖

已放在 [README › Architecture](../README.md#architecture)，共 6 張 Mermaid 圖：系統總覽、Supabase 註冊／登入／重設密碼、`/app` 管理訂閱、每 10 分鐘輪詢與寄信、DynamoDB 資料模型（含存取模式）、建置與部署路徑。

## 6. 建議加入的 MCP

| MCP                                          | 為什麼                                                                                                                     | 怎麼加                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Resend MCP**（官方）                       | 現在 Resend 的網域、API key、寄信紀錄都只能你手動查；有了它可以直接查寄送狀態／退信、輪替 API key、M2 用 broadcasts 寄公告 | 官方遠端伺服器 `https://mcp.resend.com/mcp`，claude.ai 可從 connector directory 一鍵連（也會帶 Resend skills） |
| **Cloudflare MCP**（官方）                   | 這次 DNS（CNAME、DKIM、DMARC）都要你手動加；有了它可以自動設定與驗證。若改用 Cloudflare Pages 也能一起管                   | Cloudflare 官方遠端 MCP（涵蓋 DNS、Workers、R2 等），用專案專用、權限最小的 API token                          |
| **Sentry MCP**（官方，搭配 Sentry 免費方案） | 目前前端錯誤、Lambda 例外都沒有集中監控；Sentry + MCP 讓 Claude 直接讀錯誤堆疊來修                                         | `https://mcp.sentry.dev/mcp`（OAuth）；前端加 `@sentry/react`、Lambda 加 `sentry-sdk`                          |
| **Stripe MCP**（若 M2 用 Stripe）            | M2 paywall 需要建立產品／價格、webhook、測試付款                                                                           | Stripe 官方 MCP（確認 M2 skill 指定的金流後再加）                                                              |
| **GitHub MCP**                               | 現在只有 git push；有它可以開 Issue 追蹤 §7 待辦、開 PR、看 GitHub Actions 結果（搭配 CI 建議）                            | GitHub 官方 MCP                                                                                                |
| 內建瀏覽器（已有，不需加）                   | 用測試帳號跑 `/app` 端到端測試（之前都卡在「需要你登入」）                                                                 | 建一個專用測試帳號後即可                                                                                       |

## 7. 目前進度與建議修改

### 進度

- **M0 ✅**：landing page、Supabase 註冊／登入／確認信／重設密碼；從 Lovable 遷到純 Vite SPA on Vercel，Lovable 完全移除。
- **M1 ✅**：AWS 後端（API、DynamoDB、SQS、EventBridge、6 個 Lambda）、Resend 通知信、自訂網域 `flight.dinxnai.com`、寄信網域 SPF/DKIM/DMARC、Supabase Custom SMTP。
- **超出課程的部分**：6 個機場雙向任選航線（取代原本 2 個方案）、去回程日期區間（各 ≤ 60 天）、單程組合備援、dedup 競態修正（鎖 + 二次檢查）、改目標價自動重置 dedup、輪詢改 10 分鐘、Lambda 原始碼進 repo + 55 個後端測試（moto）+ 20 個前端測試（Vitest）、天空主題與飛機 favicon。
- **M2 ⏳**：付款／`subscription_status`；另有兩項安全性工作你在 11/8 決定延後（JWT、密碼長度）。

### 建議修改（依優先順序）

**P0 — 上線給別人用之前一定要做（安全）**

1. **API 加 JWT 驗證**：目前任何人都能用任意 email 呼叫 `GET /subscriptions`、`POST/DELETE /subscribe` → 可讀取／刪除別人的訂閱，也能幫陌生人訂閱，讓你的網域寄信給他們（傷寄信信譽、可能被當垃圾信）。做法：前端帶 `Authorization: Bearer <supabase access token>` → HTTP API JWT authorizer（issuer `https://yzqirjnhldwqjjakxeko.supabase.co/auth/v1`、audience `authenticated`；先確認專案用非對稱簽章金鑰才有 JWKS）→ Lambda 從 `requestContext.authorizer.jwt.claims.email` 取 email，忽略 body/query 的 email。
2. **API throttling + 限縮 CORS**：`$default` stage 設 rate/burst（例如 10 req/s、burst 20）；CORS 從 `*` 改成 `https://flight.dinxnai.com`（加上本機開發網址）。
3. **密碼最短 8 碼**：Supabase Auth → Providers → Email；同步改 `src/pages/Auth.tsx`、`ResetPassword.tsx` 的 `minLength={6}`。（洩漏密碼檢查需要 Pro，免費方案跳過。）
4. **通知信加退訂**：信裡加「取消此提醒」連結 + `List-Unsubscribe` header（Gmail/Yahoo 對寄件者的要求），連結可指向 `/app` 或帶簽章 token 的退訂 API。

**P1 — 可靠性與維運**

5. **SQS 加 DLQ**：現在 Resend 一直回 5xx 時，訊息會每 180 秒重試直到 4 天後過期，期間不停打 Resend 也沒有任何人知道。加 `flight-fare-dlq`、`maxReceiveCount` 3–5。
6. **CloudWatch 告警 + AWS Budget**：至少 (a) 任一 Lambda `Errors > 0`、(b) DLQ 有訊息、(c) parser 連續 N 次沒有任何票價；預算設 US$5/月 email 通知。告警寄到你的信箱（SNS）。
7. **Log 保留期限**：7 個 log group 都是永不過期 → 設 14 或 30 天；刪掉測試用的 `/aws/lambda/flight-resend-test`。
8. **`notification_history` 開 TTL**：寫入時加 `expire_at`（例如 30 天後），表就不會無限成長；dedup 只需要最近一筆。
9. **parser 不要每條航線都 Scan 整張表**：現在 wrapper Scan 一次、每個 parser 又 Scan + filter 一次 → 成本是「航線數 × 表大小」。改法二選一：wrapper Scan 一次後把該航線的訂閱者直接放進 payload；或加 GSI（PK `route`）讓 parser 用 Query。
10. **每個 Lambda 分開 IAM 角色**（最小權限）：例如 list-subscriptions 只需要 `dynamodb:Query`，不需要讀 Resend 金鑰或發 SQS。
11. **基礎設施即程式碼 + CI**：把所有 AWS 資源寫成 SAM/CDK/Terraform（重建、M2 改動、刪除都一鍵）；GitHub Actions 用 OIDC 連 AWS 跑測試＋部署 Lambda → 可以拿掉 `flight-seed` 橋接和手動 zip 流程。也加前端 `bun run test && bun run lint` 檢查。

**P2 — 功能與資料品質**（11/8 討論過、延後的）

12. 有來回票時也比較「單程組合」，取較便宜者。
13. TWD 查詢回 HTTP 400 時，改用 USD 查詢再換算，不要整組月份跳過。
14. 兩層查價：命中目標價後用即時 API（Ignav 免費額度或 fast-flights）驗證再寄信，信中附即時價。
15. Travelpayouts `marker`：程式已支援把 marker 加進訂購連結（聯盟分潤），若 `flight/travelpayouts` 還沒有 `marker` 欄位可以補上。
16. `/app` 顯示「目前最低價／上次檢查時間」（需要把 parser 結果寫回 DynamoDB）。

**P3 — 清理**

17. 舊網域 `flight-price-notifier-ten.vercel.app` → 設 308 轉到新網域或移除；Supabase redirect allowlist 移除舊網址。
18. 移除 legacy：`plan_name`（tokyo/seoul）相容碼、`aws/flight-routes.json` 與 S3 上的 `flight-routes.json`、devDependency `postgres`（Drizzle 遺留）、沒用到的 shadcn 元件與相依（目前頁面只用 calendar / popover / sonner / switch）。
19. DMARC 觀察幾週沒問題後，`p=none` → `p=quarantine`，並加 `rua` 收報告。
20. 完成 Lovable 收尾：撤銷 Lovable GitHub App、刪 Lovable 專案與舊 Lovable Cloud 後端。

## 8. 從 0 開始重建的詳細步驟

目標：在一個**新的 claude.ai Project**（或全新帳號）重建出跟現在完全相同的系統。以下用 `<APP>` = `flight`、`<DOMAIN>` = `flight.dinxnai.com`、`<ACCOUNT>` = AWS 帳號 ID、region `us-east-1`。

> 建議做法：若要長期維護，第 4–6 步改用 SAM/CDK 一次寫成模板（見 §7-11）；下面列出手動步驟是為了讓每個資源的設定都清楚。

### 第 0 步：準備帳號與工具

1. 帳號：GitHub、Vercel（用 GitHub 登入）、Supabase、AWS（開 root MFA，建一個 IAM 管理者使用者或 IAM Identity Center）、Cloudflare（買網域）、Resend、Travelpayouts（加入 Aviasales 計畫 → Profile → API token）。
2. 本機工具：Git、Bun（或 Node 20+）、Python 3.12、AWS CLI v2（`aws configure`）。
3. claude.ai：建立新 Project → Settings › Connectors 連上 **AWS、Supabase、Vercel、GitHub**（建議再加 Resend、Cloudflare，見 §6）。
4. 把課程 repo 的 skill 放進新 repo 的 `.claude/skills/`（M0：`m0-landing-and-signin`、`-checklist`、`supabase-best-practice`；M1 從 `m1-flight-price-checker` 分支取得）。

### 第 1 步：前端專案（M0）

1. 建立 GitHub repo，用 Vite 建立 React + TypeScript 專案（或直接 fork 本 repo 最省事）：
   ```sh
   bun create vite flight-price-notifier --template react-ts
   cd flight-price-notifier
   bun add react-router @tanstack/react-query @supabase/supabase-js react-day-picker date-fns sonner lucide-react zod
   bun add -d tailwindcss @tailwindcss/vite vitest jsdom @testing-library/react @testing-library/jest-dom
   bunx shadcn@latest init   # 之後 add calendar popover switch sonner
   ```
2. 頁面與路由（同本 repo `src/routes.tsx`）：`/`、`/sign-in`、`/sign-up`、`/auth/confirmed`、`/reset-password`、`/app`（`RequireAuth` 保護）、`/auth` → `/sign-in`。
3. `src/integrations/supabase/client.ts`：`createClient(VITE_SUPABASE_URL, VITE_SUPABASE_PUBLISHABLE_KEY, { auth: { storage: localStorage, persistSession: true, autoRefreshToken: true } })`。
4. `vercel.json`：framework `vite`、output `dist`、`rewrites: [{ source: "/(.*)", destination: "/index.html" }]`。
5. `.gitignore` 排除 `.env*`（保留 `.env.example`）。

### 第 2 步：Supabase（只做 Auth）

1. New project（region 選離使用者近的，例如 Tokyo/Singapore）。
2. Project Settings › API：複製 Project URL 與 **publishable key**（絕不要把 service_role / secret key 放進 `VITE_*`）。
3. Authentication › Providers › Email：開啟 Email、Confirm email；密碼最短 **8**。
4. **不要建任何 `public` 資料表、trigger、RPC**（`supabase-best-practice`）。

### 第 3 步：Vercel 部署

1. Vercel › Add New › Project › Import GitHub repo（框架自動偵測 Vite）。
2. Settings › Environment Variables：`VITE_SUPABASE_URL`、`VITE_SUPABASE_PUBLISHABLE_KEY`（勾 Production、Preview、Development）；`VITE_FLIGHT_API_URL` 在第 6 步拿到 API 網址後再加（或寫成程式預設值）。
3. Deploy → 記下 `*.vercel.app` 網址；Supabase › Authentication › URL Configuration：Site URL 設成它、Redirect URLs 加 `https://<vercel>.vercel.app/**`。
4. 驗收（`m0-landing-and-signin-checklist`）：註冊 → 收確認信 → 回到 `/auth/confirmed` → 進入 `/app`。

### 第 4 步：AWS 基礎資源（M1 前半）

```sh
export AWS_REGION=us-east-1 ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# Secrets
aws secretsmanager create-secret --name flight/travelpayouts --secret-string '{"token":"<TP_TOKEN>"}'
aws secretsmanager create-secret --name flight/resend \
  --secret-string '{"api_key":"<RESEND_KEY>","from":"onboarding@resend.dev","test_to":"<你的信箱>"}'

# DynamoDB
aws dynamodb create-table --table-name subscriptions --billing-mode PAY_PER_REQUEST \
  --attribute-definitions AttributeName=email,AttributeType=S AttributeName=route,AttributeType=S \
  --key-schema AttributeName=email,KeyType=HASH AttributeName=route,KeyType=RANGE
aws dynamodb create-table --table-name notification_history --billing-mode PAY_PER_REQUEST \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sent_at,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sent_at,KeyType=RANGE

# SQS（建議一開始就加 DLQ，現況沒有）
aws sqs create-queue --queue-name flight-fare-dlq
aws sqs create-queue --queue-name flight-fare-queue --attributes '{
  "VisibilityTimeout":"180",
  "RedrivePolicy":"{\"deadLetterTargetArn\":\"arn:aws:sqs:us-east-1:'$ACCOUNT':flight-fare-dlq\",\"maxReceiveCount\":\"5\"}"}'

# S3（放 Lambda zip；若用正常 CLI 部署其實可省略）
aws s3 mb s3://flight-config-$ACCOUNT
```

IAM 角色 `flight-lambda-role`（信任 `lambda.amazonaws.com`，附加 `AWSLambdaBasicExecutionRole`），inline policy `flight-data`：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:<ACCOUNT>:secret:flight/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:DeleteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:<ACCOUNT>:table/subscriptions",
        "arn:aws:dynamodb:us-east-1:<ACCOUNT>:table/notification_history"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:GetQueueUrl",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:us-east-1:<ACCOUNT>:flight-fare-queue"
    },
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:us-east-1:<ACCOUNT>:function:flight-parser"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::flight-config-<ACCOUNT>/*"
    }
  ]
}
```

（更好的做法：每個 Lambda 一個角色，見 §7-10。`put-role-policy` 會整份覆蓋，修改時要讀出 → 合併 → 寫回。）

### 第 5 步：Lambda（M1 主體）

原始碼直接用本 repo 的 `aws/lambdas/<name>/index.py`，handler 都是 `index.handler`，runtime `python3.12`，128 MB。

```sh
python3 -m pytest aws/tests -q          # 先確認測試通過（moto，不碰真 AWS）
ROLE=arn:aws:iam::$ACCOUNT:role/flight-lambda-role
for fn in flight-save-subscription:10 flight-list-subscriptions:10 flight-parser:30 flight-parser-wrapper:60 flight-fare-notification:30; do
  name=${fn%%:*}; t=${fn##*:}
  python3 aws/build_zip.py $name >/dev/null   # 產生 aws/build/$name.zip（stdout 是 md5 + base64，丟掉即可）
  aws lambda create-function --function-name $name --runtime python3.12 --handler index.handler \
    --role $ROLE --timeout $t --memory-size 128 --zip-file fileb://aws/build/$name.zip
done
aws lambda update-function-configuration --function-name flight-fare-notification \
  --environment 'Variables={NOTIFY_FLOOR_HOURS=24,REALERT_PCT=20,REALERT_ABS_TWD=2000,CONFIG_VERSION=2}'

# SQS → notification（BatchSize 1）
aws lambda create-event-source-mapping --function-name flight-fare-notification --batch-size 1 \
  --event-source-arn arn:aws:sqs:us-east-1:$ACCOUNT:flight-fare-queue
```

（用正常 AWS CLI 就**不需要** `flight-seed`；那只是 AWS MCP 沙盒無法上傳 zip 的權宜做法。）

### 第 6 步：API Gateway 與排程

```sh
# HTTP API + Lambda 整合
API_ID=$(aws apigatewayv2 create-api --name flight-api --protocol-type HTTP \
  --cors-configuration 'AllowOrigins=https://<DOMAIN>,http://localhost:5173,AllowMethods=GET,POST,DELETE,OPTIONS,AllowHeaders=content-type,authorization' \
  --query ApiId --output text)
for pair in "flight-save-subscription:POST /subscribe" "flight-save-subscription:DELETE /subscribe" "flight-list-subscriptions:GET /subscriptions"; do
  fn=${pair%%:*}; rk=${pair#*:}
  INT=$(aws apigatewayv2 create-integration --api-id $API_ID --integration-type AWS_PROXY \
    --payload-format-version 2.0 --integration-uri arn:aws:lambda:us-east-1:$ACCOUNT:function:$fn \
    --query IntegrationId --output text)
  aws apigatewayv2 create-route --api-id $API_ID --route-key "$rk" --target integrations/$INT
  aws lambda add-permission --function-name $fn --statement-id "apigw-$(echo $rk | tr ' /' '--')" \
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:us-east-1:$ACCOUNT:$API_ID/*"
done
aws apigatewayv2 create-stage --api-id $API_ID --stage-name '$default' --auto-deploy \
  --default-route-settings 'ThrottlingRateLimit=10,ThrottlingBurstLimit=20'
echo "https://$API_ID.execute-api.us-east-1.amazonaws.com"   # → Vercel 的 VITE_FLIGHT_API_URL

# EventBridge 每 10 分鐘 → wrapper
aws events put-rule --name flight-price-check --schedule-expression 'rate(10 minutes)'
aws lambda add-permission --function-name flight-parser-wrapper --statement-id eb-price-check \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn arn:aws:events:us-east-1:$ACCOUNT:rule/flight-price-check
aws events put-targets --rule flight-price-check \
  --targets Id=wrapper,Arn=arn:aws:lambda:us-east-1:$ACCOUNT:function:flight-parser-wrapper
```

（現況的 CORS 是 `*`、沒有 throttling；上面的指令已直接套用 §7 的建議值。）

建議同時加上 P0 的 JWT authorizer：

```sh
AUTH=$(aws apigatewayv2 create-authorizer --api-id $API_ID --name supabase-jwt --authorizer-type JWT \
  --identity-source '$request.header.Authorization' \
  --jwt-configuration Audience=authenticated,Issuer=https://<SUPABASE_REF>.supabase.co/auth/v1 \
  --query AuthorizerId --output text)
# 每條 route：aws apigatewayv2 update-route --api-id $API_ID --route-id <id> --authorization-type JWT --authorizer-id $AUTH
```

（需要 Lambda 改成從 JWT claims 取 email、前端帶 access token；這是程式修改，屬於 M2 範圍。）

### 第 7 步：維運基本盤（現況沒有，建議重建時一起做）

```sh
for g in flight-save-subscription flight-list-subscriptions flight-parser flight-parser-wrapper flight-fare-notification; do
  aws logs create-log-group --log-group-name /aws/lambda/$g 2>/dev/null
  aws logs put-retention-policy --log-group-name /aws/lambda/$g --retention-in-days 30
done
aws dynamodb update-time-to-live --table-name notification_history \
  --time-to-live-specification Enabled=true,AttributeName=expire_at   # 需要程式寫入 expire_at
```

再到 AWS Console › Billing › Budgets 建 US$5/月預算；CloudWatch › Alarms 建 Lambda Errors、DLQ `ApproximateNumberOfMessagesVisible > 0` 的告警（SNS 寄到信箱）。

### 第 8 步：網域與寄信（Cloudflare + Vercel + Resend + Supabase SMTP）

順序很重要：

1. **Vercel** › Project › Settings › Domains › Add `<DOMAIN>` → 記下它給的 CNAME 目標。
2. **Cloudflare** › 網域 › DNS › Records › Add：Type `CNAME`、Name `flight`、Target = 上一步的值、**Proxy status 設 DNS only（灰色雲）**（讓 Vercel 自己簽 HTTPS 憑證）。等 Vercel 顯示 Valid。
3. **Supabase** › Authentication › URL Configuration：Site URL `https://<DOMAIN>`；Redirect URLs 加 `https://<DOMAIN>/**`。
4. **Resend** › Domains › Add Domain `<DOMAIN>`、region **us-east-1** → Sign in to Cloudflare 自動加 `send.flight`（SPF/MX）與 `resend._domainkey.flight`（DKIM）→ Verified。
5. **Cloudflare** 加 DMARC：TXT `_dmarc` = `v=DMARC1; p=none; rua=mailto:<你的信箱>`。
6. 更新 `flight/resend` 的 `from` 為 `FlightDin <alerts@<DOMAIN>>`。
7. **Supabase** › Authentication › Emails › SMTP Settings：Host `smtp.resend.com`、Port `465`、User `resend`、Password = 另建一把 Resend API key、Sender `no-reply@<DOMAIN>`；Rate limits › email 調到 30/h。

### 第 9 步：端到端驗收（等同 `m1-flight-price-checker-checklist`）

1. 用 `+` 別名註冊新帳號（例如 `you+test1@gmail.com`）→ 確認信寄件人是你的網域 → 回到 `<DOMAIN>/auth/confirmed`。
2. `/app` 建一條航線（例如 TSA-HND，目標價設高一點以確保命中）→ DynamoDB `subscriptions` 出現該列。
3. 手動 invoke：`aws lambda invoke --function-name flight-parser-wrapper /dev/stdout` → CloudWatch 看到 `dispatched TSA-HND` → parser `enqueued 1 match` → notification `sent ...`。
4. 收到通知信（NT$、≈US$、立即訂購按鈕）；馬上再 invoke 一次 → 記錄為 `skipped (deduped)`。
5. 同時送 3 則相同 SQS 訊息 → 只寄 1 封（鎖有效）。
6. 改目標價 → API 回 `dedup_reset: true`；刪除航線 → 列消失。
7. 前端：`bun run test && bun run lint && bun run build`；後端：`python3 -m pytest aws/tests -q`。

### 第 10 步：收尾

1. 把資源清單（名稱、ARN、網址）寫進新 Project 的文件（類似 `claude/m1-aws-resources.md`）。
2. 把 M1 skill 與這份文件 commit 進 repo，之後 M2 從這裡接續。
