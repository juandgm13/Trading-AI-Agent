# Fuentes que Claude consulta directamente (web fetch / búsqueda web)

Este archivo es **editable**: añade, quita o comenta líneas libremente.
Claude lo lee en cada ejecución diaria, además del JSON generado por `fetch_news.py`.

- Una línea `- URL — descripción` en *Páginas a leer* → Claude hace web fetch de esa URL.
- Una línea en *Búsquedas del día* → Claude hace una búsqueda web con ese texto (añadiendo la fecha de hoy).
- Lo que pongas en *Mis intereses* se usa para priorizar el contenido del informe.
- Las líneas que empiezan por `<!--` o están dentro de comentarios HTML se ignoran.

Límite orientativo: ~12 páginas y ~7 búsquedas por ejecución (para que el informe salga a tiempo).
Si una página falla o está bloqueada, Claude lo anota en el apartado de fuentes y sigue.

## Páginas a leer

- https://www.expansion.com/mercados.html — Portada de mercados de Expansión (Ibex, última hora)
- https://www.expansion.com/mercados/cotizaciones/indices/ibex35_I.IB.html — Cotización y valores del Ibex 35
- https://finance.yahoo.com/quote/ES=F/ — Futuros del S&P 500 (anticipan la apertura de Wall Street)
- https://finance.yahoo.com/quote/NQ=F/ — Futuros del Nasdaq 100
- https://finance.yahoo.com/quote/%5ESTOXX50E/ — Euro Stoxx 50
- https://finance.yahoo.com/quote/EURUSD=X/ — Cambio euro/dólar
- https://finance.yahoo.com/quote/BZ=F/ — Petróleo Brent
- https://www.ecb.europa.eu/press/pr/html/index.en.html — Notas de prensa del BCE
- https://www.federalreserve.gov/newsevents/pressreleases.htm — Notas de prensa de la Reserva Federal
- https://www.coindesk.com/markets — Mercado cripto: movimientos de las últimas 24 h (CoinDesk)
- https://alternative.me/crypto/fear-and-greed-index/ — Índice de miedo/codicia cripto (con histórico)

## Búsquedas del día

- calendario económico hoy España Europa Estados Unidos datos macro
- previsión apertura Ibex 35 hoy
- futuros Wall Street hoy premarket
- resultados empresariales hoy Ibex Wall Street
- earnings today before the bell
- bitcoin ethereum noticias hoy mercado cripto regulación ETF

## Mis intereses

<!-- Escribe aquí empresas, sectores o temas que te interesen, por ejemplo: -->
- Perfil: inversor moderado y principiante, horizonte medio/largo plazo.
- Preferencia por empresas grandes y consolidadas, dividendos y ETFs indexados.
- Mercados: España (Ibex 35) y Estados Unidos (S&P 500, Nasdaq).
- Criptomonedas: opero en **Coinbase** (en euros). Tope del 25 % de la cartera en cripto.
- Quiero recomendaciones directas (comprar / mantener / reducir / vender / esperar) con su porqué.
