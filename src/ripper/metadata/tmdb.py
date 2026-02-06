"""TMDb API client for movie/TV metadata lookup."""

import logging

import aiohttp

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"


class TMDbClient:
    """Async client for The Movie Database API."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        """Search for movies by title.

        Returns list of result dicts with keys: id, title, release_date, overview.
        """
        params: dict[str, str | int] = {"query": query}
        if year:
            params["year"] = year
        return await self._search("search/movie", params, "results")

    async def search_tv(self, query: str) -> list[dict]:
        """Search for TV shows by title.

        Returns list of result dicts with keys: id, name, first_air_date, overview.
        """
        return await self._search("search/tv", {"query": query}, "results")

    async def get_movie_details(self, movie_id: int) -> dict:
        """Get detailed movie info including runtime."""
        return await self._get(f"movie/{movie_id}")

    async def get_tv_details(self, tv_id: int) -> dict:
        """Get detailed TV show info."""
        return await self._get(f"tv/{tv_id}")

    async def get_season_episodes(self, tv_id: int, season_num: int) -> list[dict]:
        """Get episodes for a TV season with runtimes."""
        data = await self._get(f"tv/{tv_id}/season/{season_num}")
        return data.get("episodes", [])

    async def _search(
        self, endpoint: str, params: dict, results_key: str
    ) -> list[dict]:
        """Execute a search query."""
        data = await self._get(endpoint, params)
        return data.get(results_key, [])

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make an authenticated GET request to TMDb."""
        session = await self._get_session()
        url = f"{TMDB_BASE_URL}/{endpoint}"
        request_params = {"api_key": self.api_key}
        if params:
            request_params.update(params)

        try:
            async with session.get(url, params=request_params) as resp:
                if resp.status != 200:
                    logger.error("TMDb API error: %d for %s", resp.status, endpoint)
                    return {}
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error("TMDb request failed: %s", e)
            return {}
