# Deployment Guide

This project is deployed as:

- `Cloudflare Pages` for the static frontend
- `AWS` for Cognito, API Gateway, Lambda, DynamoDB, and Bedrock

This document covers:

1. Cloudflare Pages dashboard settings
2. Wrangler CLI deployment flow
3. AWS CORS changes required before production

## 1. Cloudflare Pages Dashboard Checklist

Use this if you want to deploy from the Cloudflare dashboard.

### Create the Pages project

In Cloudflare:

- Go to `Workers & Pages`
- Choose `Create application`
- Choose `Pages`

You can deploy either with Git integration or Direct Upload.

For this project, the simplest path is Direct Upload or a Git-connected static Pages project.

### Exact settings

Use these values:

- Project name: `pulpit-archive`
- Production branch: `main` if using Git
- Framework preset: `None`
- Build command: leave blank
- Build output directory: `frontend-alternative`
- Root directory: repository root

This frontend does not need a build step.

### Custom domain

Recommended:

- `archive.abethel.org`

Other acceptable options:

- `sermons.abethel.org`
- `pulpit.abethel.org`

After the first successful deploy:

- open the Pages project
- go to `Custom domains`
- attach the chosen subdomain

## 2. Wrangler CLI Deployment Flow

Use this if you want repeatable CLI deploys.

This repo already includes:

- [wrangler.toml](/Users/aiden/Documents/Codex/2026-04-21-read-pulpit-repo/wrangler.toml)

That file points Cloudflare Pages at:

- `frontend-alternative`

### First-time setup

From the repo root:

```bash
cd /Users/aiden/Documents/Codex/2026-04-21-read-pulpit-repo
npx wrangler login
```

Then create the Pages project:

```bash
npx wrangler pages project create pulpit-archive --production-branch main
```

### First production deploy

```bash
cd /Users/aiden/Documents/Codex/2026-04-21-read-pulpit-repo
npx wrangler pages deploy frontend-alternative --project-name pulpit-archive
```

### Preview deploy

```bash
cd /Users/aiden/Documents/Codex/2026-04-21-read-pulpit-repo
npx wrangler pages deploy frontend-alternative --project-name pulpit-archive --branch preview
```

### Useful follow-up commands

List Pages projects:

```bash
npx wrangler pages project list
```

Download Pages config from Cloudflare later if needed:

```bash
npx wrangler pages download config pulpit-archive
```

## 3. AWS CORS Update

This is required before the deployed frontend will work reliably.

The frontend runs in the browser and calls:

- Cognito directly
- API Gateway directly

Cognito is AWS-managed in this flow.
The main change you need to make is on API Gateway.

### Production origin to allow

Replace with your final Pages domain:

- `https://archive.abethel.org`

If you want to test against the default Pages hostname first, also allow:

- `https://pulpit-archive.pages.dev`

### If your API is an HTTP API

Recommended CORS settings:

- `allowOrigins`
  - `https://archive.abethel.org`
  - optionally `https://pulpit-archive.pages.dev`
- `allowMethods`
  - `POST`
  - `OPTIONS`
- `allowHeaders`
  - `authorization`
  - `content-type`
- `allowCredentials`
  - `true` only if you explicitly need it
- `maxAge`
  - `300`

Example AWS CLI command:

```bash
aws apigatewayv2 update-api \
  --api-id YOUR_API_ID \
  --cors-configuration AllowOrigins="https://archive.abethel.org","https://pulpit-archive.pages.dev" AllowMethods="POST","OPTIONS" AllowHeaders="authorization","content-type" MaxAge=300
```

### If your HTTP API uses a `$default` route with auth

Make sure `OPTIONS` requests are not blocked by authorization.

If needed, add:

- `OPTIONS /{proxy+}`

without authorization, as AWS documents for HTTP APIs with `$default` routes.

### If your API is a REST API instead

You need equivalent CORS handling on the REST API resources and methods:

- `POST`
- `OPTIONS`

and the same origin/header policy.

If you are unsure whether the API is REST or HTTP API, check it first before editing CORS.

## 4. Production Smoke Test

After deploy, verify all of this from the real Pages domain:

1. Page loads over HTTPS
2. Signup works
3. Email verification works
4. Login works
5. Search returns an answer
6. Source sermon cards render and open YouTube
7. Topic browser loads curated sermon cards
8. Browser console shows no CORS failures

## 5. Recommended Sequence

Use this order:

1. Choose the production subdomain
2. Update AWS API Gateway CORS for that exact origin
3. Create the Cloudflare Pages project
4. Deploy the static frontend
5. Attach the custom domain
6. Run production smoke tests

## 6. Why This Is The Right Split

Cloudflare Pages is handling the cheap part:

- static file hosting

AWS is handling the application part:

- identity
- API
- server-side logic
- AI retrieval

That keeps frontend cost low without forcing changes to the existing AWS backend.
