# Informe diario pre-apertura con Claude Code

Línea independiente del agente local: una **tarea programada de Claude Code** (sesión en la nube de
Anthropic) que cada día laborable, antes de que abran la bolsa española (9:00) y Wall Street
(15:30, hora de Madrid), recoge noticias y precios públicos y escribe un informe en español **didáctico** pensado para un
**inversor moderado y principiante** que quiere aprender con cada informe.

- **Sin secretos**: no usa Alpaca, Gmail ni Ollama. Solo fuentes públicas.
- **Python recoge y calcula, Claude analiza**: `fetch_news.py` reutiliza `src/news_sources.py` (RSS + Yahoo
  Finance por símbolo); `fetch_prices.py` descarga 2 años de velas diarias de Yahoo Finance y calcula
  indicadores técnicos y gráficos; Claude añade web fetch / búsquedas y redacta el informe.
- **Didáctico**: cada informe incluye notas 💡 para los términos técnicos y una lección del día
  ("Aprende con el informe de hoy") que progresa según `learning_log.md`.
- **Entrega**: el informe se guarda en `claude_agent/reports/AAAA-MM-DD.md` (commit + push).

## Flujo diario

```
Tarea programada (L–V ~7:47 Madrid)
  └─ Claude sigue claude_agent/daily_prompt.md
       1. python claude_agent/fetch_news.py      → data/news_FECHA.json (RSS ES/EU/US + watchlist)
       2. python claude_agent/fetch_prices.py    → data/prices_FECHA.json (indicadores técnicos)
       3. web fetch de sources.md + búsquedas     → cierre anterior, futuros, agenda, bancos centrales
          + lee inputs/                           → tus notas, cartera, empresas de interés
       4. elige 3–5 ideas (noticias + técnico) → fetch_prices.py --charts … → reports/charts/FECHA/*.png
       5. elige la lección del día (learning_log.md)
       6. redacta reports/FECHA.md con report_template.md
       7. git commit + push (informe + gráficos + learning_log.md)
```

## Archivos

| Archivo | Para qué |
|---|---|
| `config.yaml` | Feeds RSS (España, Europa, EE. UU., macro), watchlist por símbolo, ventana horaria, límites |
| `sources.md` | **Editable**: páginas para web fetch, búsquedas del día y tus intereses |
| `inputs/` | **Editable**: deja aquí archivos propios (notas, cartera, PDFs, listas de enlaces) |
| `fetch_news.py` | Recoge noticias públicas → JSON compacto (sin LLM) |
| `fetch_prices.py` | Velas diarias (Yahoo, 2 años) → indicadores (MM20/50/200, RSI, MACD, volatilidad, 52 semanas, soportes/resistencias) + gráficos PNG |
| `learning_log.md` | Temario didáctico y registro de conceptos ya explicados (lo actualiza Claude) |
| `daily_prompt.md` | Instrucciones completas que sigue Claude en cada ejecución |
| `report_template.md` | Estructura fija del informe |
| `reports/` | Informes generados (versionados) y sus gráficos en `reports/charts/FECHA/` |
| `data/` | JSON crudos de cada día (ignorado por git) |

## Ejecución manual

```bash
pip install -r claude_agent/requirements.txt
python claude_agent/fetch_news.py                 # → claude_agent/data/news_AAAA-MM-DD.json
python claude_agent/fetch_news.py --no-full-content --output /tmp/news.json
python claude_agent/fetch_prices.py                # → claude_agent/data/prices_AAAA-MM-DD.json
python claude_agent/fetch_prices.py --charts SAN.MC,^IBEX   # + gráficos en reports/charts/AAAA-MM-DD/
```

Luego, en una sesión de Claude Code: *"Sigue claude_agent/daily_prompt.md"*.

## Personalizar

- **Más fuentes RSS** → añade `{name, url}` al grupo correspondiente de `config.yaml`.
  Cada ejecución informa en `feed_status` de las que fallan (`fallo`) o no tienen novedades.
  Comprobado desde la nube: CNBC, Cinco Días, elEconomista y Bolsamanía bloquean o no sirven el RSS;
  las páginas de artículo de Investing.com bloquean la descarga (solo se usa su resumen RSS).
  No se intenta saltar esas protecciones anti-bot (se probó Scrapling y se descartó por ese motivo).
- **Más valores** → añade `{symbol, name}` a `watchlist` (Yahoo Finance; `.MC` = Bolsa de Madrid).
  Se usan tanto para noticias como para el análisis técnico; solo los valores de la watchlist pueden tener gráfico.
- **Lecciones** → edita el temario de `learning_log.md` o añade temas que quieras aprender.
- **Páginas concretas o búsquedas** → edita `sources.md`.
- **Tu contexto personal** → archivos en `inputs/` (no pongas datos sensibles: el repo puede ser visible).

## Tarea programada

- Horario: `CRON_TZ=Europe/Madrid 47 7 * * 1-5` (lunes a viernes, 7:47).
- Cada disparo crea una sesión nueva con este prompt:

  > Haz checkout de la rama `Trading-Agent_Claude` del repositorio y sigue al pie
  > de la letra las instrucciones de `claude_agent/daily_prompt.md` para generar y publicar el informe
  > pre-apertura de hoy.

- Los lunes la ventana de noticias se amplía a 72 h para incluir el fin de semana.
- Festivos: si ambos mercados están cerrados, el informe lo indica y se centra en la semana.

## Tests

```bash
pytest tests/claude_agent -q --no-cov
```

> Aviso: el informe es información general generada por IA a partir de noticias públicas.
> No es asesoramiento financiero.
