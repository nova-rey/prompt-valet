from __future__ import annotations

from datetime import datetime, timedelta

from prompt_valet.ui.app import (
    _format_relative_age,
    _format_timestamp_label,
    _parse_iso_timestamp,
)


def test_parse_iso_timestamp_z_suffix() -> None:
    value = _parse_iso_timestamp("2025-01-01T00:00:00Z")
    assert value == datetime(2025, 1, 1, 0, 0, 0)


def test_parse_iso_timestamp_offset() -> None:
    value = _parse_iso_timestamp("2025-01-01T01:00:00+01:00")
    assert value == datetime(2025, 1, 1, 0, 0, 0)


def test_format_timestamp_label_with_value() -> None:
    assert (
        _format_timestamp_label("Created", "2025-01-01T00:00:00Z")
        == "Created: 2025-01-01 00:00:00 UTC"
    )
    assert _format_timestamp_label("Started", None) is None


def test_format_relative_age_variants() -> None:
    assert _format_relative_age(timedelta(seconds=45)) == "45s"
    assert _format_relative_age(timedelta(seconds=125)) == "2m"
    assert _format_relative_age(timedelta(hours=3, minutes=10)) == "3h"
    assert _format_relative_age(timedelta(days=2, hours=5)) == "2d"
