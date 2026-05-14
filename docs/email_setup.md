# Email setup runbook — Resend + GoDaddy DNS + Render

Production magic-link delivery depends on three things being wired up:

1. **Resend account** with API key
2. **`ekiptahmin.com` verified** in Resend (DNS records on GoDaddy)
3. **`RESEND_API_KEY` env var** set on Render

Until all three are done, prod uses the dummy email backend and silently
drops every email. A startup-time WARNING line lands in Render logs to make
this state visible — search for `RESEND_API_KEY not set` after each deploy.

## Step 1 — Resend account + API key

1. Sign up at https://resend.com (free tier: 3 000 mails/month, 100/day).
2. After login: **API Keys → Create API Key**.
3. Permissions: **Full Access** (sending only is fine, but full is simpler).
4. Copy the key — starts with `re_`. **You will not see it again.**

## Step 2 — Add and verify the domain

1. **Domains → Add Domain** → `ekiptahmin.com`.
2. Resend shows ~3 DNS records to add:
   - `MX  send.ekiptahmin.com → feedback-smtp.<region>.amazonses.com` (priority 10)
   - `TXT send.ekiptahmin.com → "v=spf1 include:amazonses.com ~all"`
   - `TXT resend._domainkey.ekiptahmin.com → "<long DKIM key>"`
3. Leave this tab open — you need the exact values.

## Step 3 — GoDaddy DNS

1. GoDaddy → **My Products → Domains → ekiptahmin.com → DNS**.
2. For each record Resend gave you:
   - Click **Add New Record**
   - Pick the matching type (MX / TXT)
   - **Name** field: enter only the subdomain part. GoDaddy auto-appends
     `.ekiptahmin.com`. So for `send.ekiptahmin.com` the Name is `send`,
     for `resend._domainkey.ekiptahmin.com` the Name is `resend._domainkey`.
   - **Value**: paste exactly. No surrounding quotes for GoDaddy's UI.
   - **TTL**: 1 hour is fine.
3. Save. Propagation usually takes 5–30 min.
4. Back in Resend's domain page, click **Verify**. Repeat every few
   minutes until all three rows turn green.

## Step 4 — Render env var

1. Render dashboard → **ekiptahmin** service → **Environment**.
2. Find `RESEND_API_KEY` (it's listed but unset — `sync: false` in
   `render.yaml`).
3. Paste the `re_...` key you copied in Step 1.
4. **Save Changes** — Render auto-redeploys.

## Step 5 — Verify in production

Once the redeploy finishes:

1. Render dashboard → service → **Shell** tab.
2. Run:
   ```bash
   python manage.py send_test_email <your-email>
   ```
3. Expected output:
   ```
   EMAIL_BACKEND     = django.core.mail.backends.smtp.EmailBackend
   DEFAULT_FROM_EMAIL = noreply@ekiptahmin.com
   ...
   send_mail returned 1 — request accepted by SMTP server.
   ```
4. Check the inbox (and spam). If it arrives, magic links are live.

## Troubleshooting

**Test command says `backend is dummy`.**
→ `RESEND_API_KEY` env var isn't visible to the running process. Make sure
you saved it in Render and the redeploy completed.

**`send_mail` raises `SMTPAuthenticationError`.**
→ The API key is wrong or revoked. Generate a new one in Resend, update
the Render env var.

**`send_mail` returns 1 but mail never arrives.**
→ Domain isn't fully verified, or it's in spam. Re-check the three DNS
rows in Resend (all green?). Check Resend's **Emails** dashboard — every
attempt is logged there with delivery status.

**Resend dashboard shows "bounced" or "complained".**
→ Recipient mailbox doesn't exist or marked us as spam. Resend will
auto-suppress future sends to that address.

## What's NOT yet hooked up

- Reminder emails (round deadline approaching, etc.) — planned, not built.
- Bounce/complaint webhook → no automatic invite-revocation. Manual for
  now via the Resend dashboard.
