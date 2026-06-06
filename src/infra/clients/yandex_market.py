from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, List

import requests
from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.domain.entities import Review
from src.infra.config.settings import Settings


class YandexMarketReview(BaseModel):
    """Customer review data from Yandex Market."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    _SOURCE_PAYLOAD_INCLUDE = {
        "feedback_id",
        "author",
        "description",
        "statistics",
        "identifiers",
        "media",
        "created_at",
        "need_reaction",
    }

    feedback_id: int = Field(alias="feedbackId")
    author: str | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")
    need_reaction: bool | None = Field(default=None, alias="needReaction")

    # Description
    comment: str | None = Field(default=None, alias="description.comment")
    advantages: str | None = Field(default=None, alias="description.advantages")
    disadvantages: str | None = Field(default=None, alias="description.disadvantages")

    # Statistics
    rating: int | None = Field(default=None, alias="statistics.rating")
    recommended: bool | None = Field(default=None, alias="statistics.recommended")
    comments_count: int | None = Field(default=None, alias="statistics.commentsCount")

    # Identifiers
    offer_id: str | None = Field(default=None, alias="identifiers.offerId")
    order_id: int | None = Field(default=None, alias="identifiers.orderId")

    # Media
    photos: List[str] | None = Field(default=None, alias="media.photos")
    videos: List[str] | None = Field(default=None, alias="media.videos")

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
    # Flatten nested structure for pydantic
    flat: dict[str, Any] = {
        "feedbackId": raw.get("feedbackId"),
        "author": raw.get("author"),
        "createdAt": raw.get("createdAt"),
        "needReaction": raw.get("needReaction"),
    }

    desc = raw.get("description") or {}
    flat["description.comment"] = desc.get("comment")
    flat["description.advantages"] = desc.get("advantages")
    flat["description.disadvantages"] = desc.get("disadvantages")

    stats = raw.get("statistics") or {}
    flat["statistics.rating"] = stats.get("rating")
    flat["statistics.recommended"] = stats.get("recommended")
    flat["statistics.commentsCount"] = stats.get("commentsCount")

    ids = raw.get("identifiers") or {}
    flat["identifiers.offerId"] = ids.get("offerId")
    flat["identifiers.orderId"] = ids.get("orderId")

    media = raw.get("media") or {}
    flat["media.photos"] = media.get("photos")
    flat["media.videos"] = media.get("videos")

    return YandexMarketReview(**flat)


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
        url = f"{self._base_url}{path}"
        response = requests.post(url, headers=self._headers, json=body, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def fetch_reviews(self) -> List[Review]:
        """Fetch unanswered reviews from Yandex Market."""
        max_age = self._config.reviews_max_age_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)

        all_reviews: List[Review] = []
        page_token: str | None = None

        while True:
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

        return all_reviews

    def publish_reply(self, review_id: str, message: str) -> None:
        """Publish a reply to a review on Yandex Market."""
        import time
        from requests.exceptions import RequestException

        body = {
            "feedbackId": int(review_id),
            "comment": {
                "text": message,
            },
        }

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                data = self._post(
                    f"/v2/businesses/{self._config.business_id}/goods-feedback/comments/update",
                    body,
                )
                status = data.get("status")
                if status == "ERROR":
                    error = data.get("error", {})
                    raise RuntimeError(f"Yandex Market API error: {error.get('code')} — {error.get('message')}")
                return
            except RequestException as exc:
                if attempt == max_attempts:
                    raise
                wait = 2 ** attempt
                print(f"  [YM publish] attempt {attempt}/{max_attempts} failed ({exc}), retrying in {wait}s…")
                time.sleep(wait)
