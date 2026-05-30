from __future__ import annotations

from lcloud.config import Settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.lc_host == "127.0.0.1"
    assert s.lc_port == 8787
    assert s.lc_max_workers == 10
    assert s.lc_max_file_bytes == 1024 * 1024 * 1024
    assert s.lc_session_ttl_seconds == 7 * 24 * 3600


def test_settings_paths(tmp_path) -> None:
    s = Settings(_env_file=None, lc_data_dir=tmp_path / "d")
    s.ensure_runtime_dirs()
    assert (tmp_path / "d").is_dir()
    assert (tmp_path / "d" / "keys").is_dir()
    assert (tmp_path / "d" / "tmp").is_dir()
