"""TheDiscDB GraphQL client for disc content identification."""

import logging
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

DISCDB_URL = "https://thediscdb.com/graphql/"

DISC_QUERY = """
query DiscByHash($hash: String!) {
  mediaItems(
    where: {
      releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } }
    }
  ) {
    nodes {
      title
      year
      type
      externalids { tmdb imdb }
      releases {
        discs(order: { index: ASC }) {
          contentHash
          format
          titles(order: { index: ASC }) {
            index
            sourceFile
            duration
            hasItem
            item {
              title
              type
              season
              episode
            }
          }
        }
      }
    }
  }
}
"""

SLUG_QUERY = """
query DiscBySlugs($media: String!, $release: String!, $disc: String!) {
  mediaItems(where: { slug: { eq: $media } }) {
    nodes {
      title
      year
      type
      externalids { tmdb imdb }
      releases(where: { slug: { eq: $release } }) {
        discs(where: { slug: { eq: $disc } }) {
          format
          titles(order: { index: ASC }) {
            index
            sourceFile
            duration
            hasItem
            item {
              title
              type
              season
              episode
            }
          }
        }
      }
    }
  }
}
"""


class DiscDbClient:
    """Async client for TheDiscDB GraphQL API."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def lookup_disc(self, content_hash: str) -> dict | None:
        """Look up a disc by its content hash.

        Returns a normalized dict with title, year, type, tmdb_id, imdb_id,
        and titles (from the matching disc only), or None on failure/miss.
        """
        session = await self._get_session()

        try:
            async with session.post(
                DISCDB_URL,
                json={
                    "query": DISC_QUERY,
                    "variables": {"hash": content_hash},
                },
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "DiscDB API error: %d", resp.status
                    )
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("DiscDB request failed: %s", e)
            return None

        return _extract_disc(data, content_hash)

    async def lookup_disc_by_url(self, url: str) -> dict | None:
        """Look up a disc by its TheDiscDB URL.

        Parses the URL into slugs and queries the GraphQL API.
        Returns the same normalized dict as lookup_disc, or None.
        """
        parsed = parse_discdb_url(url)
        if parsed is None:
            logger.warning("Invalid DiscDB URL: %s", url)
            return None

        media_slug, release_slug, disc_slug = parsed
        session = await self._get_session()

        try:
            async with session.post(
                DISCDB_URL,
                json={
                    "query": SLUG_QUERY,
                    "variables": {
                        "media": media_slug,
                        "release": release_slug,
                        "disc": disc_slug,
                    },
                },
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "DiscDB API error: %d", resp.status
                    )
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("DiscDB request failed: %s", e)
            return None

        return _extract_disc_from_slug(data)


def parse_discdb_url(url: str) -> tuple[str, str, str] | None:
    """Parse a TheDiscDB URL into (media_slug, release_slug, disc_slug).

    Expected path: /{type}/{media}/releases/{release}/discs/{disc}
    Returns None for invalid URLs.
    """
    # Add scheme if missing so urlparse works
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")

    # Expected: [type, media, "releases", release, "discs", disc]
    if len(parts) != 6:
        return None
    if parts[2] != "releases" or parts[4] != "discs":
        return None

    return (parts[1], parts[3], parts[5])


def _normalize_titles(raw_titles: list[dict]) -> list[dict]:
    """Filter to titles with items and normalize into flat dicts."""
    titles = []
    for t in raw_titles:
        if not t.get("hasItem"):
            continue
        item = t.get("item") or {}
        titles.append({
            "index": t.get("index"),
            "source_file": t.get("sourceFile", ""),
            "duration": t.get("duration"),
            "item_title": item.get("title", ""),
            "item_type": item.get("type", ""),
            "season": item.get("season"),
            "episode": item.get("episode"),
        })
    return titles


def _extract_disc(data: dict, content_hash: str) -> dict | None:
    """Extract and normalize the matching disc from a GraphQL response."""
    nodes = (
        data.get("data", {}).get("mediaItems", {}).get("nodes", [])
    )
    if not nodes:
        return None

    media = nodes[0]
    external = media.get("externalids") or {}

    # Find the specific disc matching our hash
    matching_titles = None
    for release in media.get("releases", []):
        for disc in release.get("discs", []):
            if disc.get("contentHash") == content_hash:
                matching_titles = disc.get("titles", [])
                break
        if matching_titles is not None:
            break

    if matching_titles is None:
        return None

    return {
        "title": media.get("title", ""),
        "year": media.get("year"),
        "type": media.get("type", ""),
        "tmdb_id": external.get("tmdb"),
        "imdb_id": external.get("imdb"),
        "titles": _normalize_titles(matching_titles),
    }


def _extract_disc_from_slug(data: dict) -> dict | None:
    """Extract disc from a slug-based GraphQL response.

    Expects exactly one node, one release, one disc.
    """
    nodes = (
        data.get("data", {}).get("mediaItems", {}).get("nodes", [])
    )
    if not nodes:
        return None

    media = nodes[0]
    external = media.get("externalids") or {}

    releases = media.get("releases", [])
    if not releases:
        return None

    discs = releases[0].get("discs", [])
    if not discs:
        return None

    return {
        "title": media.get("title", ""),
        "year": media.get("year"),
        "type": media.get("type", ""),
        "tmdb_id": external.get("tmdb"),
        "imdb_id": external.get("imdb"),
        "titles": _normalize_titles(discs[0].get("titles", [])),
    }
