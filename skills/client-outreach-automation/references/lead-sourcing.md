# Lead Sourcing Reference

## Default Criteria

- Target small shops, studios, local service providers, creator businesses, independent brands, and small teams.
- Require a valid public business or cooperation email.
- Exclude large chains, major institutions, schools, hospitals, government offices, banks, insurance, gambling, political campaigns, spam, adult/NSFW, and clearly personal-only accounts.
- Use current public web data. Saved candidates are useful for patterns, but lead data can change.
- If the user asks for `不可有網站` or `沒有官網`, treat social profiles, Linktree, Portaly, lit.link, bio.site, marketplaces, forms, Google Maps, and directory profiles as not being an independent official website.

## Good Source Classes

- Public social entrance pages such as Portaly, Linktree, lit.link, and bio.site.
- Public association or member directories with email, address, and links.
- Public business directories where the email is visibly public.
- Public brand/store profile pages that explicitly invite cooperation, booking, orders, commissions, or customer inquiries.

## Workbook Columns

Use these columns for outreach-ready lists:

```text
店家名稱
店家類型
無自己的官網
Email
IG
FB
地址
電話
聯絡人
來源網址
狀態
備註
```

Required minimum columns for `run-outreach.py`:

```text
店家名稱
Email
聯絡人
地址
電話
來源網址
備註
狀態
```

Set `狀態` to `未聯絡` for new leads.

## Deduplication

Compare against:

- Every `outputs/**/validated_rows.json`.
- `client-outreach/outreach-history.csv`.
- `client-outreach/sent-drafts.csv`.
- `client-outreach/bounced-emails.csv`.
- The current batch itself.

Normalize and compare:

- Email.
- Phone.
- IG URL.
- FB URL.
- Google Maps URL.
- Official website / main URL.
- Store name.
- Store name + address when both exist.

If an email is known to bounce, add it with:

```bash
python3 client-outreach/run-outreach.py --add-bounced-email EMAIL --bounce-reason "mailbox not found" --bounce-source "user_reported_bounce"
```

## Validation

Before sending:

- `missingEmail = 0`.
- `badEmail = 0`.
- `dupSelf = 0`.
- `dupOld = 0` by email/social/main URL/name keys.
- Bounced emails are excluded.
- If `不可有網站`, every row has `無自己的官網 = 是` unless exceptions are clearly reported.
- `.xlsx` zip test passes with `unzip -t`.
- `run-outreach.py --dry-run` reports the expected pending count.
