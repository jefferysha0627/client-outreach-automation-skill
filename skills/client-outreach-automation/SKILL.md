---
name: client-outreach-automation
description: Build and operate a public-lead outreach workflow that searches for small-business prospects, exports deduped Excel/CSV lead lists, creates Gmail drafts, optionally sends approved outreach, and safely sends existing Gmail drafts. Use when the user asks to find customers/leads/shops, build outreach lists, run cold-email outreach from Excel/CSV, create Gmail drafts, one-click send Gmail drafts, dedupe outreach history, or package a prospecting-to-Gmail workflow.
---

# Client Outreach Automation

## Overview

Use this skill to help a user build a responsible prospecting workflow: search only public business contact data, export a clean lead file, dedupe against prior outreach, create Gmail drafts, and send only after explicit confirmation. The bundled tool lives in `assets/client-outreach/` and can be installed into the user's current project.

## Install The Tool

If the current project does not already contain `client-outreach/`, install the bundled scripts:

```bash
python3 <skill-dir>/scripts/install-client-outreach.py --target .
```

Replace `<skill-dir>` with the absolute path to this skill folder. The installer refuses to overwrite an existing `client-outreach/` unless `--force` is provided.

After installation, edit:

```text
client-outreach/config.json
client-outreach/templates/email-template*.txt
```

Set the sender name, identity, email, LINE or other contact method, daily limit, batch size, and templates before creating drafts or sending mail.

## Gmail Setup

Use the user's own Google Cloud OAuth client. Never ask for or expose another person's `.env`, OAuth token, Gmail refresh token, cookies, or client secret.

```bash
python3 client-outreach/setup-gmail-oauth.py
python3 client-outreach/run-outreach.py --check-setup
```

The Gmail scope is `https://www.googleapis.com/auth/gmail.compose`, which supports creating drafts and sending drafts created by this workflow.

## Lead Search Workflow

For requests like `找客戶`, `找店家`, `產出名單`, `中文可溝通`, or `Email 必填`:

1. Search current public web sources for business contacts intended for cooperation, booking, order, commission, or customer inquiry.
2. Export an `.xlsx` or `.csv` under `outputs/`.
3. Deduplicate against prior outputs, `outreach-history.csv`, `sent-drafts.csv`, `bounced-emails.csv`, and the current batch.
4. Run a dry-run before creating drafts or sending:
   ```bash
   python3 client-outreach/run-outreach.py --source-file "outputs/.../leads.xlsx" --dry-run
   ```

Read `references/lead-sourcing.md` for lead criteria, sources, workbook columns, and dedupe rules.

## Outreach Workflow

Draft-only, safest default:

```bash
python3 client-outreach/run-outreach.py --source-file "outputs/.../leads.xlsx" --dry-run
python3 client-outreach/run-outreach.py --source-file "outputs/.../leads.xlsx" --confirm
```

Direct send from a newly approved lead file:

```bash
python3 client-outreach/run-outreach.py --source-file "outputs/.../leads.xlsx" --dry-run
python3 client-outreach/run-outreach.py --source-file "outputs/.../leads.xlsx" --confirm --send --confirm-send
```

Never bypass `require_review`, `batch_size`, `daily_limit`, `processed-files.csv`, `outreach-history.csv`, `sent-drafts.csv`, bounced-email filtering, or foreign-language review.

## Send Existing Gmail Drafts

For terse requests like `送出` or `一鍵送出草稿`, send only drafts that still exist in live Gmail:

```bash
python3 client-outreach/send-drafts.py --all --confirm
python3 client-outreach/send-drafts.py --limit 1
```

The first command intersects local history with Gmail `users.drafts.list`, sends live draft IDs, and records results in `sent-drafts.csv`. The second command verifies the final live draft count.

## Safety Rules

- Use only public business contact information. Do not collect private personal data.
- Do not optimize for spam, evasion, or bypassing provider limits.
- Do not send unless the user explicitly asked to send and the command includes the tool's confirmation flags.
- List counts and skipped reasons before sending when practical.
- Stop if OAuth, Gmail API, template language review, or source-file validation fails.
- Keep `.env`, OAuth tokens, client secrets, lead files, logs, histories, and processed reports out of GitHub.
- For production or client use, remind the user to comply with local email, privacy, and unsubscribe requirements.

## Final Response

Keep the response concise. Include output file path, exported/read rows, dedupe result, drafts created or sent count, skipped reasons, processed report path, final Gmail draft count when sending, and any guard that stopped the workflow.
