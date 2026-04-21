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
INGESTION (weekly)
──────────────────
EventBridge cron
  → Lambda: ingest
      - YouTube Data API v3 (fetch new video IDs)
      - Filter: 2026+ only (cost control)
      - youtube-transcript-api (free captions, no key needed)
      - Extract scripture references from description
      - Write JSON to S3
  → Bedrock KB sync
      - Chunk into ~300 token paragraphs (20% overlap)
      - Embed via Titan Embeddings
      - Store in managed vector store

QUERY (per request)
───────────────────
API Gateway (HTTPS)
  → Cognito (member or staff JWT)
  → Lambda: query
      - Crisis keyword check (pre-Bedrock redirect)
      → Bedrock Guardrails (API-level enforcement)
          - Block: political opinions, staff info, prompt injection
          - Redirect: pastoral care disclosures → pastor contact
          - Ground: responses must cite actual sermon content
      → Bedrock Knowledge Base
          - Convert question to vector
          - Find 3-5 most relevant sermon chunks
      → Bedrock Nova Lite
          - Synthesize cited answer
      → DynamoDB (audit log)
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
│   ├── knowledge-base/
│   │   └── bedrock-kb.tf      # KB + managed vector store + chunking config
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
│   ├── ingest/handler.py      # YouTube → transcript → S3
│   └── query/handler.py       # guardrails → KB → cited answer
│
├── environments/
│   ├── dev/terraform.tfvars
│   └── prod/terraform.tfvars
│
└── scripts/
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

### Decision 2 — OpenSearch Serverless vs Bedrock Managed Vector Store

**Initial plan:** Use OpenSearch Serverless as the vector store — the AWS-recommended default for Bedrock KB.

**Discovery:** OpenSearch Serverless has a **minimum charge of ~$175/month** regardless of usage. "Serverless" means no cluster management, not pay-per-use. AWS keeps minimum capacity units running whether you have zero queries or a million.

The entire rest of this system costs ~$2/month. A $175/month vector store for a congregation with 50 queries/month is indefensible.

**Bedrock Managed Vector Store:** AWS manages the vector store internally at zero idle cost. Pay only for embedding at ingest (~$0.10/sermon, one-time) and query costs (fractions of a cent).

**Result: OpenSearch Serverless removed. Saves $175/month. Upgrade path documented for >50k queries/month.**

---

### Decision 3 — Claude Sonnet vs Amazon Nova Lite

**Initial plan:** Claude Sonnet as the default LLM — highest quality.

**Reality check:** The LLM's job in a RAG system is to read retrieved chunks and summarize them clearly. The Knowledge Base does the hard work of finding relevant content. Synthesizing a sermon summary is not a complex reasoning task.

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
| OpenSearch Serverless | $175/month minimum idle cost. Replaced by Bedrock managed vector store |
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
| Bedrock KB sync (new weekly sermons) | ~$0.40 |
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
- Korean ingestion — separate S3 prefix, language tag, bilingual KB

### Scale
- Multi-church — parameterize channel ID and Cognito pool per church
- S3 Terraform backend — required for team collaboration
- OpenSearch Serverless — only justified above ~50,000 queries/month

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
- **AWS Bedrock** — Knowledge Base, Guardrails, managed vector store, model selection
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
