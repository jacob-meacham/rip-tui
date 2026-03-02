"""Tests for TheDiscDB content hash computation and GraphQL client."""

import hashlib
import struct

from ripper.core.scanner import _compute_content_hash, compute_hash_from_backup
from ripper.metadata.discdb import (
    _extract_disc_from_slug,
    _normalize_titles,
    parse_discdb_url,
)


class TestComputeContentHash:
    def test_empty_list_returns_empty_string(self):
        assert _compute_content_hash([]) == ""

    def test_single_size(self):
        size = 1234567890
        expected = hashlib.md5(struct.pack("<q", size)).hexdigest().upper()
        assert _compute_content_hash([size]) == expected

    def test_multiple_sizes(self):
        sizes = [100, 200, 300]
        md5 = hashlib.md5()
        for s in sizes:
            md5.update(struct.pack("<q", s))
        expected = md5.hexdigest().upper()
        assert _compute_content_hash(sizes) == expected

    def test_order_matters(self):
        hash_a = _compute_content_hash([100, 200])
        hash_b = _compute_content_hash([200, 100])
        assert hash_a != hash_b

    def test_returns_32_char_uppercase_hex(self):
        result = _compute_content_hash([42])
        assert len(result) == 32
        assert result == result.upper()
        # Verify all chars are valid hex
        int(result, 16)

    def test_large_bluray_sizes(self):
        """Test with realistic Blu-ray M2TS file sizes."""
        sizes = [
            34474836992,  # ~32 GB main feature
            4513218560,   # ~4.2 GB extra
            322122752,    # ~307 MB trailer
        ]
        result = _compute_content_hash(sizes)
        assert len(result) == 32
        assert result == result.upper()


class TestComputeHashFromBackup:
    def test_computes_hash_from_m2ts_files(self, tmp_path):
        stream_dir = tmp_path / "BDMV" / "STREAM"
        stream_dir.mkdir(parents=True)
        # Create M2TS files with known sizes
        (stream_dir / "00000.m2ts").write_bytes(b"\x00" * 1000)
        (stream_dir / "00001.m2ts").write_bytes(b"\x00" * 2000)
        (stream_dir / "00002.m2ts").write_bytes(b"\x00" * 500)

        result = compute_hash_from_backup(tmp_path)

        assert result is not None
        expected = _compute_content_hash([1000, 2000, 500])
        assert result == expected

    def test_files_sorted_alphabetically(self, tmp_path):
        stream_dir = tmp_path / "BDMV" / "STREAM"
        stream_dir.mkdir(parents=True)
        # Create files in reverse order — hash should still be alphabetical
        (stream_dir / "00002.m2ts").write_bytes(b"\x00" * 300)
        (stream_dir / "00000.m2ts").write_bytes(b"\x00" * 100)
        (stream_dir / "00001.m2ts").write_bytes(b"\x00" * 200)

        result = compute_hash_from_backup(tmp_path)

        expected = _compute_content_hash([100, 200, 300])
        assert result == expected

    def test_no_bdmv_directory_returns_none(self, tmp_path):
        result = compute_hash_from_backup(tmp_path)
        assert result is None

    def test_empty_stream_directory_returns_none(self, tmp_path):
        stream_dir = tmp_path / "BDMV" / "STREAM"
        stream_dir.mkdir(parents=True)

        result = compute_hash_from_backup(tmp_path)
        assert result is None

    def test_ignores_non_m2ts_files(self, tmp_path):
        stream_dir = tmp_path / "BDMV" / "STREAM"
        stream_dir.mkdir(parents=True)
        (stream_dir / "00000.m2ts").write_bytes(b"\x00" * 100)
        (stream_dir / "index.bdmv").write_bytes(b"\x00" * 50)
        (stream_dir / "readme.txt").write_bytes(b"hi")

        result = compute_hash_from_backup(tmp_path)

        expected = _compute_content_hash([100])
        assert result == expected


class TestParseDiscdbUrl:
    def test_valid_movie_url(self):
        url = "https://thediscdb.com/movie/my-neighbor-totoro-1988/releases/2020-steelbook-blu-ray/discs/blu-ray"
        result = parse_discdb_url(url)
        assert result == (
            "my-neighbor-totoro-1988",
            "2020-steelbook-blu-ray",
            "blu-ray",
        )

    def test_valid_series_url(self):
        url = "https://thediscdb.com/series/band-of-brothers/releases/2018-blu-ray/discs/disc-1"
        result = parse_discdb_url(url)
        assert result == (
            "band-of-brothers",
            "2018-blu-ray",
            "disc-1",
        )

    def test_without_scheme(self):
        url = "thediscdb.com/movie/totoro/releases/2020/discs/bd"
        result = parse_discdb_url(url)
        assert result == ("totoro", "2020", "bd")

    def test_trailing_slash(self):
        url = "https://thediscdb.com/movie/totoro/releases/2020/discs/bd/"
        result = parse_discdb_url(url)
        assert result == ("totoro", "2020", "bd")

    def test_too_few_segments(self):
        assert parse_discdb_url("https://thediscdb.com/movie/totoro") is None

    def test_too_many_segments(self):
        url = "https://thediscdb.com/movie/totoro/releases/2020/discs/bd/extra"
        assert parse_discdb_url(url) is None

    def test_wrong_structure_no_releases(self):
        url = "https://thediscdb.com/movie/totoro/editions/2020/discs/bd"
        assert parse_discdb_url(url) is None

    def test_wrong_structure_no_discs(self):
        url = "https://thediscdb.com/movie/totoro/releases/2020/tracks/bd"
        assert parse_discdb_url(url) is None


class TestNormalizeTitles:
    def test_filters_no_item(self):
        raw = [
            {"index": 0, "hasItem": True, "sourceFile": "00001.mpls",
             "duration": 7200, "item": {"title": "Main", "type": "MainMovie"}},
            {"index": 1, "hasItem": False, "sourceFile": "00002.mpls",
             "duration": 60, "item": None},
        ]
        result = _normalize_titles(raw)
        assert len(result) == 1
        assert result[0]["item_title"] == "Main"
        assert result[0]["source_file"] == "00001.mpls"

    def test_empty_list(self):
        assert _normalize_titles([]) == []

    def test_missing_item_key(self):
        raw = [{"index": 0, "hasItem": True, "sourceFile": "00001.mpls"}]
        result = _normalize_titles(raw)
        assert len(result) == 1
        assert result[0]["item_title"] == ""
        assert result[0]["item_type"] == ""

    def test_episode_fields(self):
        raw = [
            {"index": 0, "hasItem": True, "sourceFile": "00001.mpls",
             "duration": 2700,
             "item": {"title": "Pilot", "type": "Episode",
                      "season": 1, "episode": 1}},
        ]
        result = _normalize_titles(raw)
        assert result[0]["season"] == 1
        assert result[0]["episode"] == 1


class TestExtractDiscFromSlug:
    def _make_response(self, **overrides):
        """Build a minimal valid slug response."""
        disc = overrides.get("disc", {
            "format": "Blu-ray",
            "titles": [
                {"index": 0, "hasItem": True, "sourceFile": "00001.mpls",
                 "duration": 7200,
                 "item": {"title": "Main", "type": "MainMovie",
                          "season": None, "episode": None}},
            ],
        })
        release = overrides.get("release", {"discs": [disc]})
        media = overrides.get("media", {
            "title": "My Neighbor Totoro",
            "year": 1988,
            "type": "Movie",
            "externalids": {"tmdb": 8392, "imdb": "tt0096283"},
            "releases": [release],
        })
        return {"data": {"mediaItems": {"nodes": [media]}}}

    def test_valid_response(self):
        data = self._make_response()
        result = _extract_disc_from_slug(data)
        assert result is not None
        assert result["title"] == "My Neighbor Totoro"
        assert result["year"] == 1988
        assert result["type"] == "Movie"
        assert result["tmdb_id"] == 8392
        assert result["imdb_id"] == "tt0096283"
        assert len(result["titles"]) == 1
        assert result["titles"][0]["item_title"] == "Main"

    def test_empty_nodes(self):
        data = {"data": {"mediaItems": {"nodes": []}}}
        assert _extract_disc_from_slug(data) is None

    def test_no_releases(self):
        data = self._make_response(
            media={
                "title": "X", "year": 2020, "type": "Movie",
                "externalids": {}, "releases": [],
            }
        )
        assert _extract_disc_from_slug(data) is None

    def test_no_discs(self):
        data = self._make_response(release={"discs": []})
        assert _extract_disc_from_slug(data) is None

    def test_filters_has_item(self):
        disc = {
            "format": "Blu-ray",
            "titles": [
                {"index": 0, "hasItem": True, "sourceFile": "00001.mpls",
                 "duration": 7200,
                 "item": {"title": "Main", "type": "MainMovie"}},
                {"index": 1, "hasItem": False, "sourceFile": "00002.mpls",
                 "duration": 60, "item": None},
            ],
        }
        data = self._make_response(disc=disc)
        result = _extract_disc_from_slug(data)
        assert len(result["titles"]) == 1
