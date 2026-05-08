"""
data/news_fetcher.py
Fetches news headlines and scores them by relevance.
Keyword scoring works without any API key.
Optional: Claude API for deeper analysis when ANTHROPIC_API_KEY is set.
"""
import requests
import os
import time
import logging
from datetime import datetime, timedelta
from core.database import save_news
from core.logger import setup_logger

logger = setup_logger("NewsFetcher")

# Keywords that suggest a headline affects prediction markets
BULLISH_KEYWORDS = [
    "surges", "rally", "breakthrough", "approved", "wins", "victory",
    "rises", "jumps", "beats", "record high", "bullish", "positive",
    "passes", "confirmed", "elected", "agreement", "deal"
]
BEARISH_KEYWORDS = [
    "crash", "falls", "drops", "rejected", "ban", "loses", "collapse",
    "scandal", "arrested", "resign", "fails", "concern", "risk",
    "investigation", "crisis", "negative", "warning"
]
RELEVANCE_KEYWORDS = {
    "bitcoin": 5, "btc": 5, "ethereum": 4, "eth": 4,
    "solana": 4, "sol": 4, "crypto": 3, "blockchain": 2,
    "federal reserve": 4, "fed": 3, "interest rate": 4,
    "cpi": 4, "inflation": 3, "recession": 3,
    "election": 4, "president": 3, "congress": 2,
    "polymarket": 5, "prediction market": 5,
    "gold": 3, "oil": 2, "war": 3, "ceasefire": 3,
    "supreme court": 3, "sec": 3, "cftc": 3,
}


class NewsFetcher:
    """
    Fetches news and scores headlines for prediction market relevance.
    No Claude API needed - keyword scoring works standalone.
    Optionally upgrades to AI analysis when API key is present.
    """

    def __init__(self):
        self.news_api_key = os.getenv("NEWS_API_KEY", "")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.seen_headlines = set()
        self._last_ai_call = 0
        self._ai_calls_this_minute = 0

    # ------------------------------------------------------------------
    # Fetch Headlines
    # ------------------------------------------------------------------

    def fetch_headlines(self, query: str = "bitcoin crypto prediction",
                        page_size: int = 20) -> list:
        """
        Fetch latest headlines from NewsAPI.
        Returns list of {headline, source, url, published_at}.
        Falls back to mock headlines if no API key.
        """
        if not self.news_api_key:
            logger.warning("No NEWS_API_KEY - using mock headlines for testing")
            return self._mock_headlines()

        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": page_size,
                "from": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
                "apiKey": self.news_api_key
            }
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            articles = r.json().get("articles", [])
            results = []
            for a in articles:
                headline = a.get("title", "")
                if headline and headline not in self.seen_headlines:
                    self.seen_headlines.add(headline)
                    results.append({
                        "headline": headline,
                        "source": a.get("source", {}).get("name", ""),
                        "url": a.get("url", ""),
                        "published_at": a.get("publishedAt", "")
                    })
            logger.info(f"Fetched {len(results)} new headlines")
            return results
        except Exception as e:
            logger.error(f"NewsAPI fetch failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Keyword Scoring (no API needed)
    # ------------------------------------------------------------------

    def score_headline(self, headline: str) -> dict:
        """
        Score a headline for relevance and direction.
        Returns: {score, keywords_found, direction, confidence}
        Score 0-10: 0-4 low, 5-7 medium, 8+ high priority
        """
        h = headline.lower()
        score = 0
        keywords_found = []

        for kw, points in RELEVANCE_KEYWORDS.items():
            if kw in h:
                score += points
                keywords_found.append(kw)

        # Cap at 10
        score = min(score, 10)

        # Direction detection
        bullish_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in h)
        bearish_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in h)

        if bullish_hits > bearish_hits:
            direction = "BULLISH"
            confidence = min(0.5 + bullish_hits * 0.1, 0.85)
        elif bearish_hits > bullish_hits:
            direction = "BEARISH"
            confidence = min(0.5 + bearish_hits * 0.1, 0.85)
        else:
            direction = "NEUTRAL"
            confidence = 0.3

        return {
            "score": score,
            "keywords_found": keywords_found,
            "direction": direction,
            "confidence": confidence
        }

    # ------------------------------------------------------------------
    # AI Analysis (optional - only when Anthropic key is set)
    # ------------------------------------------------------------------

    def analyse_with_ai(self, headline: str) -> dict:
        """
        Use Claude Haiku to classify a headline.
        Rate limited to 10 calls/minute.
        Falls back to keyword scoring if no API key.
        """
        if not self.anthropic_key:
            return self.score_headline(headline)

        # Rate limiting - max 10/minute
        now = time.time()
        if now - self._last_ai_call < 60:
            if self._ai_calls_this_minute >= 10:
                logger.debug("AI rate limit reached - using keyword scoring")
                return self.score_headline(headline)
        else:
            self._ai_calls_this_minute = 0
            self._last_ai_call = now

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            prompt = f"""Classify this news headline for prediction market trading.
Headline: "{headline}"

Reply with ONLY a JSON object like:
{{"direction": "BULLISH|BEARISH|NEUTRAL", "confidence": 0.0-1.0, "markets_affected": ["bitcoin","ethereum","gold","politics"]}}"""

            msg = client.messages.create(
                model="claude-haiku-3-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            import json
            result = json.loads(msg.content[0].text)
            self._ai_calls_this_minute += 1
            keyword_score = self.score_headline(headline)
            return {
                "score": keyword_score["score"],
                "keywords_found": keyword_score["keywords_found"],
                "direction": result.get("direction", "NEUTRAL"),
                "confidence": result.get("confidence", 0.5),
                "ai_analysed": True,
                "markets_affected": result.get("markets_affected", [])
            }
        except Exception as e:
            logger.error(f"AI analysis failed: {e} - falling back to keyword scoring")
            return self.score_headline(headline)

    # ------------------------------------------------------------------
    # Main Process Loop
    # ------------------------------------------------------------------

    def process_and_store(self, headlines: list) -> list:
        """Score all headlines and save HIGH_PRIORITY ones to DB."""
        high_priority = []
        for item in headlines:
            headline = item.get("headline", "")
            if not headline:
                continue

            # Use AI if key present, otherwise keyword scoring
            if self.anthropic_key:
                analysis = self.analyse_with_ai(headline)
            else:
                analysis = self.score_headline(headline)

            save_news(
                headline=headline,
                source=item.get("source", ""),
                url=item.get("url", ""),
                relevance_score=analysis["score"],
                keywords_found=analysis["keywords_found"],
                ai_signal=analysis.get("direction"),
                ai_confidence=analysis.get("confidence")
            )

            if analysis["score"] >= 7:
                high_priority.append({**item, **analysis})
                logger.info(
                    f"HIGH PRIORITY [{analysis['score']}/10] "
                    f"{analysis['direction']} ({analysis['confidence']:.0%}): "
                    f"{headline[:80]}"
                )

        return high_priority

    def _mock_headlines(self) -> list:
        """Returns test headlines when no NewsAPI key is present."""
        return [
            {"headline": "Bitcoin surges past $90,000 after Fed signals rate pause",
             "source": "Mock News", "url": "", "published_at": datetime.utcnow().isoformat()},
            {"headline": "Ethereum network upgrade approved by core developers",
             "source": "Mock News", "url": "", "published_at": datetime.utcnow().isoformat()},
            {"headline": "SEC rejects new crypto ETF applications amid regulatory concerns",
             "source": "Mock News", "url": "", "published_at": datetime.utcnow().isoformat()},
        ]
