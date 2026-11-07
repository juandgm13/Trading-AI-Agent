# inputs/

Deja aquí cualquier archivo que quieras que Claude tenga en cuenta en el informe diario:
notas personales, una lista de empresas que sigues, un PDF de un análisis,
enlaces a artículos concretos (un `.md` o `.txt` con URLs), etc.

Claude lee todos los archivos de esta carpeta (excepto este README) en cada ejecución.
Formatos recomendados: `.md`, `.txt`, `.csv`, `.pdf`.

## Tu cartera: `cartera.csv`

Apunta aquí **cada movimiento** (compras, ventas y dividendos) de tus brókers y de Coinbase.
Ábrelo como una tabla (Excel, LibreOffice, Numbers o el editor de GitHub). Cada mañana
`portfolio.py` reconstruye tus posiciones, las valora con el último precio y el informe te dice
qué haría un experto con *tu* cartera (mantener, comprar más, reducir, vender) y si respetas el
tope del 25 % en criptomonedas.

| Columna | Qué poner | Ejemplo |
|---|---|---|
| `fecha` | Fecha de la operación (`AAAA-MM-DD`; también vale `DD/MM/AAAA`) | `2026-11-09` |
| `activo` | Ticker de Yahoo Finance | `SAN.MC`, `AAPL`, `SPY`, `BTC-EUR` |
| `nombre` | Nombre legible (opcional) | `Banco Santander` |
| `tipo` | `accion`, `etf`, `fondo`, `cripto` u `otro` | `cripto` |
| `operacion` | `compra`, `venta` o `dividendo` | `compra` |
| `cantidad` | Nº de acciones / participaciones / monedas (vacío en dividendos) | `0.015` |
| `precio` | Precio por unidad en `moneda`. En dividendos: importe neto cobrado | `68000` |
| `moneda` | Moneda del precio (`EUR`, `USD`…) | `EUR` |
| `comision` | Comisiones totales de la operación, en `moneda` (0 si no hay) | `1.49` |
| `broker` | Dónde lo tienes | `Coinbase` |
| `notas` | Lo que quieras (opcional) | `aportación mensual` |

Ejemplo:

```csv
fecha,activo,nombre,tipo,operacion,cantidad,precio,moneda,comision,broker,notas
2026-11-09,SPY,ETF S&P 500,etf,compra,2,735.5,USD,1,MyInvestor,
2026-11-09,BTC-EUR,Bitcoin,cripto,compra,0.005,68000,EUR,1.49,Coinbase,primera compra
2026-12-15,SAN.MC,Banco Santander,accion,dividendo,,12.40,EUR,0,MyInvestor,
```

Notas:
- Se aceptan `,` o `;` como separador y decimales con punto o coma (Excel en español usa `;`).
- Las posiciones se calculan con **precio medio ponderado**; las comisiones suben el coste.
- Las ganancias en EUR de activos en dólares usan el tipo de cambio de hoy (aproximación).
- Si una fila tiene un error, se ignora y aparece avisada en el informe.

> Aviso: el repositorio puede ser visible para otras personas; no pongas aquí datos sensibles
> (números de cuenta, contraseñas, etc.).
