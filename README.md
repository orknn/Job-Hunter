# 🎯 Job Hunter — Weekly Job Digest

Automated workflow that searches for **Finance Director / Head of FP&A** positions in Barcelona every Saturday and sends a scored, premium HTML email digest.

## Architecture

```
GitHub Actions (Every Saturday 11:30 CEST)
│
├── 1. FETCH — three sources, one title gate (scripts/titles.py)
│   ├── Adzuna API → finance queries in Barcelona
│   ├── Career sites of the target companies (Workday, Greenhouse, Ashby, …)
│   └── LinkedIn public guest search → companies nobody thought to list
│
├── 2. SCORE — OpenAI (gpt-5.6-luna) → 10-dimension rubric scoring (A-D)
│   ├── Two lanes: Director/Head-level targets, and one rung below
│   │   (Senior Manager / Senior Finance Business Partner)
│   └── Every cut is recorded with its reason
│
├── 3. TRACK — data/seen_jobs.json marks what earlier digests already showed
│
├── 4. ANALYZE GAPS — OpenAI (gpt-5.6-luna) → weekly positioning gap report
│
├── 5. GENERATE — dark-theme HTML email
│   └── Tiers A → D, then the step-down lane, then the filtered-out audit list
│
└── 6. SEND — Gmail SMTP → bicenorkun@gmail.com
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
| `OPENAI_API_KEY` | Your OpenAI API key | platform.openai.com → API Keys |
| `GMAIL_USERNAME` | `bicenorkun@gmail.com` | Your email |
| `GMAIL_APP_PASSWORD` | 16-char app password | Step 2 above |

### Step 4: Test Run

Go to: **[github.com/orknn/Job-Hunter/actions](https://github.com/orknn/Job-Hunter/actions)** → Click **"Weekly Job Digest"** → **"Run workflow"** → **"Run workflow"**

Tick **dry_run** to build the digest without sending mail or touching `seen_jobs.json`; the result is in the run's artifact.

## Cost

| Service | Monthly Cost |
|---|---|
| Adzuna API | **€0** (free tier: 2,500 calls/month) |
| OpenAI (gpt-5.6-luna) | **~€0.20** (4 runs × ~50 jobs, scoring + gap analysis) |
| Gmail SMTP | **€0** |
| **Total** | **< €0.50/month** |

## Files

```
Job Hunter/
├── .github/workflows/job_digest.yml    # GitHub Actions cron workflow
├── scripts/
│   ├── titles.py                       # title gate shared by every fetcher
│   ├── fetch_jobs.py                   # Adzuna API job fetcher
│   ├── fetch_ats.py                    # target companies' own career sites
│   ├── fetch_linkedin.py               # LinkedIn guest search (low volume, see its docstring)
│   ├── llm.py                          # the one place this repo calls a model
│   ├── score_jobs.py                   # rubric scorer (10 dimensions)
│   ├── track_seen.py                   # remembers what earlier digests showed
│   ├── analyze_gaps.py                 # Weekly positioning gap report (B/C tier near-misses)
│   ├── generate_email.py               # Premium HTML email builder
│   └── send_email.py                   # Gmail SMTP sender
├── data/
│   ├── target_companies.json           # target companies with tiers and career-site configs
│   └── seen_jobs.json                  # written by the workflow (hashes + dates only)
├── requirements.txt                    # Python dependencies
└── README.md                           # This file
```
