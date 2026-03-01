"""Tests for TheDiscDB content hash computation."""

import hashlib
import struct

from ripper.core.scanner import _compute_content_hash, compute_hash_from_backup


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
