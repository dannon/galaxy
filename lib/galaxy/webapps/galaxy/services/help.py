import html
import logging
import re
from typing import (
    NoReturn,
    Optional,
)

from galaxy.config import GalaxyAppConfiguration
from galaxy.exceptions import (
    GatewayTimeoutException,
    InternalServerError,
    ServerNotConfiguredForRequest,
    UpstreamProxyError,
)
from galaxy.schema.help import (
    HelpForumSearchResponse,
    HelpForumTopicContent,
)
from galaxy.security.idencoding import IdEncodingHelper
from galaxy.util import requests
from galaxy.webapps.galaxy.services.base import ServiceBase

log = logging.getLogger(__name__)


def _compose_search_query(
    query: str,
    *,
    solved_only: bool = False,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    order: Optional[str] = None,
) -> str:
    """Build a Discourse search string from a base query plus optional operators."""
    parts = [query.strip()]
    if solved_only:
        parts.append("status:solved")
    if category:
        parts.append(f"#{category}")
    if tags:
        parts.append("tags:" + ",".join(tags))
    if order:
        parts.append(f"order:{order}")
    return " ".join(part for part in parts if part)


def _html_to_text(cooked: str, max_length: int) -> str:
    """Strip Discourse `cooked` HTML to truncated plain text."""
    text = re.sub(r"<[^>]+>", " ", cooked or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "..."
    return text


class HelpService(ServiceBase):
    """Common interface/service logic for interactions with Galaxy Help API.

    Provides the logic of the actions invoked by API controllers and uses type definitions
    and pydantic models to declare its parameters and return types.
    """

    def __init__(
        self,
        security: IdEncodingHelper,
        config: GalaxyAppConfiguration,
    ):
        super().__init__(security)
        self.config = config

    def _auth_headers(self) -> dict:
        api_key = getattr(self.config, "help_forum_api_key", None)
        api_username = getattr(self.config, "help_forum_api_username", None)
        if api_key and api_username:
            return {"Api-Key": api_key, "Api-Username": api_username}
        return {}

    def _require_url(self) -> str:
        if not self.config.help_forum_api_url:
            raise ServerNotConfiguredForRequest("Help forum API URL is not configured.")
        return self.config.help_forum_api_url.rstrip("/")

    def search_forum(
        self,
        query: str,
        *,
        solved_only: bool = False,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        order: Optional[str] = None,
    ) -> HelpForumSearchResponse:
        """Search the Galaxy Help forum using the Discourse API."""
        base_url = self._require_url()
        composed = _compose_search_query(query, solved_only=solved_only, category=category, tags=tags, order=order)
        try:
            response = requests.get(
                url=f"{base_url}/search.json",
                params={"q": composed},
                headers=self._auth_headers(),
            )
        except requests.exceptions.ConnectionError:
            raise UpstreamProxyError(
                "Could not connect to the Galaxy Help Forum. The service may be temporarily unavailable."
            )
        except requests.exceptions.Timeout:
            raise GatewayTimeoutException("The request to the Galaxy Help Forum timed out. Please try again later.")
        except requests.exceptions.RequestException as e:
            raise InternalServerError(f"An error occurred while requesting the Galaxy Help Forum: {e}")

        if not response.ok:
            self._raise_for_status(response)
        try:
            return HelpForumSearchResponse(**response.json())
        except ValueError as e:
            raise InternalServerError(f"Received an unexpected response format from the Galaxy Help Forum: {e}")

    def get_topic(self, topic_id: int, max_length: int = 1500) -> HelpForumTopicContent:
        """Fetch one topic's question + accepted/top answer as truncated plain text."""
        base_url = self._require_url()
        try:
            response = requests.get(
                url=f"{base_url}/t/{topic_id}.json",
                headers=self._auth_headers(),
            )
        except requests.exceptions.ConnectionError:
            raise UpstreamProxyError("Could not connect to the Galaxy Help Forum.")
        except requests.exceptions.Timeout:
            raise GatewayTimeoutException("The request to the Galaxy Help Forum timed out.")
        except requests.exceptions.RequestException as e:
            raise InternalServerError(f"An error occurred while requesting the Galaxy Help Forum: {e}")

        if not response.ok:
            self._raise_for_status(response)
        data = response.json()
        posts = (data.get("post_stream") or {}).get("posts") or []
        title = data.get("title", "")
        slug = data.get("slug", "")
        url = f"{base_url}/t/{slug}/{topic_id}" if slug else f"{base_url}/t/{topic_id}"

        question = _html_to_text(posts[0].get("cooked", ""), max_length) if posts else ""

        answer_text: Optional[str] = None
        answer_is_accepted = False

        # Primary: per-post accepted_answer flag (real help.galaxyproject.org shape)
        for post in posts:
            if post.get("accepted_answer") is True:
                answer_text = _html_to_text(post.get("cooked", ""), max_length)
                answer_is_accepted = True
                break

        # Secondary (defensive -- other Discourse versions expose it at topic level)
        if answer_text is None:
            accepted = data.get("accepted_answer") or {}
            accepted_post_number = accepted.get("post_number")
            if accepted_post_number:
                for post in posts:
                    if post.get("post_number") == accepted_post_number:
                        answer_text = _html_to_text(post.get("cooked", ""), max_length)
                        answer_is_accepted = True
                        break

        # Fallback: most-liked reply
        if answer_text is None:
            replies = [p for p in posts[1:] if p.get("post_number")]
            if replies:
                top = max(replies, key=lambda p: p.get("like_count") or 0)
                answer_text = _html_to_text(top.get("cooked", ""), max_length)

        return HelpForumTopicContent(
            topic_id=topic_id,
            title=title,
            url=url,
            question=question,
            answer=answer_text,
            answer_is_accepted=answer_is_accepted,
        )

    def _raise_for_status(self, response) -> NoReturn:
        if response.status_code == 429:
            raise UpstreamProxyError("The Galaxy Help Forum is rate-limiting requests. Please try again shortly.")
        if 400 <= response.status_code < 500:
            raise InternalServerError(
                f"The Galaxy Help Forum returned an error (HTTP {response.status_code}). "
                "This may indicate a misconfigured URL or API key that requires admin intervention."
            )
        raise UpstreamProxyError(
            f"The Galaxy Help Forum returned an error (HTTP {response.status_code}). "
            "The service may be temporarily unavailable. Please try again later."
        )
