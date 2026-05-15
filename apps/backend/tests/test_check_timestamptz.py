"""Unit tests for ``scripts/check_timestamptz.py`` (PR #12).

docs/06 §5 Сприннт 1: CI gate that rejects ``TIMESTAMP WITHOUT TIME ZONE``.
The script's own contract — what it accepts / rejects — is what we
pin here. The integration angle ("the existing repo passes") is
covered by simply running the script in CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_timestamptz.py"


def _load() -> object:
    """Import ``scripts/check_timestamptz`` as a module without requiring
    a ``scripts/__init__.py``."""

    spec = importlib.util.spec_from_file_location(
        "check_timestamptz",
        _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_timestamptz"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cm() -> object:
    return _load()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_passes_on_tz_aware_columns(tmp_path: Path, cm: object) -> None:
    f = _write(
        tmp_path,
        "ok_migration.py",
        "import sqlalchemy as sa\n"
        'col = sa.Column("ts", sa.DateTime(timezone=True))\n'
        'op.execute("CREATE TABLE foo (ts TIMESTAMPTZ)")\n'
        'op.execute("ALTER TABLE foo ADD COLUMN ts2 TIMESTAMP WITH TIME ZONE")\n',
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 0


def test_rejects_bare_datetime(tmp_path: Path, cm: object) -> None:
    f = _write(
        tmp_path,
        "bad1.py",
        'col = sa.Column("ts", sa.DateTime())\n',
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 1


def test_rejects_bare_timestamp_keyword(tmp_path: Path, cm: object) -> None:
    f = _write(
        tmp_path,
        "bad2.py",
        'col = sa.Column("ts", sa.TIMESTAMP)\n',
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 1


def test_rejects_raw_sql_timestamp_without_tz(tmp_path: Path, cm: object) -> None:
    f = _write(
        tmp_path,
        "bad3.py",
        'op.execute("CREATE TABLE foo (ts TIMESTAMP)")\n',
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 1


def test_escape_hatch_allows_noqa_lines(tmp_path: Path, cm: object) -> None:
    """``# noqa: timestamptz`` suppresses the check for that line."""

    f = _write(
        tmp_path,
        "ok_with_noqa.py",
        'op.execute("CREATE TABLE foo (ts TIMESTAMP)")  # noqa: timestamptz\n',
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 0


def test_does_not_fire_on_word_timestamp_in_prose(
    tmp_path: Path,
    cm: object,
) -> None:
    """Documentation prose containing the word "timestamp" must not trip
    the SQL-keyword regex (case-sensitive)."""

    f = _write(
        tmp_path,
        "doc.py",
        '"""Email verification timestamp. NULL = unverified."""\n'
        "# capture wall-clock timestamp at request time\n",
    )
    rc = cm.main(["check_timestamptz", str(f)])  # type: ignore[attr-defined]
    assert rc == 0


def test_repo_models_and_migrations_are_clean(cm: object) -> None:
    """Smoke test: the repository itself must satisfy the linter."""

    rc = cm.main(["check_timestamptz"])  # type: ignore[attr-defined]
    assert rc == 0
