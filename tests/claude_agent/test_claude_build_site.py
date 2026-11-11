"""Tests de claude_agent/build_site.py (web del informe en docs/ para GitHub Pages)."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from claude_agent import build_site as bs

NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)

REPORT = """---
fecha: 2026-11-09
termometro: prudencia
resumen: "La Fed marca el tono <hoy>"
leccion: "Qué es un ETF"
recomendaciones:
  - {activo: "SPY", accion: "Mantener", confianza: "media"}
  - {activo: "BTC", accion: "Comprar más"}
  - {activo: "", accion: "Vender"}
  - "no es un dict"
---
# 📈 Informe pre-apertura — Lunes 9 de noviembre de 2026

> **Termómetro del día:** 🟡 prudencia — texto.
> **Lección de hoy:** Qué es un ETF

## 2. Recomendaciones del día
| Activo | Acción | Cuánto | Día |
|---|---|---|---|
| SPY | 🔵 Mantener | 3 % en 2 tramos | −0,6% |
| BTC | 🟢 Comprar más | 2 % | +2,3% |
| X | ⛔ Evitar | — | 0% |

## 6. Ideas
### ETF del S&P 500 (SPY) — Mantener
![SPY](../../docs/informes/charts/2026-11-09/SPY.png)

- **🧑‍💼 Recomendación: Reducir.** Porque sí.
"""

OLD = """# 📈 Informe pre-apertura — Sábado 7 de noviembre de 2026

> **Termómetro del día:** 🔴 mucha incertidumbre — Semana dura.
> **Lección de hoy:** Tipos de interés

## 1. Hoy
Texto.
"""


def _write(tmp_path: Path) -> tuple[Path, Path, Path]:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-11-09.md").write_text(REPORT, encoding="utf-8")
    (reports / "2026-11-07.md").write_text(OLD, encoding="utf-8")
    (reports / "notas.md").write_text("no es un informe", encoding="utf-8")
    log = tmp_path / "learning_log.md"
    log.write_text("# Registro\n\n| Fecha | Concepto | Ejemplo |\n|---|---|---|\n"
                   "| 2026-11-07 | Tipos | Fed |\n| 2026-10-01 | Antiguo | x |\n", encoding="utf-8")
    return reports, tmp_path / "docs", log


# ── front matter ─────────────────────────────────────────────────────────────

def test_split_front_matter_ok_missing_invalid_and_not_dict():
    meta, body, issues = bs.split_front_matter(REPORT)
    assert meta["termometro"] == "prudencia" and body.startswith("# 📈") and issues == []
    assert bs.split_front_matter("# Sin\n") == ({}, "# Sin\n", ["sin front matter"])
    meta, body, issues = bs.split_front_matter("---\nclave: [sin cerrar\n---\n# X\n")
    assert meta == {} and body == "# X\n" and "inválido" in issues[0]
    assert bs.split_front_matter("---\n- lista\n---\n# X\n")[2] == ["front matter no es un diccionario"]
    assert bs.split_front_matter("---\n\n---\n# X\n")[0] == {}


def test_infer_meta_from_header():
    meta = bs.infer_meta(OLD)
    assert meta == {"termometro": "incertidumbre", "resumen": "Semana dura.", "leccion": "Tipos de interés"}
    assert bs.infer_meta("nada") == {}


def test_report_properties():
    r = bs.Report(date(2026, 11, 9), "2026-11-09", "", meta=bs.split_front_matter(REPORT)[0])
    assert r.thermo == "prudencia" and r.lesson == "Qué es un ETF"
    assert [x["activo"] for x in r.recommendations] == ["SPY", "BTC"]
    assert bs.Report(date(2026, 1, 1), "x", "", meta={"termometro": "raro"}).thermo == "desconocido"
    empty = bs.Report(date(2026, 1, 1), "x", "")
    assert empty.summary == "" and empty.recommendations == []


def test_load_reports_sorted_and_filtered(tmp_path):
    reports, _, _ = _write(tmp_path)
    loaded = bs.load_reports(reports)
    assert [r.slug for r in loaded] == ["2026-11-09", "2026-11-07"]
    assert loaded[1].issues == ["sin front matter"] and loaded[1].thermo == "incertidumbre"


# ── conversión ───────────────────────────────────────────────────────────────

def test_long_date():
    assert bs.long_date(date(2026, 11, 9)) == "Lunes 9 de noviembre de 2026"


@pytest.mark.parametrize("action, css", [
    ("Comprar más", "act-comprar-mas"), ("comprar", "act-comprar"), ("Mantener", "act-mantener"),
    ("Reducir", "act-reducir"), ("Vender", "act-vender"), ("Esperar", "act-esperar"), ("Evitar", "act-evitar"),
])
def test_action_badge(action, css):
    assert f'class="badge {css}"' in bs.action_badge(action)


def test_action_badge_unknown_is_escaped():
    assert bs.action_badge("<b>Rara</b>") == '<span class="badge">&lt;b&gt;Rara&lt;/b&gt;</span>'


def test_thermo_pill():
    assert "thermo-prudencia" in bs.thermo_pill("prudencia") and "Prudencia" in bs.thermo_pill("prudencia")
    assert "Sin termómetro" in bs.thermo_pill("desconocido")


def test_render_markdown_postprocess():
    _, body, _ = bs.split_front_matter(REPORT)
    html, toc = bs.render_markdown(body)
    assert 'src="charts/2026-11-09/SPY.png"' in html and 'loading="lazy"' in html
    assert '<div class="table-wrap"><table>' in html and "</table></div>" in html
    assert '<span class="badge act-mantener">Mantener</span>' in html
    assert '<span class="badge act-comprar-mas">Comprar más</span>' in html
    assert '<span class="badge act-evitar">Evitar</span>' in html
    assert '<span class="neg">−0,6%</span>' in html and '<span class="pos">+2,3%</span>' in html
    assert "<td>0%</td>" in html                       # cero: sin color
    assert '<span class="pos">3 %' not in html         # tamaños de posición: sin color
    assert "<h3" in html and "<span>ETF del S&amp;P 500 (SPY)</span>" in html
    assert "<strong>Recomendación:</strong> <span class=\"badge act-reducir\">Reducir</span>" in html
    assert "<br" in html                               # líneas de la cabecera separadas
    assert "Recomendaciones del día" in toc and "<ul>" in toc


# ── páginas ──────────────────────────────────────────────────────────────────

def test_render_index_and_archive_empty():
    assert "Todavía no hay informes" in bs.render_index([])
    assert "Todavía no hay informes" in bs.render_archive([])


def test_reco_list_empty_and_with_confidence(tmp_path):
    reports, _, _ = _write(tmp_path)
    latest, old = bs.load_reports(reports)
    html = bs.reco_list(latest)
    assert "confianza media" in html and html.count("<li>") == 2
    assert "no incluye recomendaciones" in bs.reco_list(old)


def test_build_full_site(tmp_path):
    reports, docs, log = _write(tmp_path)
    (docs / "informes").mkdir(parents=True)
    (docs / "informes" / "2020-01-01.html").write_text("viejo", encoding="utf-8")   # informe borrado
    (docs / "informes" / "charts").mkdir()
    cfg = {"timezone": "Europe/Madrid", "site": {"title": "Mi informe", "base_url": "https://x.github.io/repo/"}}
    result = bs.build(cfg, reports_dir=reports, docs_dir=docs, learning_log=log, now=NOW)

    assert result["reports"] == 2 and result["latest"] == "2026-11-09"
    assert result["url"] == "https://x.github.io/repo/informes/2026-11-09.html"
    assert result["issues"] == {"2026-11-07": ["sin front matter"]}
    assert result["pages"] == ["aprende.html", "archivo.html", "historial.html", "index.html",
                               "informes/2026-11-07.html", "informes/2026-11-09.html"]
    assert not (docs / "informes" / "2020-01-01.html").exists() and (docs / "informes" / "charts").exists()
    assert (docs / ".nojekyll").exists() and (docs / "assets" / "style.css").exists()
    assert (docs / "assets" / "site.js").exists()

    index = (docs / "index.html").read_text(encoding="utf-8")
    assert "Mi informe" in index and "La Fed marca el tono &lt;hoy&gt;" in index
    assert 'href="informes/2026-11-09.html"' in index and "Informes anteriores" in index
    assert 'class="active"' in index and "2026-11-10 08:00" in index  # hora de Madrid
    report = (docs / "informes" / "2026-11-09.html").read_text(encoding="utf-8")
    assert 'href="../assets/style.css' in report and "← Anterior" in report and "Siguiente" not in report
    old = (docs / "informes" / "2026-11-07.html").read_text(encoding="utf-8")
    assert "Siguiente →" in old and "thermo-incertidumbre" in old
    archive = (docs / "archivo.html").read_text(encoding="utf-8")
    assert "Noviembre 2026" in archive and "2 informes" in archive
    history = (docs / "historial.html").read_text(encoding="utf-8")
    assert "Historial de recomendaciones" in history and 'class="active"' in history
    learn = (docs / "aprende.html").read_text(encoding="utf-8")
    assert '<a href="informes/2026-11-07.html">2026-11-07</a>' in learn and "<td>2026-10-01</td>" in learn


def test_build_without_reports_log_or_base_url(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    result = bs.build({}, reports_dir=reports, docs_dir=tmp_path / "docs", learning_log=tmp_path / "nope.md")
    assert result["reports"] == 0 and result["latest"] is None and result["url"] is None
    assert "Todavía no hay informes" in (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")


def test_real_reports_build(tmp_path):
    """Los informes reales del repositorio se convierten sin errores."""
    result = bs.build(bs.load_config(bs.DEFAULT_CONFIG), docs_dir=tmp_path / "docs")
    assert result["reports"] >= 1 and result["issues"] == {}


def test_main(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(bs, "REPORTS_DIR", _write(tmp_path)[0])
    monkeypatch.setattr(bs, "build", lambda cfg, docs_dir: {
        "reports": 2, "pages": ["a", "b"], "url": "https://x/informes/1.html", "issues": {"2026-11-07": ["mal"]}})
    assert bs.main(["--docs-dir", str(tmp_path / "d")]) == 0
    out = capsys.readouterr().out
    assert "2 informes, 2 páginas" in out and "https://x/informes/1.html" in out and "2026-11-07: mal" in out
    monkeypatch.setattr(bs, "build", lambda cfg, docs_dir: {"reports": 0, "pages": [], "url": None, "issues": {}})
    assert bs.main([]) == 0


def test_uppercase_action_headings_with_note():
    html, _ = bs.render_markdown("### Inditex (ITX.MC) — COMPRAR (pequeño, en 2 tramos)\n\n### Ether (ETH) — ESPERAR\n")
    assert '<span class="badge act-comprar">Comprar</span><span class="badge-note">(pequeño, en 2 tramos)</span>' in html
    assert '<span class="badge act-esperar">Esperar</span>' in html
    assert bs.action_badge("COMPRAR MÁS") == '<span class="badge act-comprar-mas">Comprar más</span>'


# ── gráfico fijo en las ideas ────────────────────────────────────────────────

def test_wrap_chart_sections_wraps_until_next_heading():
    html = ('<h3 id="a"><span>SPY</span><span class="badge act-comprar">Comprar</span></h3>\n'
            '<p><img loading="lazy" alt="SPY" src="charts/x/SPY.png"></p>\n<ul><li>texto A</li></ul>\n'
            '<h3 id="b">IBE</h3>\n<p><img alt="IBE" src="charts/x/IBE.png"></p>\n<p>texto B</p>\n'
            '<h3 id="c">Sin gráfico</h3>\n<p>texto C</p>\n'
            '<h2 id="d">Cripto</h2>\n')
    out = bs.wrap_chart_sections(html)
    assert out.count('<section class="idea">') == 2
    assert ('<figure class="idea-chart"><img loading="lazy" alt="SPY" src="charts/x/SPY.png"></figure>\n'
            '<div class="idea-body"><ul><li>texto A</li></ul></div></section>') in out
    assert '<div class="idea-body"><p>texto B</p></div></section>' in out
    assert '<span class="badge act-comprar">Comprar</span></h3>' in out      # insignia intacta
    assert '<h3 id="c">Sin gráfico</h3>\n<p>texto C</p>' in out              # sin gráfico: igual
    assert out.endswith('<h2 id="d">Cripto</h2>\n')


def test_wrap_chart_sections_stops_at_hr_and_end():
    html = '<h3>X</h3><p><img src="a.png"></p><p>t</p><hr>\n<p>fin</p>'
    out = bs.wrap_chart_sections(html)
    assert '<div class="idea-body"><p>t</p></div></section>\n<hr>' in out
    tail = bs.wrap_chart_sections('<h3>Y</h3><p><img src="b.png"></p><p>u</p>')
    assert tail.endswith('<div class="idea-body"><p>u</p></div></section>\n')


def test_render_markdown_wraps_idea_charts():
    _, body, _ = bs.split_front_matter(REPORT)
    html, _ = bs.render_markdown(body)
    assert '<figure class="idea-chart"><img loading="lazy"' in html


# ── continuidad entre informes ───────────────────────────────────────────────

def _rep(slug, recos):
    return bs.Report(date=date.fromisoformat(slug), slug=slug, body_md="",
                     meta={"recomendaciones": [{"activo": a, "accion": x} for a, x in recos]})


@pytest.mark.parametrize("name, key", [
    ("Iberdrola (IBE.MC)", "IBE.MC"),
    ("ETF S&P 500 (SPY / SPY5.L)", "SPY"),
    ("ETF S&P 500 (SPY o equivalente UCITS)", "SPY"),
    ("Bitcoin (btc)", "BTC"),
    ("Bitcoin (BTC-EUR)", "BTC"),
    ("  Oro   físico ", "oro físico"),
    ("Raro ( )", "raro ( )"),
])
def test_asset_key(name, key):
    assert bs.asset_key(name) == key


def test_reco_list_marks_changes_vs_previous():
    today = _rep("2026-11-11", [("Airbus (AIR.PA)", "Esperar"), ("Bitcoin (BTC)", "COMPRAR (poco)"),
                                ("Nvidia (NVDA)", "Evitar")])
    before = _rep("2026-11-10", [("Airbus (AIR.PA)", "Comprar"), ("Bitcoin (BTC-EUR)", "Comprar")])
    html = bs.reco_list(today, before)
    assert "antes: Comprar" in html and html.count("reco-change") == 2 and "reco-change new" in html
    assert "reco-change" not in bs.reco_list(today)            # sin informe anterior: sin marcas
    assert bs.previous_actions(None) == {}
    assert bs.previous_actions(before) == {"AIR.PA": "Comprar", "BTC": "Comprar"}
    assert bs._action_label("Algo raro") == "Algo raro"


def test_render_history_table():
    r3 = _rep("2026-11-11", [("Airbus (AIR.PA)", "Comprar"), ("Bitcoin (BTC)", "Comprar")])
    r2 = bs.Report(date=date(2026, 11, 10), slug="2026-11-10", body_md="")          # sin recomendaciones
    r1 = _rep("2026-11-09", [("Airbus (AIR.PA)", "Esperar"), ("Iberdrola (IBE.MC)", "Esperar")])
    html = bs.render_history([r3, r2, r1])
    assert html.count("<th><a href=") == 2                     # el informe sin recomendaciones no es columna
    assert html.index("Airbus") < html.index("Bitcoin") < html.index("Iberdrola")  # primero los de hoy
    assert html.count('class="changed"') == 1                  # Airbus: Esperar → Comprar
    assert '<td class="muted">—</td>' in html
    assert "Todavía no hay recomendaciones" in bs.render_history([r2])
    assert bs.render_history([r3, r1], limit=1).count("<th><a href=") == 1
