# Daily Data Jobs — Project Context for Claude Code

## What This Project Is
An automated LinkedIn content pipeline that scrapes US data roles 
posted in the last 24 hours, formats them into a daily LinkedIn post, 
and publishes at 8AM ET every weekday (Mon-Fri).

The value proposition: job seekers see fresh, curated, 
salary-transparent data roles every morning before their workday starts.

## Owner Constraints
- Zero or near-zero cost. Every architectural decision must respect this.
- Deployed on GitHub Actions (public repo = unlimited free minutes)
- LLM: Azure OpenAI GPT-4.1 (student account, already deployed)
- No paid scraping proxies. No paid scheduling tools.
- Must run unattended for 12 months with minimal human intervention.

## Tech Stack — Non-Negotiable
- Language: Python 3.11+
- Scheduler: GitHub Actions cron (Mon-Fri 11:00 UTC = 6AM ET)
- Scraping: Greenhouse API → Lever API → Dice → LinkedIn guest (tiered)
- LLM: Azure OpenAI GPT-4.1 via openai Python SDK (AzureOpenAI client)
- LinkedIn Posting: /v2/ugcPosts endpoint (NOT /rest/posts)
- Image generation: Pillow (local, zero cost) — 1200x627 PNG header per post
- Token storage: storage/tokens.json encrypted with Fernet, stored as GitHub Secret
- Alerting: Telegram Bot API (NOT Gmail SMTP)
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
│   ├── image_generator.py     # Pillow 1200x627 header image — no external APIs
│   ├── linkedin_auth.py       # OAuth flow + token refresh logic
│   └── linkedin_post.py       # ugcPosts API call + image upload + first comment
├── storage/
│   ├── tokens.json            # LinkedIn OAuth tokens (gitignored, stored as GitHub Secret)
│   ├── seen_jobs.json         # Dedup hash store (rolling 7 days, committed to repo)
│   └── companies_requested.txt # Community company requests from comments
├── utils/
│   ├── __init__.py
│   ├── logger.py              # Structured logging
│   └── alerting.py            # Telegram Bot API failure alerts
├── tests/
│   ├── test_scraper.py
│   ├── test_pipeline.py
│   └── test_publisher.py
└── main.py                    # Orchestrator — called by cron
```

## Environment Variables (.env locally / GitHub Secrets in CI)
```
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_PERSON_URN=           # urn:li:person:{id} — auto-written by setup_oauth.py
TELEGRAM_BOT_TOKEN=            # @Daily_jobs_justin_bot
TELEGRAM_CHAT_ID=              # numeric chat ID — get via get_telegram_chat_id()
TOKEN_ENCRYPTION_KEY=          # Fernet key
LINKEDIN_TOKENS_JSON=          # base64(tokens.json) — GitHub Secret only
```

## GitHub Actions — Deployment Details
- Workflow: `.github/workflows/daily_post.yml`
- Schedule: `0 11 * * 1-5` (6AM ET Mon-Fri)
- Runner: ubuntu-latest (public repo = free)
- Secrets set via: `gh secret set <NAME> --body <VALUE>`
- tokens.json stored as base64 secret LINKEDIN_TOKENS_JSON, decoded at runtime
- seen_jobs.json committed back to repo after each run (not gitignored)
- Typical runtime: ~21 minutes (Greenhouse ~5.5 min + Lever ~15 min + LLM ~6s)
- Lever is slower on GitHub Actions (~929s) vs local (~429s) due to runner network
- To re-enable after disabling: `gh workflow enable daily_post.yml`
- To trigger manually: `gh workflow run daily_post.yml`
- Token refresh: when access token expires (~60 days), run setup_oauth.py locally,
  then: `gh secret set LINKEDIN_TOKENS_JSON --body "$(base64 -i storage/tokens.json)"`

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
[HOOK — dynamic, built from top 3 company names in ranked jobs]
Fresh data roles from {Company1}, {Company2} & more.
Salary included. Posted in the last 24 hours.

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

All posted today.
🔗 Apply links in first comment below ↓
👥 Know someone job hunting? Tag them — you might change their week.
🤝 Work at one of these companies and open to referring? Comment 'referral' + the company name and job seekers can reach out to you directly.
#DataJobs #DataEngineering #AIJobs
```

### Post Rules
- Salary: always show if available. If not: show "Undisclosed"
- Stack: extract from job description, max 4 technologies
- Location: "Remote" / "Hybrid, [City]" / "[City], [State]"
- Apply links: posted as first comment immediately after publish (NOT in post body)
- Hashtags: exactly 3, always these exact 3
- Character limit: hard stop at 2,800 chars, truncate gracefully

### First Comment — Apply Links
- Posted immediately after 201 response from ugcPosts
- Endpoint: `POST /v2/socialActions/{encodedPostUrn}/comments`
- Format: one line per job — `[Title] @ [Company] → [url]`
- Failure is non-fatal: log error + Telegram alert, post stays live
- Dry-run: prints comment payload to stdout, no API call

## LinkedIn API — Critical Implementation Details

### Endpoint
- POST to: `https://api.linkedin.com/v2/ugcPosts`
- DO NOT use: `https://api.linkedin.com/rest/posts` (rejects consumer tokens)
- Required header: `X-Restli-Protocol-Version: 2.0.0`
- Required header: `Authorization: Bearer {access_token}`

### Payload Schema (text-only)
```json
{
  "author": "urn:li:person:{id}",
  "lifecycleState": "PUBLISHED",
  "specificContent": {
    "com.linkedin.ugc.ShareContent": {
      "shareCommentary": { "text": "{post_text}" },
      "shareMediaCategory": "NONE"
    }
  },
  "visibility": { "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC" }
}
```

### Payload Schema (with image)
```json
{
  "author": "urn:li:person:{id}",
  "lifecycleState": "PUBLISHED",
  "specificContent": {
    "com.linkedin.ugc.ShareContent": {
      "shareCommentary": { "text": "{post_text}" },
      "shareMediaCategory": "IMAGE",
      "media": [{
        "status": "READY",
        "media": "{assetUrn}",
        "title": { "text": "Today's top data roles" }
      }]
    }
  },
  "visibility": { "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC" }
}
```

### Image Upload Flow (before ugcPosts call)
1. Generate PNG: `publisher/image_generator.py` → `/tmp/ddj_header_{YYYYMMDD}.png`
2. Register: `POST /v2/assets?action=registerUpload` with recipe `urn:li:digitalmediaRecipe:feedshare-image`
3. Upload: `PUT {uploadUrl}` with `Content-Type: application/octet-stream`
4. Use returned `asset` URN in ugcPosts payload
- If any step fails: log + Telegram alert, fall back to text-only post (non-fatal)

### Token Management
- Access token TTL: 60 days
- LinkedIn consumer apps do NOT return a refresh token — manual re-auth required every ~60 days
- Auto-refresh logic exists in linkedin_auth.py but only works if refresh token is available
- Telegram alert sent when access token < 5 days from expiry
- Store tokens in: `storage/tokens.json` (gitignored locally, stored as LINKEDIN_TOKENS_JSON GitHub Secret)
- Token file schema:
  ```json
  {access_token, refresh_token, access_expires_at, refresh_expires_at}
  ```

### OAuth Initial Setup
- Scopes needed: `openid profile email w_member_social` (NOT offline_access — not supported by consumer apps)
- DO NOT add offline_access — LinkedIn rejects it with invalid_scope error
- Callback URL: `http://localhost:8000/callback` (for initial setup only)
- Run setup once manually via: `python setup_oauth.py`
- setup_oauth.py opens browser, completes flow, saves tokens.json, writes LINKEDIN_PERSON_URN to .env
- Person URN fetched via `/v2/userinfo` (OpenID Connect) NOT `/v2/me` (deprecated, returns 403)

## Failure Handling + Alerting

### Alerting: Telegram Bot (NOT Gmail)
- Bot: @Daily_jobs_justin_bot
- send_alert(level, error_type, message, traceback) in utils/alerting.py
- Uses requests.post to api.telegram.org/bot{token}/sendMessage with HTML parse_mode
- get_telegram_chat_id() helper: calls getUpdates to find chat ID after sending /start

### What triggers an alert
- Scraper returns 0 jobs after all sources tried
- LLM API call fails after 3 retries
- LinkedIn post returns non-201 status
- Token refresh fails (`invalid_grant` = CRITICAL)
- Any unhandled exception in main.py

### Retry logic
- LLM calls: 3 retries with exponential backoff (2s, 4s, 8s)
- LinkedIn post: 2 retries, then alert and skip day
- Scrapers: fail silently per source, move to next tier

## Scheduling
Handled by GitHub Actions — see `.github/workflows/daily_post.yml`.
Cron: `0 13 * * 1-5` (Mon-Fri 8AM ET). No Oracle VM or local cron needed.

## What NOT to Do — Hard Rules
- NEVER use `/rest/posts` endpoint
- NEVER use `json_schema` response_format with GPT-4.1 (breaks — use `json_object` only)
- NEVER put more than 5 hashtags in a post
- NEVER instruct users to use specific reactions (engagement bait penalty)
- NEVER commit `.env` or `tokens.json` to git
- NEVER run scrapers without delays between requests (min 2-3s sleep)
- NEVER use headless browser — too heavy, not needed for ATS APIs
- NEVER install packages globally — always use venv
- NEVER add `offline_access` to OAuth scopes — LinkedIn consumer apps reject it
- NEVER call `/v2/me` for person URN — use `/v2/userinfo` and read the `sub` field

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
- **json_object not json_schema**: strict schema breaks on GPT-4.1
- **Personal profile not company page**: 5-8x more organic reach
- **Apply links in first comment, not post body**: LinkedIn algorithm penalises outbound links in post body (~60% reach reduction); first comment preserves full reach with zero-friction UX
- **No reaction voting**: engagement bait penalty in 2026 algorithm
- **Tag-a-friend + referral CTA**: replaced job-title CTA; drives shares (organic reach) and insider referral connections for job seekers
- **Dynamic hook**: company names pulled from ranked jobs each day so hook is always fresh
- **GitHub Actions not Oracle VM**: zero infrastructure to manage, free on public repos
- **seen_jobs.json in repo**: committed back after each run so dedup persists across Actions runs
- **Salary regex minimum $1,000**: bare values like "$124" appear in job description text and must not match
- **apply_url re-attached after Node 1**: Pydantic FilteredJob model_dump() drops fields not in schema — re-attach via original_id lookup
- **6AM ET**: moved from 8AM ET; hits feeds before the morning scroll peak
- **Mon-Fri**: expanded from Tue-Fri after launch
- **Pillow for image generation**: zero cost, no external APIs, DejaVu Sans pre-installed on ubuntu-latest runner; hero job selected by highest salary ceiling
- **Image upload non-fatal**: if registerUpload or PUT fails, pipeline falls back to text-only post and sends Telegram alert — never blocks the publish
- **0 jobs on weekend manual trigger is expected**: 24-hour recency filter correctly returns nothing when no companies post on weekends; scheduled weekday runs are unaffected

## Header Image — publisher/image_generator.py
- Canvas: 1200×627px, background #0F1117
- Hero job: auto-selected as the job with the highest salary ceiling across all ranked jobs
- Layout: "Today's top data roles" label → company (bold white, 58px) → role title → salary (teal #00C896, 52px) → 5 category pills (centered) → divider → footer
- Font: DejaVu Sans (Ubuntu/CI) → Liberation Sans → Arial → macOS Helvetica → Pillow default
- Output: `/tmp/ddj_header_{YYYYMMDD}.png` (dry-run: `/tmp/ddj_header_DRYRUN.png`)
- Dry-run: generates image, prints path, sets asset URN to placeholder — no LinkedIn upload
