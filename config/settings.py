"""
All configuration constants for the Daily Data Jobs pipeline.
Import from here — never hardcode values in other modules.
"""

import os

# ---------------------------------------------------------------------------
# Job Categories & Keywords
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Data Engineer": ["data engineer", "analytics engineer"],
    "Data Analyst": ["data analyst", "business intelligence analyst"],
    "ML Engineer": ["machine learning engineer", "mlops engineer"],
    "Data Scientist": ["data scientist", "applied scientist"],
    "AI Engineer": [
        "ai engineer",
        "generative ai engineer",
        "llm engineer",
        "genai engineer",
        "ai/ml engineer",
    ],
}

# Ordered for post template rendering
CATEGORY_ORDER: list[str] = [
    "Data Engineer",
    "Data Analyst",
    "ML Engineer",
    "Data Scientist",
    "AI Engineer",
]

CATEGORY_EMOJIS: dict[str, str] = {
    "Data Engineer": "⚙️",
    "Data Analyst": "📊",
    "ML Engineer": "🤖",
    "Data Scientist": "🔬",
    "AI Engineer": "✨",
}

CATEGORY_HEADERS: dict[str, str] = {
    "Data Engineer": "⚙️ DATA ENGINEER",
    "Data Analyst": "📊 DATA ANALYST",
    "ML Engineer": "🤖 ML ENGINEER",
    "Data Scientist": "🔬 DATA SCIENTIST",
    "AI Engineer": "✨ AI / GEN AI ENGINEER",
}

# ---------------------------------------------------------------------------
# Experience-level exclusion terms (case-insensitive match against job title)
# ---------------------------------------------------------------------------

EXPERIENCE_EXCLUDE_TERMS: list[str] = [
    "junior",
    "entry",
    "entry-level",
    "entry level",
    "intern",
    "internship",
    "graduate",
    "new grad",
    "early career",
    "0-2 years",
    "0 to 2",
    "i -",     # "Level I -" patterns
    "level i",
]

# ---------------------------------------------------------------------------
# US geography acceptance terms (case-insensitive; substring match)
# ---------------------------------------------------------------------------

US_GEO_TERMS: list[str] = [
    "united states",
    "remote",
    "us remote",
    "anywhere in us",
    "usa",
    "u.s.",
    "new york",
    "san francisco",
    "seattle",
    "austin",
    "chicago",
    "boston",
    "los angeles",
    "denver",
    "atlanta",
    "miami",
    "dallas",
    "houston",
    "portland",
    "san diego",
    "phoenix",
    "minneapolis",
    "detroit",
    "philadelphia",
    "washington",
    "raleigh",
    "charlotte",
    "nashville",
    "salt lake city",
    "las vegas",
    "baltimore",
    "pittsburgh",
    "st. louis",
    "kansas city",
    "orlando",
    "tampa",
    "columbus",
    "indianapolis",
    "cincinnati",
    "memphis",
    "new orleans",
    "richmond",
    "sacramento",
    "san jose",
    "oakland",
    "san antonio",
    "fort worth",
    "el paso",
    "tucson",
    "albuquerque",
    "bakersfield",
    "fresno",
    "long beach",
    "virginia beach",
    "colorado springs",
    "omaha",
    "cleveland",
    "wichita",
    "arlington",
    "new haven",
    "hartford",
    "providence",
    "buffalo",
    "rochester",
    "albany",
    "birmingham",
    "louisville",
    "baton rouge",
    "jackson",
    "little rock",
    "des moines",
    "madison",
    "milwaukee",
    "spokane",
    "tacoma",
    "boise",
    "sioux falls",
    "fargo",
    ", ca",
    ", ny",
    ", tx",
    ", wa",
    ", il",
    ", ma",
    ", co",
    ", ga",
    ", fl",
    ", or",
    ", nc",
    ", tn",
    ", va",
    ", az",
    ", oh",
    ", mi",
    ", mn",
    ", nj",
    ", pa",
    ", md",
    ", mo",
]

# ---------------------------------------------------------------------------
# Scraper settings
# ---------------------------------------------------------------------------

SCRAPER_MIN_DELAY_SECONDS: float = 2.0
SCRAPER_MAX_DELAY_SECONDS: float = 3.5
SCRAPER_REQUEST_TIMEOUT: int = 15       # seconds per HTTP request
SCRAPER_MAX_AGE_HOURS: int = 24         # only jobs posted within this window

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

DEDUP_HASH_TTL_DAYS: int = 7

# ---------------------------------------------------------------------------
# LLM settings
# ---------------------------------------------------------------------------

LLM_MODEL_NAME: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
LLM_TEMPERATURE: float = 0.1
LLM_BATCH_SIZE: int = 75               # jobs per Node 1 API call
LLM_MAX_RETRIES: int = 3
LLM_BACKOFF_BASE: float = 2.0          # seconds; doubles each retry (2s, 4s, 8s)
LLM_API_VERSION: str = "2025-01-01-preview"

# ---------------------------------------------------------------------------
# LinkedIn publisher
# ---------------------------------------------------------------------------

LINKEDIN_UGCPOSTS_URL: str = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_TOKEN_REFRESH_URL: str = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_AUTH_URL: str = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_POST_MAX_CHARS: int = 2800
LINKEDIN_POST_RETRIES: int = 2

ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS: int = 5
REFRESH_TOKEN_WARNING_DAYS: int = 15   # alert when < 15 days remain on refresh token

# ---------------------------------------------------------------------------
# LinkedIn OAuth initial setup
# ---------------------------------------------------------------------------

OAUTH_CALLBACK_PORT: int = 8000
OAUTH_CALLBACK_URI: str = "http://localhost:8000/callback"
OAUTH_SCOPES: list[str] = ["openid", "profile", "email", "w_member_social"]

# ---------------------------------------------------------------------------
# File paths (relative to project root)
# ---------------------------------------------------------------------------

STORAGE_DIR: str = "storage"
TOKENS_PATH: str = "storage/tokens.json"
SEEN_JOBS_PATH: str = "storage/seen_jobs.json"
COMPANIES_PATH: str = "config/companies.json"
LOGS_DIR: str = "logs"
