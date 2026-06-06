from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any, List

import requests
from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.domain.entities import Review
from src.infra.config.settings import Settings


class YandexMarketReview(BaseModel):
    """Customer review data from Yandex Market."""

    model_config = ConfigDict(frozen=True)

    feedback_id: int
    author: str | None = None
    created_at: datetime | None = None
    need_reaction: bool | None = None
    comment: str | None = None
    advantages: str | None = None
    disadvantages: str | None = None
    rating: int | None = None
    recommended: bool | None = None
    comments_count: int | None = 0
    offer_id: str | None = None
    order_id: int | None = None
    photos: List[str] | None = None
    videos: List[str] | None = None

    @property
    def summary(self) -> str:
        parts = []
        if self.comment:
            parts.append(self.comment)
        if self.advantages:
            parts.append(f"Плюсы: {self.advantages}")
        if self.disadvantages:
            parts.append(f"Минусы: {self.disadvantages}")
        if self.rating:
            parts.append(f"Оценка: {self.rating}/5")
        return "\n".join(filter(None, parts))

    @computed_field
    def has_photos(self) -> bool:
        return bool(self.photos)

    @computed_field
    def has_video(self) -> bool:
        return bool(self.videos)

    def to_source_payload(self) -> str:
        payload = {
            "feedbackId": self.feedback_id,
            "author": self.author,
            "comment": self.comment,
            "advantages": self.advantages,
            "disadvantages": self.disadvantages,
            "rating": self.rating,
            "recommended": self.recommended,
            "offerId": self.offer_id,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "hasPhotos": self.has_photos,
            "hasVideo": self.has_video,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def to_review(self) -> Review:
        return Review(id=str(self.feedback_id), text=self.to_source_payload(), summary=self.summary)


def _parse_review(raw: dict[str, Any]) -> YandexMarketReview:
    """Parse a raw API feedback dict into a YandexMarketReview."""
    desc = raw.get("description") or {}
    stats = raw.get("statistics") or {}
    ids = raw.get("identifiers") or {}
    media = raw.get("media") or {}

    return YandexMarketReview(
        feedback_id=raw.get("feedbackId", 0),
        author=raw.get("author"),
        created_at=raw.get("createdAt"),
        need_reaction=raw.get("needReaction"),
        comment=desc.get("comment"),
        advantages=desc.get("advantages"),
        disadvantages=desc.get("disadvantages"),
        rating=stats.get("rating"),
        recommended=stats.get("recommended"),
        comments_count=stats.get("commentsCount", 0),
        offer_id=ids.get("offerId"),
        order_id=ids.get("orderId"),
        photos=media.get("photos"),
        videos=media.get("videos"),
    )


class YandexMarketClient:
    """HTTP client for Yandex Market Partner API (fetch + publish reviews)."""

    def __init__(self, settings: Settings):
        self._config = settings.yandex_market
        self.settings = settings
        self.timeout = self._config.request_timeout
        self.batch_size = self._config.batch_size
        self._base_url = self._config.base_url.rstrip("/")
        self._headers = {
            "Api-Key": self._config.api_token,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with retry and rate limiting."""
        from requests.exceptions import RequestException

        url = f"{self._base_url}{path}"
        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(url, headers=self._headers, json=body, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except RequestException as exc:
                last_exc = exc
                if attempt < max_attempts:
                    wait = 2 ** attempt
                    print(f"  [YM] attempt {attempt}/{max_attempts} failed ({type(exc).__name__}), retrying in {wait}s…")
                    time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    def fetch_reviews(self) -> List[Review]:
        """Fetch unanswered reviews from Yandex Market."""
        max_age = self._config.reviews_max_age_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)

        all_reviews: List[Review] = []
        seen_ids: set[str] = set()
        page_token: str | None = None
        page = 0
        max_pages = self._config.max_pages

        while True:
            page += 1
            if page > max_pages:
                print(f"  [YM] reached max_pages limit ({max_pages}), stopping pagination")
                break
            body: dict[str, Any] = {
                "limit": self.batch_size,
                "reactionStatus": "NEED_REACTION",
            }
            if page_token:
                body["pageToken"] = page_token

            data = self._post(f"/v2/businesses/{self._config.business_id}/goods-feedback", body)

            # Check status
            status = data.get("status")
            if status == "ERROR":
                error = data.get("error", {})
                raise RuntimeError(f"Yandex Market API error: {error.get('code')} — {error.get('message')}")

            result = data.get("result") or {}
            feedbacks = result.get("feedbacks") or []
            paging = result.get("paging") or {}

            for raw in feedbacks:
                try:
                    ym_review = _parse_review(raw)
                except Exception as e:
                    print(f"  [YM parse] skipping malformed review: {e}")
                    continue

                # Dedup (API may return overlapping pages)
                review_id = str(ym_review.feedback_id)
                if review_id in seen_ids:
                    continue
                seen_ids.add(review_id)

                # Filter by age
                if ym_review.created_at and ym_review.created_at < cutoff:
                    continue

                # Skip reviews with no text content
                if not ym_review.summary.strip():
                    continue

                all_reviews.append(ym_review.to_review())

            page_token = paging.get("nextPageToken")
            if not page_token or not feedbacks:
                break

            # Rate limiting: small delay between pages to avoid hitting API limits
            time.sleep(0.5)

        return all_reviews

    def publish_reply(self, review_id: str, message: str) -> None:
        """Publish a reply to a review on Yandex Market."""
        body = {
            "feedbackId": int(review_id),
            "comment": {
                "text": message,
            },
        }

        data = self._post(
            f"/v2/businesses/{self._config.business_id}/goods-feedback/comments/update",
            body,
        )
        status = data.get("status")
        if status == "ERROR":
            error = data.get("error", {})
            raise RuntimeError(f"Yandex Market API error: {error.get('code')} — {error.get('message')}")
