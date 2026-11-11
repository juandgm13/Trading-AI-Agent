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
- **Recomendaciones directas**: para cada idea, qué haría un experto (Comprar / Comprar más / Mantener /
  Reducir / Vender / Esperar / Evitar), por qué, cuánto, horizonte y qué le haría cambiar de opinión.
- **Criptomonedas**: el universo son **todas las monedas de Coinbase** (~400, cualquier par). Radar diario
  (mayores por capitalización, más suben/bajan en el top 100, cuántas suben y bajan), análisis técnico
  detallado del núcleo + top 15 + tus monedas, sentimiento (Fear & Greed) y noticias. Tope del 25 % de la cartera.
- **Tu cartera**: si apuntas tus movimientos en `inputs/cartera.csv`, el informe valora tus posiciones y
  te dice qué hacer con cada una.
- **Entrega**: el informe se guarda en `claude_agent/reports/AAAA-MM-DD.md` y se publica como web en
  **GitHub Pages** (carpeta `docs/` de la rama `Trading-Agent_Claude`):
  <https://juandgm13.github.io/Trading-AI-Agent/> — portada con el último informe y sus recomendaciones,
  informe completo con índice, archivo, página "Aprende" e "Historial" (qué se recomendó cada día para cada activo).
- **Coherencia**: cada informe lee el anterior y tu `cartera.csv`; mantiene acciones y niveles salvo que algo cambie
  (y lo explica), y la tabla de recomendaciones indica el cambio "vs. ayer".

## Flujo diario

```
Tarea programada (L–V ~7:47 Madrid)
  └─ Claude sigue claude_agent/daily_prompt.md
       1. python claude_agent/fetch_news.py      → data/news_FECHA.json (RSS ES/EU/US + watchlist)
       2. python claude_agent/fetch_prices.py    → data/prices_FECHA.json (indicadores técnicos; último cierre
                                                    completado con el precio de cierre de Yahoo si la vela llega tarde)
       3. python claude_agent/fetch_crypto.py    → data/crypto_FECHA.json (universo Coinbase, radar, técnico, Fear & Greed)
       4. python claude_agent/portfolio.py       → data/portfolio_FECHA.json (tu cartera desde inputs/cartera.csv)
       5. web fetch de TODAS las páginas y búsquedas de sources.md (sin límite de tiempo)
                                                  → cierre anterior, futuros, agenda, bancos centrales, cripto
          + lee inputs/                           → tus notas, empresas de interés
          + lee el informe anterior                → qué se recomendó ayer, qué pasó, qué ejecutaste
       6. decide 3–5 ideas de bolsa + 1–3 de cripto con acción directa (y un veredicto para cada valor de la
          watchlist, p. ej. Airbus) → --charts … → docs/informes/charts/FECHA/*.png
       7. elige la lección del día (learning_log.md)
       8. redacta reports/FECHA.md con report_template.md (front matter + Markdown, hasta ~6.000 palabras)
       9. python claude_agent/build_site.py      → regenera la web en docs/ (GitHub Pages)
      10. git commit + push (informe + learning_log.md + docs/)
```

## Archivos

| Archivo | Para qué |
|---|---|
| `config.yaml` | Feeds RSS (España, Europa, EE. UU., macro, cripto), watchlist por símbolo, bloque `crypto` (Coinbase, tope 25 %, núcleo, `auto_top_n`), `portfolio`, ventana horaria, límites |
| `sources.md` | **Editable**: páginas para web fetch, búsquedas del día y tus intereses |
| `inputs/` | **Editable**: deja aquí archivos propios (notas, PDFs, listas de enlaces) |
| `inputs/cartera.csv` | **Editable como tabla**: tus movimientos (compras, ventas, dividendos). Formato en `inputs/README.md` |
| `fetch_news.py` | Recoge noticias públicas → JSON compacto (sin LLM) |
| `fetch_crypto.py` | Cripto: universo = todas las monedas de Coinbase cruzadas con Coinpaprika, radar (top por capitalización, más suben/bajan, amplitud), Fear & Greed, capitalización/dominancia e indicadores con velas diarias de Coinbase (par EUR o USD, 365 días) + gráficos |
| `build_site.py` | Convierte todos los informes en la web estática de `docs/` (HTML/CSS propios, sin Jekyll): portada, `informes/FECHA.html` (en cada idea el gráfico queda fijo arriba mientras se lee su texto), `archivo.html`, `aprende.html`, `historial.html` (recomendaciones por activo e informe) |
| `site/` | Plantilla HTML (`base.html`), estilos (`style.css`, modo claro/oscuro, móvil, impresión) y `site.js` (tema y ampliar gráficos) |
| `portfolio.py` | Lee `inputs/cartera.csv` → posiciones (precio medio), valor y G/P en EUR, liquidez (`tipo=liquidez`), peso de cripto frente al tope y próxima aportación mensual (`portfolio.aportacion_mensual`) |
| `fetch_prices.py` | Velas diarias (Yahoo, 2 años; si el último cierre llega tarde lo completa con el precio de cierre de Yahoo, descarta la vela de una sesión aún abierta y marca `stale`) → indicadores (MM20/50/200, RSI, MACD, volatilidad, 52 semanas, soportes/resistencias) + gráficos PNG |
| `learning_log.md` | Temario didáctico y registro de conceptos ya explicados (lo actualiza Claude) |
| `daily_prompt.md` | Instrucciones completas que sigue Claude en cada ejecución |
| `report_template.md` | Estructura fija del informe |
| `reports/` | Informes generados (Markdown con front matter, versionados). Sus gráficos están en `docs/informes/charts/FECHA/` |
| `data/` | JSON crudos de cada día (ignorado por git) |

## Ejecución manual

```bash
pip install -r claude_agent/requirements.txt
python claude_agent/fetch_news.py                 # → claude_agent/data/news_AAAA-MM-DD.json
python claude_agent/fetch_news.py --no-full-content --output /tmp/news.json
python claude_agent/fetch_prices.py                # → claude_agent/data/prices_AAAA-MM-DD.json
python claude_agent/fetch_prices.py --charts SAN.MC,^IBEX   # + gráficos en docs/informes/charts/AAAA-MM-DD/
python claude_agent/fetch_crypto.py                # → claude_agent/data/crypto_AAAA-MM-DD.json
python claude_agent/fetch_crypto.py --charts BTC,PEPE      # cualquier moneda de Coinbase
python claude_agent/portfolio.py                   # → claude_agent/data/portfolio_AAAA-MM-DD.json
python claude_agent/build_site.py                  # → docs/ (abre docs/index.html en el navegador)
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
- **Criptomonedas** → el universo (todas las de Coinbase) se actualiza solo cada día. Para fijar una moneda en
  el análisis detallado añade `{symbol, name}` (código, p. ej. `AAVE`) a `crypto.watchlist`; `crypto.auto_top_n`
  controla cuántas de las mayores por capitalización se analizan a fondo (15).
  Comprobado desde la nube: Bitcoin Magazine bloquea el RSS, CoinGecko limita las peticiones sin clave y Yahoo
  no tiene muchas monedas pequeñas (por eso las velas cripto vienen de Coinbase).
- **Web** → título y URL en `site` de `config.yaml`; diseño en `site/style.css`. La web es pública: el informe
  solo nombra Coinbase como bróker (los demás aparecen como "otro bróker").
- **Tope de cripto** → `crypto.max_portfolio_pct` (25 % por decisión del usuario).
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

> Aviso: el informe y sus recomendaciones son la opinión generada por una IA a partir de noticias y datos
> públicos y de tus movimientos. No es asesoramiento financiero personalizado ni regulado.
