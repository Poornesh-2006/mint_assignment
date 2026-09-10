import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "outputs"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

USER_AGENT = (
    "Mozilla/5.0 (compatible; RadialPulseSEOBot/1.0; "
    "+https://github.com/seo-audit-agent) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_MAX_PAGES = 40
REQUEST_TIMEOUT = 15
FETCH_DELAY_SEC = 0.3
MAX_HTML_BYTES = 1_500_000

COMMON_PATHS = [
    "/",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/aboutus",
    "/our-story",
    "/our-history",
    "/locations",
    "/location",
    "/address",
    "/find-us",
    "/findus",
    "/hours",
    "/hours-and-location",
    "/visit",
    "/visit-us",
    "/privacy",
    "/privacy-policy",
    "/menu",
    "/faqs",
    "/faq",
]

CITATION_HOSTS = (
    "yelp.com",
    "facebook.com",
    "bbb.org",
    "yellowpages.com",
    "maps.google.",
    "google.com/maps",
    "foursquare.com",
    "tripadvisor.com",
)
