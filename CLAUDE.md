# Daily Data Jobs — Project Context for Claude Code

## What This Project Is
An automated LinkedIn content pipeline that scrapes US data roles 
posted in the last 24 hours, formats them into a daily LinkedIn post, 
and publishes at 8AM ET every weekday (Tue-Fri, skip Monday).

The value proposition: job seekers see fresh, curated, 
salary-transparent data roles every morning before their workday starts.

## Owner Constraints
- Zero or near-zero cost. Every architectural decision must respect this.
- Deployed on Oracle Cloud Always Free ARM VM (Ubuntu, 4 CPU, 24GB RAM)
- LLM: Azure OpenAI GPT-4.1-mini (student account, already deployed)
- No paid scraping proxies. No paid scheduling tools.
- Must run unattended for 12 months with minimal human intervention.

## Tech Stack — Non-Negotiable
- Language: Python 3.11+
- Scheduler: Linux cron on Oracle VM
- Scraping: Greenhouse API → Lever API → Dice → LinkedIn guest (tiered)
- LLM: Azure OpenAI GPT-4.1-mini via openai Python SDK
- LinkedIn Posting: /v2/ugcPosts endpoint (NOT /rest/posts)
- Token storage: local JSON file, encrypted with simple key
- Alerting: Gmail SMTP on failure
- Dependencies: managed via venv, pinned in requirements.txt

## Project File Structure
```
daily-data-jobs/
├── CLAUDE.md                  # This file
├── README.md
├── requirements.txt
├── .env                       # All secrets, never committed
├── .gitignore
├── config/
│   ├── companies.json         # ATS slug list (Greenhouse/Lever/Ashby)
│   └── settings.py            # All config constants
├── scraper/
│   ├── __init__.py
│   ├── greenhouse.py          # Greenhouse API scraper
│   ├── lever.py               # Lever API scraper
│   ├── dice.py                # Dice fallback scraper
│   ├── linkedin_guest.py      # LinkedIn guest API (tertiary)
│   └── deduplicator.py        # SHA-256 hash deduplication
├── pipeline/
│   ├── __init__.py
│   ├── node1_filter.py        # LLM Node 1: filter + categorise + score
│   ├── node2_rank.py          # LLM Node 2: listwise rank, top 2 per cat
│   └── node3_format.py        # LLM Node 3: format LinkedIn post text
├── publisher/
│   ├── __init__.py
│   ├── linkedin_auth.py       # OAuth flow + token refresh logic
│   └── linkedin_post.py       # ugcPosts API call
├── storage/
│   ├── tokens.json            # LinkedIn OAuth tokens (gitignored)
│   ├── seen_jobs.json         # Dedup hash store (rolling 7 days)
│   └── companies_requested.txt # Community company requests from comments
├── utils/
│   ├── __init__.py
│   ├── logger.py              # Structured logging
│   └── alerting.py            # Gmail SMTP failure alerts
├── tests/
│   ├── test_scraper.py
│   ├── test_pipeline.py
│   └── test_publisher.py
└── main.py                    # Orchestrator — called by cron
```

## Environment Variables (.env)
```
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_PERSON_URN=           # urn:li:person:{id}
ALERT_EMAIL_FROM=
ALERT_EMAIL_TO=
ALERT_EMAIL_PASSWORD=          # Gmail app password
TOKEN_ENCRYPTION_KEY=          # Simple Fernet key
```

## Scraping Architecture — Critical Details

### Priority Order (always try in this order)
1. Greenhouse: `boards-api.greenhouse.io/v1/boards/{slug}/jobs`
2. Lever: `api.lever.co/v0/postings/{slug}`
3. Dice: `dice.com/jobs?q={role}&filters.postedDate=1`
4. LinkedIn guest: `linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/`
   with `f_TPR=r86400&f_E=3,4&geoId=103644278`

### Target Job Categories (exactly 5, no others allowed)
- Data Engineer
- Data Analyst
- ML Engineer
- Data Scientist
- AI Engineer  (also matches: GenAI Engineer, AI/ML Engineer)

### Search Keywords per Category
- Data Engineer: "data engineer", "analytics engineer"
- Data Analyst: "data analyst", "business intelligence analyst"
- ML Engineer: "machine learning engineer", "MLOps engineer"
- Data Scientist: "data scientist", "applied scientist"
- AI Engineer: "AI engineer", "generative AI engineer", "LLM engineer"

### Experience Level Targeting
- Include: Mid-Senior, Senior, Staff, Principal, Lead
- Exclude: Junior, Entry-level, Intern, Graduate, 0-2 years
- LinkedIn f_E parameter: 3,4 (Mid-Senior + Director)

### Geography
- US only. Exclude: EMEA, APAC, Canada, Latin America
- Accept: Remote (US), specific US cities, "United States"

### Deduplication
- Generate SHA-256 hash of (company_name + job_title + location)
- Store in seen_jobs.json with timestamp
- Skip any job whose hash exists in last 7 days
- Purge hashes older than 7 days on each run

### ATS Company Slug List
- Start with 200 companies in config/companies.json
- Format: `{"greenhouse": ["airbnb", "stripe", "databricks", ...], "lever": ["netflix", "figma", ...]}`
- Fetch this list from open source GitHub repos on first setup

## LLM Pipeline — 3-Node Chain

### Node 1: Filter + Categorise + Score
- Batch size: 50-100 jobs per API call
- Output: JSON array via `json_object` mode (NOT `json_schema`)
- Scoring rubric (pointwise, additive):
  - +4 points: explicit salary range present
  - +3 points: explicitly Remote or "Anywhere in US"
  - +3 points: well-known company (FAANG, unicorn, Fortune 500)
- Failure conditions (hard exclude):
  - Title contains: junior, entry, intern, graduate, 0-2 years
  - Location outside US
  - Role is not one of the 5 categories
- Output schema per job:
  ```
  {original_id, category, title, company, location,
   salary_info, remote_status, stack_keywords,
   notability_score, source_platform}
  ```

### Node 2: Rank + Select Top 2 Per Category
- Input: filtered, scored jobs from Node 1
- Select exactly top 2 per category (5 categories = 10 jobs total)
- Tiebreaker: salary_info present > remote_status true > score
- If fewer than 2 in a category, use what's available
- Output: JSON object with 5 keys, one array per category

### Node 3: Format LinkedIn Post
- Input: ranked top 10 jobs from Node 2
- Output: plain text string ready to POST to LinkedIn
- Must follow post format exactly (see Post Format section)
- No markdown, no code blocks, no JSON in output
- Must count characters and stay under 2,800 (safety margin)

## LinkedIn Post Format — Exact Template
```
[HOOK — max 140 chars, must fit before "see more" cutoff]
10 data roles posted in the last 24 hours.
Salary included. Real companies hiring today.

⚙️ DATA ENGINEER
[Company] — [Job Title]
📍 [Location] | 💰 [Salary or "Undisclosed"]
Stack: [tech1] · [tech2] · [tech3]
🔗 [apply_url]

[Company] — [Job Title]
📍 [Location] | 💰 [Salary or "Undisclosed"]
Stack: [tech1] · [tech2] · [tech3]
🔗 [apply_url]

📊 DATA ANALYST
[repeat same format x2]

🤖 ML ENGINEER
[repeat same format x2]

🔬 DATA SCIENTIST
[repeat same format x2]

✨ AI / GEN AI ENGINEER
[repeat same format x2]

All posted today. Updated every morning at 8AM ET.
👇 Which company do you want in tomorrow's list?
#DataJobs #DataEngineering #AIJobs
```

### Post Rules
- Salary: always show if available. If not: show "Undisclosed"
- Stack: extract from job description, max 4 technologies
- Location: "Remote" / "Hybrid, [City]" / "[City], [State]"
- Links: direct apply URL in post body (accept reach tradeoff)
- Hashtags: exactly 3, always these exact 3
- Character limit: hard stop at 2,800 chars, truncate gracefully

## LinkedIn API — Critical Implementation Details

### Endpoint
- POST to: `https://api.linkedin.com/v2/ugcPosts`
- DO NOT use: `https://api.linkedin.com/rest/posts` (rejects consumer tokens)
- Required header: `X-Restli-Protocol-Version: 2.0.0`
- Required header: `Authorization: Bearer {access_token}`

### Payload Schema
```json
{
  "author": "urn:li:person:{id}",
  "lifecycleState": "PUBLISHED",
  "specificContent": {
    "com.linkedin.ugc.ShareContent": {
      "shareCommentary": {
        "text": "{post_text}"
      },
      "shareMediaCategory": "NONE"
    }
  },
  "visibility": {
    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
  }
}
```

### Token Management
- Access token TTL: 60 days
- Refresh token TTL: 365 days (fixed, does NOT roll)
- Auto-refresh: check daily, refresh if < 5 days remaining
- Refresh endpoint: `POST https://www.linkedin.com/oauth/v2/accessToken`
- Refresh payload: `grant_type=refresh_token` + `refresh_token` + `client_id` + `client_secret`
- Day 350 alert: send email warning that manual re-auth needed in 15 days
- Day 365: refresh token dies, catch `invalid_grant`, send CRITICAL alert
- Store tokens in: `storage/tokens.json` (gitignored, encrypted)
- Token file schema:
  ```json
  {access_token, refresh_token, access_expires_at, refresh_expires_at}
  ```

### OAuth Initial Setup
- Scopes needed: `openid profile email w_member_social`
- Callback URL: `http://localhost:8000/callback` (for initial setup only)
- Run setup once manually via: `python setup_oauth.py`
- setup_oauth.py opens browser, completes flow, saves tokens.json

## Failure Handling + Alerting

### What triggers an alert email
- Scraper returns 0 jobs after all sources tried
- LLM API call fails after 3 retries
- LinkedIn post returns non-201 status
- Token refresh fails (`invalid_grant` = CRITICAL)
- Any unhandled exception in main.py

### Alert email format
```
Subject: [DAILY DATA JOBS] {SEVERITY} — {error_type} — {date}
Body: timestamp, error message, full traceback, suggested action
```

### Retry logic
- LLM calls: 3 retries with exponential backoff (2s, 4s, 8s)
- LinkedIn post: 2 retries, then alert and skip day
- Scrapers: fail silently per source, move to next tier

## Cron Configuration
```cron
# Run Tue-Fri at 8AM ET (13:00 UTC)
0 13 * * 2-5 /home/ubuntu/daily-data-jobs/venv/bin/python \
  /home/ubuntu/daily-data-jobs/main.py >> \
  /home/ubuntu/daily-data-jobs/logs/cron.log 2>&1
```

## What NOT to Do — Hard Rules
- NEVER use `/rest/posts` endpoint
- NEVER use `json_schema` response_format with GPT-4.1-mini (breaks)
- NEVER put more than 5 hashtags in a post
- NEVER instruct users to use specific reactions (engagement bait penalty)
- NEVER commit `.env` or `tokens.json` to git
- NEVER run scrapers without delays between requests (min 2-3s sleep)
- NEVER use headless browser — too heavy, not needed for ATS APIs
- NEVER install packages globally — always use venv

## Testing Approach
- Each module has a `--dry-run` flag that prints output without posting
- test_scraper.py: verify each source returns jobs with correct schema
- test_pipeline.py: feed mock jobs, verify LLM output schema
- test_publisher.py: verify post payload structure (no actual posting)
- Run full dry run: `python main.py --dry-run`

## Key Research Decisions (Why We Built It This Way)
- **ugcPosts not rest/posts**: consumer-tier tokens rejected by modern endpoint
- **ATS-first scraping**: Greenhouse/Lever have free JSON APIs, zero bot risk
- **3-node LLM chain**: monolithic prompt fails on simultaneous filter+rank+format
- **json_object not json_schema**: strict schema breaks on GPT-4.1-mini
- **Personal profile not company page**: 5-8x more organic reach
- **Links in post body**: accept 60% reach tradeoff for zero-friction UX
- **No reaction voting**: engagement bait penalty in 2026 algorithm
- **Company request CTA**: drives genuine comments, builds feedback loop
- **Skip Monday**: lowest LinkedIn engagement day per 4.8M post analysis
- **8AM ET**: East Coast morning professionals, best for job content specifically
