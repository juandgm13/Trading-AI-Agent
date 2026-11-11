"""
Genera el sitio web estático del informe diario en `docs/` (GitHub Pages, rama Trading-Agent_Claude).

Fuente: los informes Markdown de `claude_agent/reports/AAAA-MM-DD.md` (con front matter YAML) y
`claude_agent/learning_log.md`. Salida (se regenera entera en cada ejecución, es idempotente):

    docs/index.html                portada: último informe, recomendaciones, informes recientes
    docs/informes/AAAA-MM-DD.html  informe completo con índice lateral y navegación
    docs/archivo.html              todos los informes por mes
    docs/aprende.html              temario y conceptos explicados (enlazados a su informe)
    docs/historial.html            recomendaciones por activo a lo largo de los informes (cambios marcados)
    docs/assets/style.css, site.js y docs/.nojekyll (HTML propio, sin Jekyll)

Los gráficos ya están en `docs/informes/charts/FECHA/` (los genera fetch_prices/fetch_crypto); en el Markdown se
enlazan como `../../docs/informes/charts/...` para verse también en GitHub, y aquí se reescriben a `charts/...`.

Uso:
    python claude_agent/build_site.py
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

import markdown
import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import DEFAULT_CONFIG, load_config  # noqa: E402

REPORTS_DIR = _HERE / "reports"
SITE_SRC = _HERE / "site"
DOCS_DIR = _HERE.parent / "docs"
LEARNING_LOG = _HERE / "learning_log.md"
RECENT = 7
HISTORY_REPORTS = 10   # columnas (informes) de historial.html
DISCLAIMER = ("Información general generada automáticamente por una IA a partir de noticias y datos públicos. "
              "No es asesoramiento financiero personalizado ni regulado. Invertir conlleva riesgo de pérdida, "
              "muy alto en criptomonedas.")

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]
THERMO = {
    "tranquilo": "Tranquilo",
    "prudencia": "Prudencia",
    "incertidumbre": "Mucha incertidumbre",
}
ACTIONS = {  # orden: las frases largas primero
    "Comprar más": "comprar-mas",
    "Comprar": "comprar",
    "Mantener": "mantener",
    "Reducir": "reducir",
    "Vender": "vender",
    "Esperar": "esperar",
    "Evitar": "evitar",
}
_ACTION_RE = "|".join(re.escape(a) for a in ACTIONS)
_EMOJI = r"(?:[\U0001F7E0-\U0001F7EB\u23F8\u26D4\u2705\U0001F534\U0001F535]\uFE0F?\s*)?"
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Report:
    date: date
    slug: str
    body_md: str
    meta: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def thermo(self) -> str:
        value = str(self.meta.get("termometro") or "").strip().lower()
        return value if value in THERMO else "desconocido"

    @property
    def summary(self) -> str:
        return str(self.meta.get("resumen") or "").strip()

    @property
    def lesson(self) -> str:
        return str(self.meta.get("leccion") or "").strip()

    @property
    def recommendations(self) -> list[dict]:
        recos = self.meta.get("recomendaciones") or []
        return [r for r in recos if isinstance(r, dict) and r.get("activo") and r.get("accion")]


# ── Lectura ──────────────────────────────────────────────────────────────────

def split_front_matter(text: str) -> tuple[dict, str, list[str]]:
    """Separa el bloque YAML inicial. Si falta o es inválido, devuelve {} y un aviso (el informe se publica igual)."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text, ["sin front matter"]
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return {}, text[match.end():], [f"front matter inválido: {exc}"]
    if not isinstance(meta, dict):
        return {}, text[match.end():], ["front matter no es un diccionario"]
    return meta, text[match.end():], []


def infer_meta(body: str) -> dict:
    """Respaldo para informes sin front matter: termómetro y lección desde la cabecera del Markdown."""
    meta: dict = {}
    thermo = re.search(r"Term[oó]metro del d[ií]a:\*\*\s*\S*\s*(tranquilo|prudencia|mucha incertidumbre)\s*[—-]\s*(.+)",
                       body, re.I)
    if thermo:
        meta["termometro"] = "incertidumbre" if "incertidumbre" in thermo.group(1).lower() else thermo.group(1).lower()
        meta["resumen"] = thermo.group(2).strip()
    lesson = re.search(r"Lecci[oó]n de hoy:\*\*\s*(.+)", body)
    if lesson:
        meta["leccion"] = lesson.group(1).strip()
    return meta


def load_reports(reports_dir: Path = REPORTS_DIR) -> list[Report]:
    """Informes ordenados del más reciente al más antiguo (solo archivos AAAA-MM-DD.md)."""
    reports = []
    for path in reports_dir.glob("*.md"):
        if not _DATE_FILE.match(path.stem):
            continue
        meta, body, issues = split_front_matter(path.read_text(encoding="utf-8"))
        if not meta:
            meta = infer_meta(body)
        reports.append(Report(date=date.fromisoformat(path.stem), slug=path.stem, body_md=body, meta=meta,
                              issues=issues))
    return sorted(reports, key=lambda r: r.date, reverse=True)


# ── Conversión y post-proceso ────────────────────────────────────────────────

def long_date(d: date) -> str:
    return f"{DAYS[d.weekday()].capitalize()} {d.day} de {MONTHS[d.month - 1]} de {d.year}"


def action_badge(action: str) -> str:
    """Insignia de color; el texto se normaliza ("COMPRAR MÁS" → "Comprar más")."""
    text = action.strip()
    for label, key in ACTIONS.items():
        if text.lower().startswith(label.lower()):
            return f'<span class="badge act-{key}">{label}{html.escape(text[len(label):])}</span>'
    return f'<span class="badge">{html.escape(text)}</span>'


def thermo_pill(thermo: str) -> str:
    label = THERMO.get(thermo, "Sin termómetro")
    return f'<span class="pill thermo-{thermo}">{label}</span>'


def _color_percent(match: re.Match) -> str:
    text = match.group(0)
    sign = match.group(1)
    if sign in ("-", "−"):
        return f'<span class="neg">{text}</span>'
    if sign == "+" or float(match.group(2).replace(",", ".")) > 0:
        return f'<span class="pos">{text}</span>'
    return text


_PERCENT = re.compile(r"(?<![\w.])([+−-]?)(\d+(?:[.,]\d+)?)\s?%")


def postprocess(body_html: str) -> str:
    """Rutas de gráficos, tablas con scroll, insignias de acción y colores de porcentajes."""
    out = re.sub(r'src="(?:\.\./)*docs/informes/charts/', 'src="charts/', body_html)
    out = re.sub(r'<img ([^>]*?)/?>', lambda m: f'<img loading="lazy" {m.group(1).strip()}>', out)
    out = out.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")

    def cell(m: re.Match) -> str:
        content = m.group(2)
        badge = re.fullmatch(rf"\s*{_EMOJI}({_ACTION_RE})\b(.*)", content, re.S)
        if badge:
            rest = badge.group(2)
            content = action_badge(badge.group(1)) + rest
        elif _PERCENT.fullmatch(content.strip()):  # solo celdas que son una variación (no "3 % en 2 tramos")
            content = _PERCENT.sub(_color_percent, content)
        return f"{m.group(1)}{content}</td>"

    out = re.sub(r"(<td[^>]*>)(.*?)</td>", cell, out, flags=re.S)
    out = re.sub(  # "### Empresa (TICKER) — Comprar" · "— COMPRAR (en tramos)"
        rf"(<h3[^>]*>)(.*?)\s[—-]\s{_EMOJI}({_ACTION_RE})\b\.?\s*(\([^<]*\))?\s*(</h3>)",
        lambda m: (f"{m.group(1)}<span>{m.group(2)}</span>{action_badge(m.group(3))}"
                   + (f'<span class="badge-note">{m.group(4)}</span>' if m.group(4) else "") + m.group(5)),
        out,
        flags=re.I,
    )
    out = re.sub(
        rf"<strong>(🧑‍💼\s*)?Recomendación:\s*({_ACTION_RE})\.?</strong>",
        lambda m: f"<strong>Recomendación:</strong> {action_badge(m.group(2))}",
        out,
    )
    return wrap_chart_sections(out)


_CHART_SECTION = re.compile(
    r"(<h3[^>]*>.*?</h3>)\s*<p>\s*(<img [^>]*>)\s*</p>(.*?)(?=<h[1-3][\s>]|<hr\s*/?>|\Z)", re.S
)


def wrap_chart_sections(body_html: str) -> str:
    """Envuelve cada idea `### …` cuyo primer elemento es un gráfico para que el gráfico quede fijo arriba
    (CSS `position: sticky`) mientras se lee su texto; al terminar la idea, el gráfico se va con ella."""
    return _CHART_SECTION.sub(
        lambda m: (f'<section class="idea">{m.group(1)}\n<figure class="idea-chart">{m.group(2)}</figure>\n'
                   f'<div class="idea-body">{m.group(3).strip()}</div></section>\n'),
        body_html,
    )


def render_markdown(text: str) -> tuple[str, str]:
    """(HTML del cuerpo, HTML del índice) — el índice usa los encabezados ## y ###."""
    md = markdown.Markdown(
        extensions=["tables", "toc", "attr_list", "sane_lists", "fenced_code", "nl2br"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    body = md.convert(text)
    return postprocess(body), md.toc


# ── Páginas ──────────────────────────────────────────────────────────────────

def _page(base: Template, *, title: str, description: str, content: str, root: str, active: str,
          site: dict, generated: str, body_class: str = "") -> str:
    return base.substitute(
        title=html.escape(title),
        description=html.escape(description),
        content=content,
        root=root,
        site_title=html.escape(site.get("title", "Informe pre-apertura")),
        nav_index="active" if active == "index" else "",
        nav_archive="active" if active == "archive" else "",
        nav_learn="active" if active == "learn" else "",
        nav_history="active" if active == "history" else "",
        body_class=body_class,
        disclaimer=html.escape(DISCLAIMER),
        generated=generated,
        version=generated.replace(" ", "").replace(":", ""),
    )


def asset_key(name: str) -> str:
    """Clave estable de un activo: el ticker entre paréntesis ("Iberdrola (IBE.MC)" → "IBE.MC"); si no hay, el nombre."""
    m = re.search(r"\(([^)]+)\)", name)
    if m:
        token = re.split(r"[\s/,;]+", m.group(1).strip())[0]
        if token:  # los pares cripto cuentan como la moneda: "BTC-EUR" → "BTC"
            return re.sub(r"-(EUR|USD|USDC|USDT)$", "", token.upper())
    return re.sub(r"\s+", " ", name.strip().lower())


def _action_label(action: str) -> str:
    text = action.strip()
    for label in ACTIONS:
        if text.lower().startswith(label.lower()):
            return label
    return text


def previous_actions(report: Report | None) -> dict[str, str]:
    return {asset_key(str(r["activo"])): _action_label(str(r["accion"])) for r in report.recommendations} if report else {}


def reco_list(report: Report, previous: Report | None = None) -> str:
    recos = report.recommendations
    if not recos:
        return '<p class="reco-empty">Este informe no incluye recomendaciones con acción.</p>'
    before = previous_actions(previous)
    items = []
    for r in recos:
        conf = f'<span class="conf">confianza {html.escape(str(r["confianza"]))}</span>' if r.get("confianza") else ""
        change = ""
        if previous is not None:
            old = before.get(asset_key(str(r["activo"])))
            if old is None:
                change = '<span class="reco-change new">nueva</span>'
            elif old != _action_label(str(r["accion"])):
                change = f'<span class="reco-change">antes: {html.escape(old)}</span>'
        items.append(
            f'<li><span class="reco-asset">{html.escape(str(r["activo"]))}{change}</span>'
            f'<span>{action_badge(str(r["accion"]))} {conf}</span></li>'
        )
    return f'<ul class="reco-list">{"".join(items)}</ul>'


def render_history(reports: list[Report], limit: int = HISTORY_REPORTS) -> str:
    """Tabla activo × informe (más reciente a la izquierda); marca los cambios de acción respecto al informe anterior."""
    shown = [r for r in reports if r.recommendations][:limit]
    if not shown:
        return '<h1>Historial de recomendaciones</h1><p class="muted">Todavía no hay recomendaciones.</p>'
    names: dict[str, str] = {}
    cells: dict[str, dict[str, str]] = {}
    for r in reversed(shown):  # del más antiguo al más reciente: el nombre visible es el último usado
        for reco in r.recommendations:
            key = asset_key(str(reco["activo"]))
            names[key] = str(reco["activo"])
            cells.setdefault(key, {})[r.slug] = str(reco["accion"])
    order = sorted(names, key=lambda k: (shown[0].slug not in cells[k], names[k].lower()))
    head = "".join(f'<th><a href="informes/{r.slug}.html">{r.date.day}/{r.date.month}</a></th>' for r in shown)
    rows = []
    for key in order:
        tds = []
        for i, r in enumerate(shown):
            action = cells[key].get(r.slug)
            if action is None:
                tds.append('<td class="muted">—</td>')
                continue
            older = next((cells[key].get(o.slug) for o in shown[i + 1:] if o.slug in cells[key]), None)
            changed = older is not None and _action_label(older) != _action_label(action)
            tds.append(f'<td class="{"changed" if changed else ""}">{action_badge(action)}</td>')
        rows.append(f'<tr><th scope="row">{html.escape(names[key])}</th>{"".join(tds)}</tr>')
    return (
        '<p class="eyebrow">Coherencia entre informes</p><h1>Historial de recomendaciones</h1>'
        '<p class="muted">Cada columna es un informe (el más reciente a la izquierda). '
        'Las casillas con borde marcan un cambio de acción respecto al informe anterior: el informe de ese día explica por qué.</p>'
        f'<div class="card table-wrap history"><table><thead><tr><th>Activo</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def report_card(report: Report, root: str) -> str:
    return (
        f'<a class="card report-card" href="{root}informes/{report.slug}.html">'
        f'<span class="date">{long_date(report.date)}</span>{thermo_pill(report.thermo)}'
        f'<span class="summary">{html.escape(report.summary)}</span></a>'
    )


def render_index(reports: list[Report]) -> str:
    if not reports:
        return ('<section class="card hero"><div><p class="eyebrow">Informe pre-apertura</p>'
                '<h1>Todavía no hay informes</h1><p class="summary">El primero aparecerá aquí tras la próxima '
                'ejecución de la tarea diaria.</p></div></section>')
    latest = reports[0]
    lesson = f'<span class="muted">🎓 {html.escape(latest.lesson)}</span>' if latest.lesson else ""
    recent = "".join(report_card(r, "") for r in reports[1:RECENT + 1])
    recent_block = f'<h2 class="section-title">Informes anteriores</h2><div class="grid">{recent}</div>' if recent else ""
    return f"""
<section class="card hero">
  <div>
    <p class="eyebrow">Último informe · {long_date(latest.date)}</p>
    <h1>Informe pre-apertura</h1>
    <p class="summary">{html.escape(latest.summary) or "Resumen no disponible."}</p>
    <div class="hero-meta">{thermo_pill(latest.thermo)}{lesson}</div>
    <div class="hero-actions">
      <a class="btn" href="informes/{latest.slug}.html">Leer el informe completo</a>
      <a class="btn btn-ghost" href="historial.html">Historial de recomendaciones</a>
    </div>
  </div>
  <div>
    <p class="eyebrow">Recomendaciones del día</p>
    {reco_list(latest, reports[1] if len(reports) > 1 else None)}
  </div>
</section>
{recent_block}
"""


def render_report(report: Report, prev: Report | None, next_: Report | None) -> str:
    body, toc = render_markdown(report.body_md)
    lesson = f'<span class="muted">🎓 Lección: {html.escape(report.lesson)}</span>' if report.lesson else ""
    pager = []
    if prev:
        pager.append(f'<a class="card prev" href="{prev.slug}.html"><small>← Anterior</small>{long_date(prev.date)}</a>')
    else:
        pager.append("<span></span>")
    if next_:
        pager.append(f'<a class="card next" href="{next_.slug}.html"><small>Siguiente →</small>{long_date(next_.date)}</a>')
    return f"""
<header class="report-head">
  <p class="eyebrow">Informe pre-apertura</p>
  <h1>{long_date(report.date)}</h1>
  <div class="hero-meta">{thermo_pill(report.thermo)}{lesson}</div>
</header>
<div class="report-layout">
  <aside class="toc"><details open><summary class="eyebrow">En este informe</summary>{toc}</details></aside>
  <article class="card prose">{body}</article>
</div>
<nav class="pager">{"".join(pager)}</nav>
"""


def render_archive(reports: list[Report]) -> str:
    if not reports:
        return '<h1>Archivo</h1><p class="muted">Todavía no hay informes.</p>'
    months: dict[tuple[int, int], list[Report]] = {}
    for r in reports:
        months.setdefault((r.date.year, r.date.month), []).append(r)
    blocks = []
    for (year, month), items in months.items():
        rows = "".join(
            f'<li><a href="informes/{r.slug}.html"><span class="date">{long_date(r.date)}</span>'
            f'{thermo_pill(r.thermo)}<span class="summary">{html.escape(r.summary)}</span></a></li>'
            for r in items
        )
        blocks.append(f'<section class="month"><h2>{MONTHS[month - 1].capitalize()} {year}</h2>'
                      f'<ul class="card archive-list">{rows}</ul></section>')
    return f'<p class="eyebrow">{len(reports)} informes</p><h1>Archivo</h1>{"".join(blocks)}'


def render_learn(log_text: str, slugs: set[str]) -> str:
    body, _ = render_markdown(log_text)
    body = re.sub(
        r"<td>(\d{4}-\d{2}-\d{2})</td>",
        lambda m: (f'<td><a href="informes/{m.group(1)}.html">{m.group(1)}</a></td>' if m.group(1) in slugs
                   else m.group(0)),
        body,
    )
    return f'<p class="eyebrow">Aprende con cada informe</p><h1>Temario y lecciones</h1><article class="card prose">{body}</article>'


# ── Orquestación ─────────────────────────────────────────────────────────────

def build(cfg: dict, reports_dir: Path = REPORTS_DIR, docs_dir: Path = DOCS_DIR,
          learning_log: Path = LEARNING_LOG, now: datetime | None = None) -> dict:
    site = cfg.get("site") or {}
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    generated = (now or datetime.now(timezone.utc)).astimezone(tz).strftime("%Y-%m-%d %H:%M")
    base = Template((SITE_SRC / "base.html").read_text(encoding="utf-8"))
    reports = load_reports(reports_dir)

    (docs_dir / "informes").mkdir(parents=True, exist_ok=True)
    (docs_dir / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SITE_SRC / "style.css", docs_dir / "assets" / "style.css")
    shutil.copyfile(SITE_SRC / "site.js", docs_dir / "assets" / "site.js")
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")

    common = {"site": site, "generated": generated}
    latest_desc = reports[0].summary if reports else "Informe diario de mercados para inversores principiantes."
    pages = {
        "index.html": _page(base, title=site.get("title", "Informe pre-apertura"), description=latest_desc,
                            content=render_index(reports), root="", active="index", body_class="home", **common),
        "archivo.html": _page(base, title="Archivo · " + site.get("title", "Informe pre-apertura"),
                              description="Todos los informes pre-apertura.", content=render_archive(reports),
                              root="", active="archive", **common),
    }
    log_text = learning_log.read_text(encoding="utf-8") if learning_log.exists() else "# Registro de aprendizaje\n"
    pages["aprende.html"] = _page(base, title="Aprende · " + site.get("title", "Informe pre-apertura"),
                                  description="Temario y conceptos explicados en los informes.",
                                  content=render_learn(log_text, {r.slug for r in reports}),
                                  root="", active="learn", **common)
    pages["historial.html"] = _page(base, title="Historial · " + site.get("title", "Informe pre-apertura"),
                                    description="Recomendaciones por activo a lo largo de los informes.",
                                    content=render_history(reports), root="", active="history", **common)
    for i, report in enumerate(reports):
        older = reports[i + 1] if i + 1 < len(reports) else None
        newer = reports[i - 1] if i > 0 else None
        pages[f"informes/{report.slug}.html"] = _page(
            base, title=f"{long_date(report.date)} · Informe pre-apertura",
            description=report.summary or "Informe pre-apertura", content=render_report(report, older, newer),
            root="../", active="report", body_class="report", **common,
        )

    expected = {f"{r.slug}.html" for r in reports}
    for stale in (docs_dir / "informes").glob("*.html"):  # informes borrados de reports/
        if stale.name not in expected:
            stale.unlink()
    for rel, content in pages.items():
        (docs_dir / rel).write_text(content, encoding="utf-8")

    return {
        "reports": len(reports),
        "pages": sorted(pages),
        "latest": reports[0].slug if reports else None,
        "url": (site.get("base_url", "").rstrip("/") + f"/informes/{reports[0].slug}.html")
        if reports and site.get("base_url") else None,
        "issues": {r.slug: r.issues for r in reports if r.issues},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera el sitio web del informe diario en docs/.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--docs-dir", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    result = build(cfg, docs_dir=Path(args.docs_dir) if args.docs_dir else DOCS_DIR)
    print(f"Sitio generado: {result['reports']} informes, {len(result['pages'])} páginas")
    if result["url"]:
        print(f"  Último informe: {result['url']}")
    for slug, issues in result["issues"].items():
        print(f"  ⚠ {slug}: {'; '.join(issues)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
