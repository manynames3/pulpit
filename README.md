# 🎙️ Pulpit
### Serverless Sermon Intelligence System on AWS Bedrock

[![Pulpit CI](https://github.com/manynames3/pulpit/actions/workflows/ci.yml/badge.svg)](https://github.com/manynames3/pulpit/actions/workflows/ci.yml)

> A production-grade RAG system that makes a church's sermon archive searchable via natural language — auto-ingested from YouTube, secured with pastoral guardrails, fully provisioned via Terraform. **Running cost: ~$1–2/month.**

---

## What It Does

Church staff and members ask plain English questions against the full sermon archive:

```
"Has Pastor preached on grief and loss?"
"What scriptures have been taught on anxiety this year?"
"Summarize the series on the book of Romans."
"What did Pastor say about forgiveness last month?"
```

The system finds relevant sermon segments and returns a cited answer — sermon title, date, scripture reference — grounded only in what was actually taught. New sermons are indexed automatically every week. Nobody uploads anything manually, ever.

> **Built for Atlanta Bethel Church (아틀란타 벧엘교회), a Korean-English bilingual congregation in Gwinnett County, GA. Deployable by any church with a YouTube channel.**

---

## Why Not Just Upload to ChatGPT?

| Limitation | ChatGPT Upload | Pulpit |
|---|---|---|
| Archive size | ~50 docs max per session | Entire archive always indexed |
| Persistence | Re-upload every session | Always available |
| Auto-ingestion | Manual every time | New sermons indexed weekly automatically |
| Multi-user | One person per session | Entire congregation simultaneously |
| Access tiers | None | Member vs. staff permissions |
| Data privacy | Content sent to OpenAI servers | Stays entirely within your AWS account |
| Audit trail | None | Every query logged for pastoral accountability |
| Guardrails | Prompt-only, bypassable | Enforced at API level — cannot be bypassed |
| Cost at scale | $20+/month per user | ~$1–2/month flat for entire congregation |

---

## Architecture

### Core Design Principle

> Every service must answer yes to: *"Does the system break without this?"* If no — it gets cut or made optional.

### System Diagram

```
INGESTION (local, low-cost)
───────────────────────────
Mac / cron / manual run
  → scripts/ingest-local.py
      - YouTube uploads playlist API
      - youtube-transcript-api (free captions)
      - Filter: lead pastor + sermon-like content
      - Write sermon JSON to S3
  → scripts/rebuild_index.py
      - Load raw sermon JSONs from S3
      - Chunk transcripts into overlapping word windows
      - Reuse unchanged embeddings when possible
      - Embed new sermons/chunks via Titan Embeddings
      - Write a single chunked index.json back to S3

QUERY (per request)
───────────────────
API Gateway (HTTPS)
  → Cognito (member or staff JWT)
  → Lambda: query
      - Crisis keyword check (pre-Bedrock redirect)
      - Load chunked index.json from S3 (Lambda-cached)
      - Hybrid retrieval:
          - semantic chunk scoring via Titan embeddings
          - lexical boosts for title/topics/scripture/transcript mentions
          - deterministic Bible-term expansions (e.g. Genesis → 창세기)
      - Collapse the best chunks back to top sermons
      → Bedrock Guardrails
      → Bedrock Nova Lite
          - Read only the matched excerpts
          - Synthesize cited answer
      → DynamoDB
          - 30-day answer cache
          - audit log
  → Response to user

SECURITY (always on)
────────────────────
CloudTrail → S3    immutable audit log, free
IAM roles          one scoped role per Lambda, no wildcards
Cognito            2 tiers, 50k MAU free forever
SSM SecureString   secrets never in code or git
GuardDuty          off by default, enable_guardduty = true for prod
```

---

## Terraform Structure

```
pulpit/
├── main.tf                    # 4 module calls, clean root
├── variables.tf               # all config and feature toggles
├── versions.tf                # provider constraints
├── outputs.tf
│
├── modules/
│   ├── ingestion/
│   │   ├── eventbridge.tf     # weekly cron
│   │   ├── lambda.tf          # ingest Lambda + scoped IAM role
│   │   ├── s3.tf              # transcript bucket
│   │   └── ssm.tf             # YouTube API key (SecureString)
│   │
│   ├── query/
│   │   ├── api-gateway.tf
│   │   ├── cognito.tf         # user pool, member + staff groups
│   │   ├── lambda.tf          # query Lambda + scoped IAM role
│   │   ├── guardrails.tf      # Bedrock Guardrails
│   │   └── dynamodb.tf        # query audit log
│   │
│   └── security/
│       ├── cloudtrail.tf
│       ├── iam.tf
│       └── guardduty.tf       # toggleable
│
├── lambda/
│   ├── ingest/handler.py      # legacy AWS ingest path
│   └── query/handler.py       # chunked hybrid retrieval → cited answer
│
├── environments/
│   ├── dev/terraform.tfvars
│   └── prod/terraform.tfvars
│
└── scripts/
    ├── ingest-local.py        # primary local ingest path
    ├── rebuild_index.py       # chunk + embed + publish search index
    ├── set-api-key.sh         # store key in SSM after deploy
    └── create-ci-user.sh      # least-privilege IAM for CI
```

---

## Cost Decisions Made During Build

This documents every cost decision made during development — including decisions that were reversed after discovering the real numbers. This is the actual engineering thought process, not a polished retrospective.

---

### Decision 1 — YouTube Transcripts vs AWS Transcribe

**Initial plan:** Use AWS Transcribe to convert sermon audio to text.

**Discovery:** YouTube already generates free captions for virtually every uploaded video. The `youtube-transcript-api` Python library pulls them directly — no API key, no cost, no quota.

**AWS Transcribe cost:** $0.02/min × 45 min × 52 sermons/year = **$47/year** for something YouTube does for free.

**Result: AWS Transcribe removed. Saves $47/year.**

---

### Decision 2 — OpenSearch Serverless vs S3 + Lambda Chunked Index

**Initial plan:** Use OpenSearch Serverless for full-text + vector retrieval.

**Discovery:** OpenSearch Serverless adds a real fixed floor cost for a problem that is still small enough to solve inside the existing serverless stack. This archive is tens of sermons, not millions of documents. The hard problem here is retrieval quality on brief mentions, not distributed search infrastructure.

**What we actually need:**
- chunk-level transcript retrieval
- exact term matching for Bible names / Korean keywords
- semantic ranking for broader natural-language questions

All three can be done with:
- S3-stored chunked `index.json`
- Titan embeddings at ingest time
- pure-Python hybrid ranking inside Lambda

That keeps the system on near-zero idle cost. OpenSearch would improve search features, but not enough to justify the fixed cost and operational weight for this application.

**Result: OpenSearch removed from the v1/v2 architecture.**

**Why this stack won:**
- no always-on search bill
- good enough retrieval quality after chunking + lexical boosts
- much simpler operations
- easy future migration if archive size or query volume grows materially

---

### Decision 3 — Claude Sonnet vs Amazon Nova Lite

**Initial plan:** Claude Sonnet as the default LLM — highest quality.

**Reality check:** The LLM's job in this system is to read retrieved excerpts and summarize them clearly. The expensive part is not model reasoning — it is getting the right sermon chunks in front of the model. Synthesizing a sermon summary is not a complex reasoning task.

**Cost per 1,000 queries:**

| Model | Cost |
|---|---|
| Claude Sonnet | ~$40 |
| Claude Haiku | ~$8 |
| Amazon Nova Lite | ~$2 |

Nova Lite is AWS's newest lightweight model, designed specifically for retrieval tasks. Quality is sufficient for sermon Q&A. Model is a single variable — upgradeable in 30 seconds.

**Result: Nova Lite as default. Saves ~$38/1,000 queries vs Sonnet. Fully swappable.**

---

### Decision 4 — Full Archive vs 2026-Only Ingestion

**Initial plan:** Ingest the complete sermon archive on first run.

**Discovery:** Atlanta Bethel has been uploading sermons for years. Full archive = ~500 sermons × $0.10 = **$50 one-time embedding cost**.

**2026 filter:** Lambda checks `publishedAt[:4]` (year from YouTube's ISO 8601 timestamp). Anything before 2026 is skipped before any processing. ~16 sermons uploaded since January 2026.

**2026-only cost: ~$1.60 total.**

**Result: Default to 2026-only pilot. Full archive available by removing one filter line. Let the church decide if $50 is worth the complete history.**

---

### Decision 5 — GuardDuty Default State

**Initial plan:** Enable GuardDuty by default as part of the SOC 2-aligned security baseline.

**Reality check:** This is a fully serverless architecture — no EC2, no persistent servers. GuardDuty's primary value (detecting compromised instances, unusual EC2 behavior, crypto mining) doesn't apply here. The real credential risk is already mitigated by scoped IAM roles, SSM secrets, and CloudTrail.

**Result: GuardDuty off by default. One variable flip (`enable_guardduty = true`) to enable in prod. The Terraform resource exists — cost is zero until deliberately enabled.**

---

### Decision 6 — API Key in tfvars vs SSM Parameter Store

**Initial plan:** Pass YouTube API key as a Terraform variable in tfvars.

**Problem:** Terraform variables end up in state files. tfvars files get accidentally committed. CI logs can expose them. An API key in a tfvars file is one mistake away from being public.

**Result: YouTube API key stored in SSM Parameter Store as SecureString. Lambda fetches at runtime via SDK. Never in code, never in git, never in CI logs. IAM role scoped to that specific SSM path only.**

---

### Services Evaluated and Removed

| Service | Reason |
|---|---|
| AWS Transcribe | YouTube captions are free. Transcribe adds $47/year for zero benefit |
| OpenSearch Serverless | Too much fixed cost for this archive size. Replaced by S3 + Lambda chunked hybrid search |
| CloudFront | API Gateway serves HTTPS globally. Zero latency benefit at church scale |
| X-Ray tracing | CloudWatch logs sufficient. X-Ray adds cost without proportional debug value |
| Custom KMS keys | S3 AES256 default encryption is free and sufficient for pilot |
| AWS Config Rules | EC2 drift detection. Irrelevant for serverless-only architecture |
| SNS topics | CloudWatch alarms email directly. SNS is an unnecessary hop |
| Dev environment wrapper module | Created `module.pulpit.module.ingestion` nesting. Removed — run from root with `-var-file` |

---

## Real Cost Numbers

### One-time setup

| Item | Cost |
|---|---|
| Embed 2026 sermons (~16) | ~$1.60 |
| Everything else | $0 |
| **Total** | **~$1.60** |

### Monthly ongoing

| Service | Cost |
|---|---|
| Lambda, EventBridge, Cognito, DynamoDB, CloudTrail | ~$0 |
| S3 storage | ~$0.05 |
| Index rebuild embeddings (new weekly sermons/chunks) | ~$0.40 |
| Bedrock Nova Lite queries | ~$0.50–1.00 |
| GuardDuty (if enabled) | ~$1–2 |
| **Total** | **~$1–2/month** |

### Cost at scale

| Query Volume | Nova Lite | Nova Pro | Claude Haiku |
|---|---|---|---|
| 500/month | ~$1 | ~$3 | ~$5 |
| 2,000/month | ~$2 | ~$8 | ~$18 |
| 10,000/month | ~$8 | ~$35 | ~$90 |

### Full archive option

| Scope | One-time embedding |
|---|---|
| 2026 only (~16 sermons) | ~$1.60 |
| 2 years (~100 sermons) | ~$10 |
| Full archive (~500 sermons) | ~$50 |

---

## Feature Toggles

```hcl
# Swap LLM model without changing any code
# amazon.nova-lite-v1:0              default — ~$0.06/1M tokens
# amazon.nova-pro-v1:0               mid tier — ~$0.80/1M tokens
# anthropic.claude-haiku-4-5-20251001      quality — ~$0.80/1M tokens
# anthropic.claude-sonnet-4-6        best   — ~$3.00/1M tokens
variable "bedrock_model_id" { default = "amazon.nova-lite-v1:0" }

# Enable threat detection in production
variable "enable_guardduty" { default = false }

# Ingestion schedule — any EventBridge cron expression
variable "ingest_schedule" { default = "cron(0 6 ? * MON *)" }
```

### Dev vs Prod

| Setting | Dev | Prod |
|---|---|---|
| DynamoDB deletion protection | Off | On |
| S3 force_destroy | On | Off |
| GuardDuty | Off | Recommended on |
| Cognito MFA | Optional | Required for staff |
| Bedrock model | Nova Lite | Nova Pro or Haiku |

---

## Production Upgrade Path

### Security
- `enable_guardduty = true` — adds ~$1–4/month, continuous threat detection
- KMS CMK — replace AES256 with customer-managed key for full rotation audit
- WAF on API Gateway — rate limiting and geo-blocking for public deployment

### Quality
- Upgrade to Claude Haiku — one variable change, better theological nuance
- Add Bedrock reranking — improves retrieval precision, ~$0.002/query
- Korean ingestion — separate S3 prefix, language tag, bilingual index variants

### Scale
- Multi-church — parameterize channel ID and Cognito pool per church
- S3 Terraform backend — required for team collaboration
- OpenSearch Serverless — only justified once archive size or query volume materially outgrows Lambda-based hybrid search

---

## Security Design

**IAM Least Privilege:** Each Lambda has its own scoped role. Ingest Lambda: S3 write to `/transcripts/*` only + SSM read on one specific parameter path. Query Lambda: Bedrock retrieve + DynamoDB write only. No wildcards anywhere.

**Secrets:** YouTube API key in SSM SecureString. Never in tfvars, never in environment variables at rest, never in git, never in CI logs.

**Guardrails:** Bedrock Guardrails enforces content policy at the API layer, not the prompt layer. Prompt injection cannot bypass it. Crisis disclosures redirect to pastor contact before the LLM processes anything.

**Audit:** Every query logged to DynamoDB with user ID, group, question, response, citations, timestamp. 90-day TTL in dev, 365-day in prod. Staff-accessible for pastoral accountability.

---

## Deployment

```bash
git clone https://github.com/manynames3/pulpit.git
cd pulpit

# Store YouTube API key in SSM (run once after terraform apply)
./scripts/set-api-key.sh dev YOUR_API_KEY

terraform init
terraform plan  -var-file=environments/dev/terraform.tfvars
terraform apply -var-file=environments/dev/terraform.tfvars
```

---

## CI/CD

Every push runs:

| Step | Tool | Purpose |
|---|---|---|
| Format check | `terraform fmt` | Style enforcement |
| Validate | `terraform validate` | Syntax + provider schema |
| Security scan | Checkov | IaC misconfiguration warnings |
| Plan | `terraform plan` | Preview against real AWS account |

Deploy is always manual. CI never auto-applies. Plan output posted as PR comment automatically.

---

## Skills Demonstrated

- **Terraform IaC** — modular, multi-environment, variable-driven
- **AWS Serverless** — Lambda, API Gateway, EventBridge, DynamoDB, S3, SSM
- **AWS Bedrock** — Titan embeddings, Guardrails, Nova Lite model selection
- **Cost engineering** — real decisions, real numbers, reversals documented
- **Security** — IAM least-privilege, CloudTrail, Cognito tiers, secrets management
- **CI/CD** — GitHub Actions with fmt, validate, Checkov, plan
- **Architectural reasoning** — every decision documented including what was cut and why

---

## Author

**Aiden Rhaa** — AWS Solutions Architect Associate | AWS Developer Associate | Terraform Associate

Clearpath Property Group · Visual Impact Studios · Suwanee, GA

*Built for Atlanta Bethel Church (아틀란타 벧엘교회), Gwinnett County, GA.*

---

## License

MIT — deploy it, fork it, adapt it for your church.

---

## Deployment Troubleshooting Log

This section documents every real problem encountered during deployment and how each was resolved. This is not a polished retrospective — it's the actual sequence of failures, diagnoses, and pivots.

---

### Issue 0 — Cognito Signup Created Unconfirmed Users But Sent No Email

**Symptom:** Users could sign up, but login returned `This account still needs email verification` and no verification email ever arrived.

**Diagnosis:** Two configuration problems were stacked:
1. The Cognito user pool had `EmailSendingAccount=COGNITO_DEFAULT`, but `AutoVerifiedAttributes` was empty, so Cognito was not set to auto-verify email.
2. The frontend signup request only sent `Username` and `Password`. It did not send `UserAttributes: [{ Name: "email", Value: email }]`, so Cognito created users without an `email` attribute.

**Observed evidence:**
- `describe-user-pool` showed `AutoVerifiedAttributes: null`
- `list-users` showed affected users in `UNCONFIRMED` state with only a `sub` attribute
- `sign-up` responses returned `UserConfirmed: false` with no `CodeDeliveryDetails`

**Live fix applied:**
```bash
aws cognito-idp update-user-pool \
  --user-pool-id us-east-1_h9uVjOm3V \
  --auto-verified-attributes email

aws cognito-idp admin-update-user-attributes \
  --user-pool-id us-east-1_h9uVjOm3V \
  --username hangi87@aol.com \
  --user-attributes Name=email,Value=hangi87@aol.com

aws cognito-idp resend-confirmation-code \
  --client-id 26eit6g1j6qmfk3jp0m8uat58i \
  --username hangi87@aol.com
```

The same attribute update and resend step was also applied to `hangi87@aim.com`.

**Permanent code fix:**
- Added `auto_verified_attributes = ["email"]` to `modules/query/cognito.tf`
- Updated both frontends to send the `email` user attribute during `SignUp`

**Verification after fix:**
- `describe-user-pool` now shows `AutoVerifiedAttributes: ["email"]`
- `resend-confirmation-code` now returns `CodeDeliveryDetails` with `DeliveryMedium: EMAIL`

**Password reset edge case:** Cognito still rejects `ForgotPassword` for `UNCONFIRMED` users because they do not yet have a verified recovery channel. The frontend now detects that response, resends the confirmation code automatically, and routes the user back into account verification instead of showing a misleading email-format error.

**Lesson:** In Cognito, an email-looking username is not enough. If signup depends on email confirmation, the pool must auto-verify email and the client must submit the `email` attribute explicitly.

---

### Issue 1 — Terraform Format Check (CI exit code 3)

**Symptom:** GitHub Actions failing immediately with `Terraform exited with code 3`.

**Diagnosis:** Exit code 3 from `terraform fmt -check` means unformatted files were found. The `.tf` files were generated programmatically and never run through `terraform fmt`.

**Fix:** Ran `terraform fmt -recursive` locally, committed the formatted files.

**Lesson:** Always run `terraform fmt` before committing generated Terraform code.

---

### Issue 2 — Duplicate `required_providers` Block

**Symptom:** `terraform validate` failing with `Duplicate required providers configuration`.

**Diagnosis:** Both `main.tf` and the newly added `versions.tf` defined `required_providers`. Terraform only allows one per module.

**Fix:** Removed the `required_providers` block from `main.tf`, keeping it only in `versions.tf`.

---

### Issue 3 — `BEDROCK_MANAGED_VECTOR_STORE` Not a Valid Storage Type

**Symptom:** `terraform apply` failing with `ValidationException: Value 'BEDROCK_MANAGED_VECTOR_STORE' failed to satisfy enum value set`.

**Diagnosis:** This storage type doesn't exist in the Terraform AWS provider. The valid options are `RDS, OPENSEARCH_SERVERLESS, PINECONE, MONGO_DB_ATLAS, NEPTUNE_ANALYTICS, REDIS_ENTERPRISE_CLOUD`. All of them either have significant idle costs or introduce third-party dependencies.

**What we tried first:** Switched to `S3` as the storage backend. Also invalid — not in the enum.

**Final decision:** Removed Bedrock Knowledge Base entirely for the v1 pilot. For this archive size, a dedicated vector store was overkill. The system now keeps a chunked `index.json` in S3 and does hybrid retrieval in Lambda. That preserves low cost while materially improving mention-search over the original sermon-level approach.

**Documented upgrade path:** When the archive grows beyond 50 sermons:
- Option A: stay on S3 + Lambda and rebuild the chunked index
- Option B: move to OpenSearch only if archive size / query volume justifies the fixed search cost
- Option C: evaluate pgvector or another managed search layer if filtering and scale requirements become real

**Lesson:** Not every AWS feature has Terraform support yet. Always verify the provider schema before designing around a feature.

---

### Issue 4 — Bedrock Guardrail Provider Bug

**Symptom:** `Provider returned invalid result object after apply` on `aws_bedrock_guardrail.pulpit.description`.

**Diagnosis:** Known AWS provider bug. When `description` is omitted from `aws_bedrock_guardrail`, the provider returns an unknown value after apply which Terraform can't handle. The resource gets marked as tainted.

**Fix:** Added `description` field to the guardrail resource. Resource was destroyed and recreated cleanly on next apply.

---

### Issue 5 — Lambda Missing Dependencies (InvalidELFHeader)

**Symptom:** Lambda invocation failing with `Unable to import module 'handler': /var/task/cryptography/hazmat/bindings/_rust.abi3.so: invalid ELF header`.

**Root cause:** Two problems stacked:
1. Lambda zip only contained `handler.py` — no third-party libraries included
2. `google-api-python-client` pulls in `cryptography` which has compiled C extensions (`.so` files) built for Mac ARM — incompatible with Lambda's Linux x86_64 runtime

**What we tried first:** Created `scripts/build-lambda.sh` to package dependencies with `pip install --target`. Still failed because Mac ARM binaries don't run on Linux x86_64.

**Considered:** Using Docker with the official Lambda container (`public.ecr.aws/lambda/python:3.12`) to build Linux-compatible binaries. Rejected — adds Docker as a requirement for a simple deploy step.

**Final fix:** Removed `google-api-python-client` entirely. Replaced with direct HTTP calls to YouTube Data API v3 using `requests`. The `requests` library is pure Python — no compiled extensions, no platform issues. Same API calls, same results, no Docker needed.

**Lesson:** Avoid libraries with compiled C extensions in Lambda unless you have a consistent Linux build environment. Pure Python libraries are always portable.

---

### Issue 6 — CORS Blocking Browser Requests

**Symptom:** Frontend showing `CONNECTION ERROR: Failed to fetch` when calling the API.

**First diagnosis (wrong):** API Gateway CORS not configured. Added OPTIONS method with CORS headers via Terraform, redeployed.

**Actual root cause:** The HTML file was being opened as `file://` directly from the filesystem. Browsers treat `file://` as `null` origin. CORS policy blocks `null` origin even when `Access-Control-Allow-Origin: *` is set — this is a browser security restriction, not an API Gateway issue.

**Fix:** Serve the file over HTTP instead of opening it as a file:
```bash
cd ~/pulpit/frontend
python3 -m http.server 8080
# open http://localhost:8080
```

**Lesson:** Never test CORS from `file://`. Always use a local HTTP server.

---

### Issue 7 — YouTube Blocking AWS Lambda IPs

**Symptom:** `youtube-transcript-api` returning `YouTube is blocking requests from your IP` on every video. Lambda invocations consistently returning 0 ingested.

**Root cause:** YouTube actively blocks requests from known cloud provider IP ranges (AWS, GCP, Azure). `youtube-transcript-api` scrapes YouTube's internal transcript endpoint — not an official API — so it gets blocked at the IP level. This is a fundamental limitation, not a configuration issue.

**What we tried:**
1. Filtering for `eventType=completed` (live streams) — made no difference, IP is blocked regardless of video type
2. Updating to `youtube-transcript-api` v1.2.4 with new instance-based API — fixed a different bug but didn't resolve the IP block

**Confirmed working locally:** Running the same `youtube-transcript-api` code from a Mac with a residential IP works correctly. Korean auto-generated captions (`ko`, `generated: True`) are available on Atlanta Bethel's videos.

**Options evaluated:**
- YouTube Official Captions API — requires OAuth authorization from the channel owner (the church). Not available for this deployment.
- Proxy services — adds cost and complexity, introduces a third-party dependency
- Running Lambda in a VPC with NAT Gateway — NAT Gateway IPs are still AWS IPs, still blocked
- Cookies-based auth — YouTube explicitly warns this will result in account ban

**Final decision:** Run ingestion locally from your Mac using `scripts/ingest-local.py`. Residential IP is never blocked. Script uploads transcripts directly to S3. Run it manually after Sunday service.

**Long-term upgrade path:** If the church authorizes the YouTube app, switch to the official YouTube Captions API (`captions.download`) which is not IP-blocked. Until then, local ingestion is the correct solution.

**Lesson:** `youtube-transcript-api` is a scraper, not an official API. It works from residential IPs but is unreliable from cloud infrastructure. For production systems, always use official APIs.

---

### Issue 8 — `youtube-transcript-api` v1.x Breaking API Change

**Symptom:** `type object 'YouTubeTranscriptApi' has no attribute 'list_transcripts'` when testing locally.

**Root cause:** The library changed from class-based static methods to instance-based methods in v1.x.

**Before (v0.6.x):**
```python
transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
```

**After (v1.x):**
```python
api = YouTubeTranscriptApi()
transcripts = api.list(video_id)
```

Also: individual segment text changed from `segment["text"]` to `segment.text`.

**Fix:** Updated `requirements.txt` to pin `youtube-transcript-api==1.2.4` and updated all call sites to use the new instance API.

---

## Ingestion Architecture — Final State

The ingestion design went through three significant pivots:

**Original design:** EventBridge → Lambda → AWS Transcribe → S3
Rejected: AWS Transcribe costs $47/year. YouTube provides free captions.

**Second design:** EventBridge → Lambda → `youtube-transcript-api` → S3
Rejected: YouTube blocks Lambda's AWS IP addresses. Ingestion always returns 0.

**Final design:** Local script (`scripts/ingest-local.py`) → `youtube-transcript-api` → S3
Works: Residential IP is not blocked by YouTube. Run manually after Sunday service.

The rest of the system (query Lambda, API Gateway, Cognito, Guardrails) is fully serverless and unaffected. Only ingestion runs locally.

---

## Running Ingestion

```bash
# Install dependencies (one time)
pip3 install youtube-transcript-api requests boto3 --break-system-packages

# Configure env vars (copy example)
cp .env.example .env
# edit .env with your bucket/channel/key

# Load env vars and run
cd ~/pulpit
set -a && source .env && set +a
python3 scripts/ingest-local.py
```

Output:
```
Pulpit Local Ingest — 2026-04-21 07:30
Channel: UCchY0Iagf_2cCP0RGVwQ-FA
Bucket:  pulpit-transcripts-dev-636305658578
────────────────────────────────────────────────────────────
  ✅    2026-04-19  주일 4부 예배ㅣ정수한 목사ㅣ사도행전 17장 22-25절
  ✅    2026-04-12  주일 2부 예배ㅣ이혜진 담임목사ㅣ요한복음 11장
  EXIST 2026-04-05  주일 4부 예배 (already indexed)
────────────────────────────────────────────────────────────
Ingested: 2  |  Skipped: 1  |  Errors: 0
```

---

## Scheduling Ingestion (Cron Job)

If OAuth captions access isn’t available, the reliable approach is to **run ingestion from a residential / church-office internet connection** on a small always-on machine (Mac mini, office desktop, home server). This avoids YouTube blocking cloud IP ranges.

### macOS (launchd) — recommended for a Mac mini

Create `~/Library/LaunchAgents/com.pulpit.ingest.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>com.pulpit.ingest</string>
    <key>StartCalendarInterval</key>
    <dict>
      <key>Weekday</key><integer>1</integer>
      <key>Hour</key><integer>17</integer>
      <key>Minute</key><integer>0</integer>
    </dict>
    <key>WorkingDirectory</key><string>/Users/YOUR_USER/pulpit</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/zsh</string>
      <string>-lc</string>
      <string>set -a && source /Users/YOUR_USER/pulpit/.env && set +a && /usr/bin/python3 /Users/YOUR_USER/pulpit/scripts/ingest-local.py</string>
    </array>
    <key>StandardOutPath</key><string>/Users/YOUR_USER/pulpit/ingest.log</string>
    <key>StandardErrorPath</key><string>/Users/YOUR_USER/pulpit/ingest.err.log</string>
  </dict>
</plist>
```

Notes:
- `Weekday=1` is Sunday. `Hour=17` is 5pm. `launchd` uses the Mac’s local timezone.
- Replace `YOUR_USER` with your macOS username.

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.pulpit.ingest.plist
launchctl list | grep pulpit
```

### Linux (cron)

Example (runs Mondays at 8am local time):

```bash
crontab -e
```

Add:

```bash
0 8 * * 1 cd /opt/pulpit && set -a && . /opt/pulpit/.env && set +a && /usr/bin/python3 /opt/pulpit/scripts/ingest-local.py >> /opt/pulpit/ingest.log 2>> /opt/pulpit/ingest.err.log
```

---

## Alternative Frontend Deployment

This repo now includes an alternative static frontend at `frontend-alternative/index.html`.

The deployment split for that frontend is:

- `Cloudflare Pages` for static hosting
- `AWS` for Cognito, API Gateway, Lambda, DynamoDB, and Bedrock

### Why use Cloudflare Pages for this frontend

This specific frontend is a static site. It does not need SSR, a backend runtime, or a JavaScript build pipeline.

Because of that, the cheapest practical hosting model is:

- static frontend on Cloudflare Pages
- existing application backend in AWS

That keeps the fixed frontend cost close to zero while preserving the current AWS application architecture.

For this project, the main long-term cost driver is the AWS backend, especially AI usage, not static asset hosting.

### Why not default to Amplify

AWS Amplify is still a valid option. It is not required for this frontend.

Cloudflare Pages was chosen here because:

- the frontend is static
- the hosting bill is typically lower
- it avoids paying AWS-hosting convenience costs for features this frontend does not use

### When Amplify still makes sense

Use Amplify instead if you want:

- everything to stay inside the AWS ecosystem
- Git-based frontend deploys managed entirely in AWS
- one-vendor operational ownership
- a future path toward a more complex application frontend

Short version:

- `Cloudflare Pages` is the cost-first option
- `Amplify` is the AWS-ecosystem option

### Files added for the alternative frontend

- `frontend-alternative/index.html`
- `DEPLOY.md`
- `wrangler.toml`

### Deployment notes

The Cloudflare Pages deploy path expects:

- Framework preset: `None`
- Build command: blank
- Build output directory: `frontend-alternative`

Before production use, update API Gateway CORS for the final frontend domain. The detailed deployment and CORS steps are documented in `DEPLOY.md`.
