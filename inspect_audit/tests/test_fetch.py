"""Log sources an audit can be pointed at, resolved before anything reads them."""

import urllib.request
from pathlib import Path

import pytest

from inspect_audit._registry import fetch_logs


def _serve(monkeypatch, pages: dict[str, bytes]) -> None:
    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc):  # noqa: ANN002
            return False

    def urlopen(url, timeout=None):  # noqa: ANN001
        if url not in pages:
            raise ValueError(f"unexpected fetch {url!r}")
        return Response(pages[url])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_paths_and_native_urls_pass_through() -> None:
    assert fetch_logs("logs/") == "logs/"
    assert fetch_logs("s3://bucket/prefix") == "s3://bucket/prefix"


def test_a_csv_manifest_downloads_every_listed_log(monkeypatch) -> None:  # noqa: ANN001
    _serve(
        monkeypatch,
        {
            "https://x/manifest.csv": b"model,logs\na,https://x/l/one.eval\nb,https://x/l/two.eval\n",
            "https://x/l/one.eval": b"ONE",
            "https://x/l/two.eval": b"TWO",
        },
    )
    out = Path(fetch_logs("https://x/manifest.csv"))
    assert sorted(p.name for p in out.iterdir()) == ["one.eval", "two.eval"]
    assert (out / "one.eval").read_bytes() == b"ONE"


def test_a_plain_manifest_and_a_single_log_url(monkeypatch) -> None:  # noqa: ANN001
    _serve(
        monkeypatch,
        {
            "https://x/list.txt": b"# comment\nhttps://x/l/a.eval\n\nhttps://x/l/b.eval?X-Amz=sig\n",
            "https://x/l/a.eval": b"A",
            "https://x/l/b.eval?X-Amz=sig": b"B",
            "https://x/solo.eval": b"S",
        },
    )
    out = Path(fetch_logs("https://x/list.txt"))
    assert sorted(p.name for p in out.iterdir()) == ["a.eval", "b.eval"]
    solo = Path(fetch_logs("https://x/solo.eval"))
    assert [p.name for p in solo.iterdir()] == ["solo.eval"]


def test_a_manifest_without_a_log_column_is_refused(monkeypatch) -> None:  # noqa: ANN001
    _serve(monkeypatch, {"https://x/m.csv": b"model,score\na,1\n"})
    with pytest.raises(ValueError, match="column"):
        fetch_logs("https://x/m.csv")
