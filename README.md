# 🎯 Job Hunter — Weekly Job Digest

Automated workflow that searches for **Finance Director / Head of FP&A** positions in Barcelona every Saturday and sends a scored, premium HTML email digest.

## Architecture

```
GitHub Actions (Every Saturday 11:30 CEST)
│
├── 1. FETCH — Adzuna API → search 8 finance queries in Barcelona
│   └── Match against 42 target companies (Tier A/B/C + Hidden)
│
├── 2. SCORE — Claude AI (Haiku) → 10-dimension rubric scoring (A-D)
│   └── Coty correction applied, HQP risk flagged
│
├── 3. GENERATE — Build premium dark-theme HTML email
│   └── Grouped by tier: A → B → C → D
│
└── 4. SEND — Gmail SMTP → bicenorkun@gmail.com
```

## Setup (One-time, ~10 minutes)

### Step 1: Adzuna API (Free — job search database)

1. Go to **[developer.adzuna.com](https://developer.adzuna.com)**
2. Click **Register** → fill in name/email (no credit card)
3. You'll receive an email with your `App ID` and `App Key`

### Step 2: Gmail App Password (Free — email sending)

1. Go to **[myaccount.google.com/security](https://myaccount.google.com/security)**
2. Make sure **2-Step Verification** is turned ON
3. Search for **"App passwords"** on that page
4. Create a new App Password → select **Mail** → type `job-hunter`
5. Copy the 16-character password (looks like: `abcd efgh ijkl mnop`)

### Step 3: Add Secrets to GitHub

Go to: **[github.com/orknn/Job-Hunter/settings/secrets/actions](https://github.com/orknn/Job-Hunter/settings/secrets/actions)**

Click **"New repository secret"** for each:

| Secret Name | Value | Where to get it |
|---|---|---|
| `ADZUNA_APP_ID` | Your App ID | developer.adzuna.com registration email |
| `ADZUNA_APP_KEY` | Your App Key | developer.adzuna.com registration email |
| `ANTHROPIC_API_KEY` | Your Claude API key | console.anthropic.com → API Keys |
| `GMAIL_USERNAME` | `bicenorkun@gmail.com` | Your email |
| `GMAIL_APP_PASSWORD` | 16-char app password | Step 2 above |

### Step 4: Test Run

Go to: **[github.com/orknn/Job-Hunter/actions](https://github.com/orknn/Job-Hunter/actions)** → Click **"Weekly Job Digest"** → **"Run workflow"** → **"Run workflow"**

## Cost

| Service | Monthly Cost |
|---|---|
| Adzuna API | **€0** (free tier: 2,500 calls/month) |
| Claude AI (Haiku) | **~€0.20** (4 runs × ~50 jobs) |
| Gmail SMTP | **€0** |
| **Total** | **< €0.50/month** |

## Files

```
Job Hunter/
├── .github/workflows/job_digest.yml    # GitHub Actions cron workflow
├── scripts/
│   ├── fetch_jobs.py                   # Adzuna API job fetcher
│   ├── score_jobs.py                   # Claude AI scorer (10 dimensions)
│   ├── generate_email.py               # Premium HTML email builder
│   └── send_email.py                   # Gmail SMTP sender
├── data/
│   └── target_companies.json           # 42 target companies with tiers
├── requirements.txt                    # Python dependencies
└── README.md                           # This file
```
