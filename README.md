# 🎙️ Pulpit
### Serverless Sermon Intelligence System on AWS Bedrock

> A production-grade RAG (Retrieval-Augmented Generation) system that makes a church's entire sermon archive searchable via natural language — auto-ingested from YouTube, secured with pastoral guardrails, and fully provisioned via Terraform.

---

## What It Does

Church staff and members ask plain English questions against the full sermon archive:

```
"Has Pastor preached on grief and loss?"
"What scriptures have been taught on anxiety in the last 2 years?"
"Summarize the series on the book of Romans."
"What did Pastor say about forgiveness in 2023?"
```

The system retrieves relevant sermon segments and returns a cited answer — sermon title, date, and scripture reference — grounded only in what was actually taught. New sermons are ingested automatically every week from the church's YouTube channel. Nobody uploads anything manually.

---

## Why This Exists

Every church with years of recorded sermons has the same problem: the content is locked inside videos nobody has time to rewatch. Staff repeat research. Counselors can't quickly find what's been taught on a topic. New members have no way to explore the archive. This system solves that with infrastructure that costs less than a streaming subscription per month.

> **Built originally for a Korean-English bilingual church community in Gwinnett County, GA. Designed to be deployable by any church with a YouTube channel.**

---

## Why Not Just Use ChatGPT?

This question comes up immediately. Here is the honest answer:

| Limitation | ChatGPT Upload | Shepherd |
|---|---|---|
| Archive size | ~50 docs max per session | Unlimited — entire archive always indexed |
| Persistence | Re-upload every session | Always available, zero manual work |
| Auto-ingestion | Manual upload required | New sermons indexed automatically weekly |
| Multi-user | Single user per session | Entire congregation simultaneously |
| Access tiers | None | Members vs. staff permissions via Cognito |
| Data privacy | Content sent to OpenAI servers | Stays entirely within your AWS account |
| Audit trail | None | Every query logged — accountability built in |
| Guardrails | Prompt-only, bypassable | Enforced at API level via Bedrock Guardrails |
| Cost at scale | $20/month per user | ~$5–10/month flat for entire congregation |

For a single document and a single user, ChatGPT is fine. For a living, multi-user archive that grows every week and must never expose sensitive pastoral content — you need infrastructure.

---

## Architecture

### Design Philosophy

> Every service in this architecture must answer yes to: *"Does the system break without this?"* If no — it is cut or made optional.

This is a deliberately lean system. No OpenSearch cluster, no CloudFront, no X-Ray, no custom KMS keys, no SNS topics. Each of those was evaluated and cut. The reasoning is documented below in the [Architecture Decisions](#architecture-decisions) section.

### System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                 INGESTION PIPELINE                       │
│                      (weekly)                           │
│                                                         │
│  EventBridge (cron: every Monday 6am)                   │
│       ↓                                                 │
│  Lambda: ingest                                         │
│    - YouTube Data API v3 → fetch new videos             │
│    - youtube-transcript-api → pull free captions        │
│    - Format JSON: title, date, scripture, series        │
│    - Write to S3: transcripts/{year}/{sermon-id}.json   │
│       ↓                                                 │
│  S3 Bucket (transcripts)                                │
│       ↓                                                 │
│  Bedrock Knowledge Base (StartIngestionJob trigger)     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                  QUERY PIPELINE                          │
│                   (per request)                         │
│                                                         │
│  User (browser / mobile)                                │
│       ↓                                                 │
│  API Gateway (HTTPS)                                    │
│       ↓                                                 │
│  Cognito Authorizer (member or staff JWT)               │
│       ↓                                                 │
│  Lambda: query                                          │
│       ↓                                                 │
│  Bedrock Guardrails                                     │
│    - Block: self-harm, crisis, manipulation attempts    │
│    - Block: political opinions, personal staff info     │
│    - Redirect: pastoral care disclosures → contact info │
│    - Ground: responses must cite sermon content only    │
│       ↓                                                 │
│  Bedrock Knowledge Base (semantic retrieval)            │
│       ↓                                                 │
│  Bedrock LLM (synthesize cited answer)                  │
│       ↓                                                 │
│  DynamoDB (log: user tier, query, response, timestamp)  │
│       ↓                                                 │
│  Response → user                                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    SECURITY LAYER                        │
│              (always on, mostly free)                   │
│                                                         │
│  CloudTrail → S3  (immutable API audit log)             │
│  IAM least-privilege  (one role per Lambda, no *)       │
│  Cognito  (auth, 2 tiers, 50k MAU free forever)         │
│  S3 block public access  (enforced via Terraform)       │
│  GuardDuty  (off by default — see options below)        │
└─────────────────────────────────────────────────────────┘
```

---

## Terraform Structure

```
shepherd/
├── main.tf                  # root: calls all modules
├── variables.tf             # all toggles and config here
├── outputs.tf               # API endpoint, Cognito pool ID, etc.
│
├── modules/
│   ├── ingestion/
│   │   ├── eventbridge.tf   # weekly cron rule
│   │   ├── lambda.tf        # ingest Lambda + IAM role
│   │   └── s3.tf            # transcript storage bucket
│   │
│   ├── knowledge-base/
│   │   └── bedrock-kb.tf    # KB resource + S3 data source sync
│   │
│   ├── query/
│   │   ├── api-gateway.tf   # REST API + Cognito authorizer
│   │   ├── cognito.tf       # user pool, member + staff groups
│   │   ├── lambda.tf        # query Lambda + IAM role
│   │   ├── guardrails.tf    # Bedrock Guardrails config
│   │   └── dynamodb.tf      # query audit log table
│   │
│   └── security/
│       ├── cloudtrail.tf    # trail + S3 log bucket
│       ├── iam.tf           # account-level baseline policies
│       └── guardduty.tf     # detector, toggled by variable
│
└── environments/
    ├── dev/
    │   └── terraform.tfvars
    └── prod/
        └── terraform.tfvars
```

---

## Configuration & Feature Toggles

All meaningful decisions are exposed as variables. Nothing is hardcoded.

```hcl
# variables.tf

variable "bedrock_model_id" {
  description = "LLM model for query synthesis. See model options below."
  default     = "amazon.nova-lite-v1:0"
}

variable "enable_guardduty" {
  description = "Enable AWS GuardDuty threat detection. Recommended for production."
  default     = false
}

variable "youtube_channel_id" {
  description = "The church YouTube channel ID to ingest from."
}

variable "ingest_schedule" {
  description = "EventBridge cron for ingestion. Default: every Monday 6am UTC."
  default     = "cron(0 6 ? * MON *)"
}

variable "environment" {
  description = "dev or prod. Controls log retention, deletion protection, etc."
  default     = "dev"
}
```

### Dev vs. Prod differences

| Setting | Dev | Prod |
|---|---|---|
| DynamoDB deletion protection | Off | On |
| CloudTrail log retention | 30 days | 365 days |
| GuardDuty | Off | Recommended on |
| Bedrock model | Nova Lite | Configurable |
| Cognito MFA | Optional | Required for staff |

---

## LLM Model Options

The model is a single variable swap. Here is the full tradeoff matrix:

| Model | Bedrock Model ID | Input/1M tokens | Output/1M tokens | Quality | Recommended For |
|---|---|---|---|---|---|
| **Amazon Nova Lite** ✅ default | `amazon.nova-lite-v1:0` | $0.06 | $0.24 | Good | Starter / budget / pilot |
| **Amazon Nova Pro** | `amazon.nova-pro-v1:0` | $0.80 | $3.20 | Very Good | Mid-budget production |
| **Claude Haiku 3.5** | `anthropic.claude-haiku-4-5-20251001` | $0.80 | $4.00 | Excellent | Quality-first production |
| **Claude Sonnet** | `anthropic.claude-sonnet-4-6` | $3.00 | $15.00 | Best | High-volume enterprise |
| **Llama 3.1 8B** | `meta.llama3-1-8b-instruct-v1:0` | $0.22 | $0.22 | Moderate | Experimental / cost testing |

### Why Nova Lite as default

- Lowest cost for a non-profit / pilot deployment
- Native AWS model — best-tested integration with Bedrock Knowledge Base
- Designed specifically for retrieval-augmented tasks
- Quality is sufficient for sermon Q&A: the KB does the heavy lifting, the LLM just synthesizes

### When to upgrade

Upgrade to **Claude Haiku** if:
- Answers feel choppy or miss nuance in complex theological questions
- Bilingual Korean/English content is being mishandled
- The congregation is large and answer quality is a pastoral concern

Upgrade to **Claude Sonnet** only if this becomes a high-traffic multi-church platform.

---

## Guardrails Configuration

Bedrock Guardrails enforces content policy at the API level — not the prompt level. This is important: prompt-only guardrails can be bypassed with prompt injection. API-level enforcement cannot.

### What is blocked

```
HARD BLOCKS (returns safe error message):
- Self-harm, suicide, crisis language
- Requests for personal information about staff or members
- Political opinion fishing ("what does Pastor think about...")
- Attempts to override system behavior ("ignore previous instructions")
- Content unrelated to the sermon archive scope

REDIRECTS (returns pastoral contact instead of an answer):
- Mental health disclosures
- Marriage or family crisis language
- Abuse disclosures of any kind
→ Response: "This sounds important. Please speak directly with a pastor.
   You can reach the pastoral team at [contact]. This conversation is private."

GROUNDING ENFORCEMENT:
- Responses must be sourced from the Knowledge Base
- Model cannot generate theological positions not present in sermons
- Every answer must cite: sermon title, date, scripture reference
- If topic not found: "This topic hasn't been addressed in our sermon archive.
   Consider speaking with a pastor directly."
```

### Why this matters for a church specifically

A general-purpose AI assistant in a church context is a liability without guardrails. Members may be vulnerable. Questions may be pastoral in nature. The system must never:
- Give spiritual advice beyond what was actually preached
- Engage with theological manipulation attempts
- Miss a crisis disclosure by treating it as a search query

Bedrock Guardrails handles all of this at the infrastructure layer — before the LLM ever sees the query.

---

## Access Tiers

Two Cognito user groups with different document access:

| Content | Member | Staff / Elder |
|---|---|---|
| All sermon transcripts | ✅ | ✅ |
| Scripture reference index | ✅ | ✅ |
| Series summaries | ✅ | ✅ |
| Elder board meeting minutes | ❌ | ✅ |
| Pastoral care policies | ❌ | ✅ |
| Benevolence fund guidelines | ❌ | ✅ |
| Query audit logs | ❌ | ✅ |

Lambda reads the Cognito group claim from the JWT and filters KB retrieval scope accordingly.

---

## Auto-Ingestion Pipeline

### Why YouTube transcripts instead of AWS Transcribe

AWS Transcribe costs ~$0.02/minute of audio. A 45-minute sermon = $0.90. At 52 sermons/year = ~$47/year in transcription alone. YouTube already generates free captions for almost all uploaded videos — typically within hours of upload.

The `youtube-transcript-api` Python library pulls these directly. No cost. No quota impact beyond the YouTube Data API free tier (10,000 units/day — a weekly Lambda run uses ~10 units).

### Transcript metadata schema

Each sermon is stored in S3 as structured JSON:

```json
{
  "sermon_id": "abc123",
  "title": "When God Feels Silent",
  "date": "2024-03-17",
  "series": "Psalms of Lament",
  "scripture_references": ["Psalm 22:1-2", "Matthew 27:46"],
  "pastor": "Pastor Kim",
  "language": "en",
  "duration_minutes": 47,
  "youtube_url": "https://youtube.com/watch?v=abc123",
  "transcript": "Full transcript text here..."
}
```

Scripture references are extracted from the video description via regex — churches almost always include them. This metadata powers citation in responses.

---

## Cost Analysis

### Monthly estimate (active congregation, ~500 queries/month)

| Service | Free Tier | Est. Monthly Cost |
|---|---|---|
| Lambda (2 functions) | Always free | ~$0.00 |
| EventBridge | Always free | ~$0.00 |
| S3 (transcript storage) | 5GB free | ~$0.05 |
| Bedrock KB sync | Per-sync pricing | ~$0.50 |
| Bedrock Nova Lite queries | No free tier | ~$0.50–1.00 |
| API Gateway | 1M calls free yr 1 | ~$0.00–1.00 |
| Cognito | 50k MAU free | ~$0.00 |
| DynamoDB | Always free at this scale | ~$0.00 |
| CloudTrail | 1 trail free | ~$0.00 |
| GuardDuty (if enabled) | 30-day trial | ~$1.00–2.00 |
| **Total** | | **~$1–5/month** |

### Scaling cost

| Query Volume | Nova Lite | Claude Haiku | Claude Sonnet |
|---|---|---|---|
| 500/month | ~$1 | ~$5 | ~$20 |
| 2,000/month | ~$3 | ~$18 | ~$75 |
| 10,000/month | ~$15 | ~$90 | ~$375 |

At any volume, Nova Lite is the responsible default for a non-profit. The model ID is one variable change when budget allows an upgrade.

---

## Production Upgrade Path

This system is intentionally built lean for a pilot/non-profit deployment. Here is the documented upgrade path when moving to a funded or multi-church production deployment:

### Security upgrades
- **Enable GuardDuty** — set `enable_guardduty = true`. Adds ~$1–4/month. Provides continuous threat detection.
- **Custom KMS keys** — replace S3 default encryption with KMS CMK for full key control and rotation audit trail.
- **AWS Config Rules** — add drift detection if the deployment grows to include EC2 or more complex resources.
- **WAF on API Gateway** — add rate limiting and geo-blocking if the API is public-facing at scale.

### Quality upgrades
- **Upgrade model to Claude Haiku** — single variable change. Noticeably better synthesis quality for complex theological questions.
- **Add reranking** — Bedrock KB supports reranking models that improve retrieval precision. Adds cost but improves answer quality significantly at scale.
- **Bilingual support** — Korean sermon ingestion via separate S3 prefix + language-aware KB configuration.

### Scale upgrades
- **Multi-church deployment** — parameterize `youtube_channel_id` and `cognito_user_pool` per church. Each church gets isolated data and auth. One Terraform workspace per church.
- **CloudFront** — add CDN in front of API Gateway if latency becomes a concern for geographically distributed users.
- **Dedicated OpenSearch** — replace Bedrock's managed vector store with a dedicated OpenSearch Serverless collection if query volume exceeds ~50,000/month.

---

## What Was Cut and Why

| Service | Reason Removed |
|---|---|
| AWS Transcribe | YouTube provides free captions. Transcribe costs ~$47/year unnecessarily |
| OpenSearch Serverless | Bedrock KB manages its own vector store. OpenSearch adds ~$90/month base cost with no benefit at this scale |
| CloudFront | API Gateway already serves HTTPS globally. CloudFront adds cost and complexity with no meaningful latency improvement for this use case |
| X-Ray tracing | CloudWatch logs are sufficient for debugging at this scale. X-Ray adds cost and complexity without proportional observability value |
| Custom KMS keys | S3 default encryption (SSE-S3) is free and sufficient for a non-profit pilot. KMS adds ~$1/month per key plus API call costs |
| AWS Config Rules | Designed for EC2 and stateful resource drift detection. A serverless-only architecture has minimal drift risk |
| SNS topics | CloudWatch alarms can email directly. SNS is an unnecessary hop for a single-destination alert |
| Multiple Lambda layers | Two Lambdas (ingest + query) cover all functionality cleanly. Additional functions would add complexity without separation of concern benefit |

---

## Architecture Decisions

### Why Bedrock over self-hosted LLM

Self-hosting an open-source LLM (Llama, Mistral) on EC2 would cost ~$50–200/month minimum for a GPU instance — more than this entire system costs to run. Bedrock is pay-per-token with no infrastructure to manage. For a non-profit deployment, this is the only responsible choice.

### Why not LangChain or LlamaIndex

These frameworks abstract the RAG pipeline but add dependency complexity and version fragility. Bedrock's native Knowledge Base handles chunking, embedding, and retrieval without a framework layer. Fewer dependencies = fewer breaking changes = lower maintenance burden for a church that has no dedicated DevOps staff.

### Why Cognito over a simpler auth approach

API keys would be simpler but not auditable per-user. Cognito provides per-user identity, group-based access tiers, JWT tokens compatible with API Gateway, and a free tier that covers any realistic church size. The audit log in DynamoDB ties every query to a Cognito user ID — this matters for pastoral accountability.

### Why DynamoDB over CloudWatch Logs for query history

CloudWatch Logs are optimized for operational debugging, not queryable history. DynamoDB lets staff retrieve *"all questions asked about topic X in the last 6 months"* or *"all queries by a specific user."* That's a pastoral accountability feature, not just observability.

### Why EventBridge over a manual trigger

The system should require zero human involvement after deployment. A cron trigger means a sermon uploaded Sunday is indexed by Monday morning automatically. Manual triggers create a recurring human dependency that will eventually fail.

---

## Local Development

```bash
# Prerequisites
# - AWS CLI configured
# - Terraform >= 1.5
# - Python 3.11+
# - YouTube Data API v3 key

# Clone and initialize
git clone https://github.com/yourhandle/shepherd
cd shepherd
terraform init

# Deploy dev environment
cd environments/dev
terraform plan
terraform apply

# Test ingestion manually
aws lambda invoke \
  --function-name shepherd-ingest-dev \
  --payload '{"manual": true}' \
  response.json

# Test query
curl -X POST https://{api-id}.execute-api.us-east-1.amazonaws.com/dev/query \
  -H "Authorization: Bearer {cognito-jwt}" \
  -H "Content-Type: application/json" \
  -d '{"question": "Has Pastor preached on forgiveness?"}'
```

---

## Skills Demonstrated

This project was built as a portfolio demonstration of the following:

- **Terraform IaC** — modular, multi-environment, variable-driven infrastructure
- **AWS Serverless** — Lambda, API Gateway, EventBridge, DynamoDB, S3
- **AWS Bedrock** — Knowledge Base, Guardrails, LLM integration
- **Security baseline** — IAM least-privilege, CloudTrail, Cognito auth tiers
- **Cost engineering** — deliberate service selection based on TCO, not defaults
- **Architectural reasoning** — every decision documented with tradeoffs
- **Real-world use case** — built for an actual community need, not a toy demo

---

## Author

**Aiden Rhaa** — AWS Solutions Architect Associate | AWS Developer Associate | Terraform Associate

Clearpath Property Group · Visual Impact Studios · Suwanee, GA

*Built for the Korean-English worship community in Gwinnett County.*

---

## License

MIT — deploy it, fork it, adapt it for your church.

---

## CI/CD Pipeline

[![Pulpit CI](https://github.com/manynames3/pulpit/actions/workflows/ci.yml/badge.svg)](https://github.com/manynames3/pulpit/actions/workflows/ci.yml)

Every push and pull request runs:

| Step | Tool | Purpose |
|---|---|---|
| Format check | `terraform fmt` | Consistent code style |
| Syntax validate | `terraform validate` | Catch errors before deploy |
| Security scan | Checkov | Flag IaC misconfigurations (SOC 2 alignment) |
| Lint | TFLint | AWS-specific best practices |
| Plan | `terraform plan` | Preview changes, posted as PR comment |

Deploy is always a **manual step** — CI never auto-applies.

### GitHub Secrets required for plan job

```
AWS_ACCESS_KEY_ID      — IAM user with limited deploy permissions
AWS_SECRET_ACCESS_KEY  — corresponding secret
```

To add secrets: GitHub repo → Settings → Secrets and variables → Actions → New repository secret
