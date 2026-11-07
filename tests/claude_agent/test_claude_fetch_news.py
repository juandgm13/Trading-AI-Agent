"""Tests de claude_agent/fetch_news.py (recogida pública para el informe diario)."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from claude_agent import fetch_news as fn
from src.ingestion import Article
from src.news_sources import RSSNewsSource

NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)  # martes


def _art(i, source="Expansión Mercados", hours_ago=1, title=None, url=None):
    return Article(
        source=source,
        title=title or f"Titular {i}",
        description=f"<p>Resumen <b>{i}</b></p>",
        content="",
        url=url or f"https://example.com/{i}",
        published_at=NOW - timedelta(hours=hours_ago),
        feed_group=source,
    )


CFG = {
    "timezone": "Europe/Madrid",
    "lookback_hours": 24,
    "monday_lookback_hours": 72,
    "max_articles_per_feed": 10,
    "max_articles_per_symbol": 2,
    "summary_max_chars": 50,
    "full_content_top_n": 2,
    "full_content_max_chars": 300,
    "full_content_skip_domains": ["blocked.com"],
    "feeds": {
        "espana": [{"name": "Expansión Mercados", "url": "https://e/rss"}],
        "eeuu": [{"name": "MarketWatch", "url": "https://m/rss"}],
        "macro": [{"name": "Fed", "url": "https://f/rss"}],
        "cripto": [{"name": "CoinDesk", "url": "https://c/rss"}],
    },
    "watchlist": {"espana": [{"symbol": "SAN.MC", "name": "Santander"}], "eeuu": [{"symbol": "AAPL"}]},
    "crypto": {"watchlist": [{"symbol": "BTC-EUR", "name": "Bitcoin"}]},
}


# ── helpers puros ────────────────────────────────────────────────────────────

def test_load_config_reads_real_file():
    cfg = fn.load_config()
    assert cfg["timezone"] == "Europe/Madrid"
    assert cfg["feeds"]["espana"]
    assert cfg["watchlist"]["espana"]


def test_load_config_has_crypto_block():
    cfg = fn.load_config()
    assert cfg["feeds"]["cripto"]
    assert cfg["crypto"]["max_portfolio_pct"] == 25
    assert all(e["symbol"].endswith("-EUR") for e in cfg["crypto"]["watchlist"])


def test_crypto_watchlist_from_config():
    assert fn._crypto_watchlist_from_config(CFG) == [("BTC-EUR", "Bitcoin", "cripto")]
    assert fn._crypto_watchlist_from_config({"crypto": {"watchlist": [{"symbol": "ETH-EUR"}]}}) == [
        ("ETH-EUR", "ETH-EUR", "cripto")]
    assert fn._crypto_watchlist_from_config({}) == []


def test_load_config_empty_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("", encoding="utf-8")
    assert fn.load_config(p) == {}


def test_lookback_hours_monday_and_weekday():
    monday = datetime(2026, 11, 9, 8, 0)
    tuesday = datetime(2026, 11, 10, 8, 0)
    assert fn.lookback_hours(CFG, monday) == 72
    assert fn.lookback_hours(CFG, tuesday) == 24
    assert fn.lookback_hours({}, tuesday) == 24


def test_clean_text_strips_html_and_truncates():
    assert fn.clean_text("<p>Hola <b>mundo</b></p>", 100) == "Hola mundo"
    out = fn.clean_text("a" * 20, 10)
    assert len(out) == 10 and out.endswith("…")
    assert fn.clean_text("", 10) == ""


def test_clean_text_falls_back_on_parser_error():
    with patch.object(fn._TextExtractor, "feed", side_effect=ValueError("boom")):
        assert fn.clean_text("texto  plano", 100) == "texto plano"


def test_article_to_dict_fields():
    d = fn.article_to_dict(_art(1), "espana", 100)
    assert d["group"] == "espana"
    assert d["summary"] == "Resumen 1"
    assert d["published_at"].endswith("+00:00")
    assert set(d) == {"source", "group", "title", "summary", "published_at", "url"}


def test_filter_and_dedup_by_date_url_and_title():
    since = NOW - timedelta(hours=24)
    arts = [
        _art(1),
        _art(2, hours_ago=30),  # demasiado antiguo
        _art(3, url="https://example.com/1"),  # URL duplicada
        _art(4, title="  titular 1 "),  # título duplicado (normalizado)
        _art(5),
    ]
    seen: set[str] = set()
    out = fn.filter_and_dedup(arts, since, seen)
    assert [a.url for a in out] == ["https://example.com/1", "https://example.com/5"]
    assert "https://example.com/1" in seen


def test_collecting_logger_records_warnings_and_errors():
    log = fn.CollectingLogger()
    log.warning("w", source="rss")
    log.error("e", source="rss", exc_info=True)
    log.info("i")
    log.debug("d")
    assert [i["level"] for i in log.issues] == ["warning", "error"]
    assert log.is_debug is False


# ── RSSNewsSource con feeds personalizados ──────────────────────────────────

@pytest.mark.asyncio
async def test_rss_source_custom_feeds_filtered_by_group():
    src = RSSNewsSource(feeds=[("A", "https://a", "espana"), ("B", "https://b", "eeuu")])
    called = []

    async def fake(loop, source, url, group, max_articles=20):
        called.append(source)
        return [_art(source, source=source)]

    with patch.object(src, "_fetch_feed", side_effect=fake):
        arts = await src.fetch(feed_groups=("espana",))
    assert called == ["A"]
    assert [a.source for a in arts] == ["A"]


@pytest.mark.asyncio
async def test_rss_source_default_feeds_unchanged():
    src = RSSNewsSource()
    with patch.object(src, "_fetch_feed", new=AsyncMock(return_value=[])) as m:
        await src.fetch(feed_groups=("stocks", "macro"))
    assert m.await_count == len(RSSNewsSource.FEEDS_STOCKS) + len(RSSNewsSource.FEEDS_MACRO)


# ── collect() ────────────────────────────────────────────────────────────────

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_collect_builds_report_data():
    rss_articles = [
        _art(1, source="Expansión Mercados"),
        _art(2, source="MarketWatch", url="https://blocked.com/x"),
        _art(3, source="Desconocida"),  # fuente no configurada → eeuu
        _art(4, source="Fed", hours_ago=48),  # antigua → sin_noticias_recientes
        _art(5, source="CoinDesk"),
    ]

    async def yahoo_fetch(symbol, max_articles=20):
        if symbol == "AAPL":
            raise RuntimeError("yahoo caído")
        return [_art(f"s{i}", source="Yahoo Finance", url=f"https://y/{i}") for i in range(4)]

    async def content(url, session, logger=None):
        return "x" * 500 if "example.com/1" in url else "corto"

    with patch.object(fn.RSSNewsSource, "fetch", new=AsyncMock(return_value=rss_articles)), \
         patch.object(fn.YahooSymbolSource, "fetch", side_effect=yahoo_fetch), \
         patch.object(fn, "fetch_article_content", side_effect=content), \
         patch.object(fn.aiohttp, "ClientSession", return_value=_FakeSession()):
        data = await fn.collect(CFG, now=NOW)

    assert data["lookback_hours"] == 24
    assert data["counts"]["market"] == {"espana": 1, "europa": 0, "eeuu": 2, "macro": 0, "cripto": 1}
    status = {f["name"]: f["status"] for f in data["feed_status"]}
    assert status == {"Expansión Mercados": "ok", "MarketWatch": "ok", "Fed": "sin_noticias_recientes",
                      "CoinDesk": "ok"}
    assert data["market"]["cripto"][0]["group"] == "cripto"
    assert data["symbols"]["BTC-EUR"]["group"] == "cripto"
    assert data["symbols"]["BTC-EUR"]["name"] == "Bitcoin"
    assert len(data["symbols"]["SAN.MC"]["articles"]) == 2
    assert data["symbols"]["SAN.MC"]["name"] == "Santander"
    assert data["symbols"]["AAPL"] == {"name": "AAPL", "group": "eeuu", "articles": []}
    esp = data["market"]["espana"][0]
    assert len(esp["full_text"]) == 300  # recortado a full_content_max_chars
    assert all("full_text" not in a for a in data["market"]["eeuu"])  # bloqueado o corto
    json.dumps(data)  # serializable


@pytest.mark.asyncio
async def test_collect_handles_rss_failure_and_no_full_content():
    cfg = dict(CFG, full_content_top_n=0)
    with patch.object(fn.RSSNewsSource, "fetch", new=AsyncMock(side_effect=RuntimeError("x"))), \
         patch.object(fn.YahooSymbolSource, "fetch", new=AsyncMock(return_value=[])):
        data = await fn.collect(cfg, now=NOW)
    assert all(f["status"] == "fallo" for f in data["feed_status"])
    assert data["counts"]["symbols"] == 0


@pytest.mark.asyncio
async def test_collect_full_content_disabled_flag():
    with patch.object(fn.RSSNewsSource, "fetch", new=AsyncMock(return_value=[_art(1)])), \
         patch.object(fn.YahooSymbolSource, "fetch", new=AsyncMock(return_value=[])), \
         patch.object(fn, "_add_full_content", new=AsyncMock()) as add:
        await fn.collect(CFG, now=NOW, full_content=False)
    add.assert_not_awaited()


# ── main() ───────────────────────────────────────────────────────────────────

def test_main_writes_json(tmp_path, capsys):
    fake = {
        "generated_at": NOW.isoformat(),
        "generated_at_local": "2026-11-10T08:00:00+01:00",
        "counts": {"market": {"espana": 1}, "symbols": 0},
        "feed_status": [{"name": "X", "group": "espana", "status": "fallo"}],
    }
    out = tmp_path / "n.json"
    with patch.object(fn, "collect", new=AsyncMock(return_value=fake)):
        assert fn.main(["--output", str(out), "--no-full-content"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["counts"]["symbols"] == 0
    assert "Feeds con fallo: X" in capsys.readouterr().out


def test_main_default_output_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fn, "DEFAULT_DATA_DIR", tmp_path)
    fake = {
        "generated_at_local": "2026-11-10T08:00:00+01:00",
        "counts": {"market": {}, "symbols": 0},
        "feed_status": [],
    }
    with patch.object(fn, "collect", new=AsyncMock(return_value=fake)):
        fn.main([])
    assert (tmp_path / "news_2026-11-10.json").exists()
    assert "ninguno" in capsys.readouterr().out
