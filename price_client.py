"""
Price Client — séries de preço para análise técnica de ações B3.

Backend plugável (padrão: Yahoo Finance via endpoint /v8/finance/chart).
Desenhado para trocar facilmente por brapi.dev quando houver token — basta
implementar outra função `_fetch_<backend>` com a mesma assinatura.

Arquitetura de cache: um JSON por ticker em _cache/precos/, carimbado pela
data do dia. Preço muda diariamente → cache diário desacoplado do pipeline
pesado da CVM (cache_cvm). Reusa o arquivo se ele já foi baixado hoje.
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ─── Config ─────────────────────────────────────────────────────────────────────
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_BENCHMARK_SYMBOL = "^BVSP"          # Ibovespa — para força relativa
_BENCHMARK_KEY    = "__IBOV__"       # chave interna no dict de retorno
_DEFAULT_RANGE = "2y"                # cobre momentum 12m + média móvel 200d
_THROTTLE_S    = 0.30                # cortesia entre requests (evita rate-limit)
_MAX_RETRIES   = 3


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class PriceSeries:
    """Série de preço ajustado de um ticker, ordenada por data crescente."""
    ticker: str                                   # ex: "WEGE3"
    symbol: str                                   # símbolo no backend, ex: "WEGE3.SA"
    cd_cvm: str = ""
    nome: str = ""
    dates: List[str] = field(default_factory=list)   # ["2024-01-02", ...]
    close: List[float] = field(default_factory=list)  # preço ajustado (total return)
    raw_close: List[float] = field(default_factory=list)  # preço de fechamento bruto
    volume: List[float] = field(default_factory=list)
    market_price: Optional[float] = None          # último preço (meta)
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    currency: str = "BRL"
    erro: Optional[str] = None

    def __len__(self) -> int:
        return len(self.close)

    @property
    def ok(self) -> bool:
        return self.erro is None and len(self.close) > 0

    def last(self) -> Optional[float]:
        return self.close[-1] if self.close else None


# ─── Utilitários ────────────────────────────────────────────────────────────────

def _log(msg: str, end: str = "\n") -> None:
    print(msg, end=end, file=sys.stderr, flush=True)


def _http_get_json(url: str) -> dict:
    """GET com retries e backoff simples. Lança em falha definitiva."""
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429:                # rate-limited → espera mais
                time.sleep(1.5 * (attempt + 1))
                continue
            if exc.code in (404, 401):         # não existe / não autorizado → desiste já
                raise
            time.sleep(0.5 * (attempt + 1))
        except Exception as exc:               # timeout, conexão, JSON inválido
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError("falha desconhecida")


# ─── Backend: Yahoo Finance ───────────────────────────────────────────────────

def _fetch_yahoo(symbol: str, range_: str = _DEFAULT_RANGE) -> dict:
    """
    Retorna dict cru normalizado a partir do endpoint /v8/finance/chart.
    Estrutura: {dates, close, raw_close, volume, market_price, week52_high,
                week52_low, currency}.
    """
    url = _YAHOO_CHART.format(symbol=symbol) + f"?range={range_}&interval=1d"
    data = _http_get_json(url)
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise ValueError("resposta Yahoo sem 'result'")

    meta = result.get("meta", {})
    ts   = result.get("timestamp") or []
    ind  = result.get("indicators", {})
    quote   = (ind.get("quote") or [{}])[0]
    adjwrap = (ind.get("adjclose") or [{}])
    adj     = adjwrap[0].get("adjclose") if adjwrap else None
    raw     = quote.get("close") or []
    vol     = quote.get("volume") or []

    dates, close, raw_close, volume = [], [], [], []
    for i, t in enumerate(ts):
        a = adj[i] if adj and i < len(adj) else None
        r = raw[i] if i < len(raw) else None
        price = a if a is not None else r          # fallback adj→raw
        if price is None:
            continue                                # pula dias sem fechamento (feriado parcial)
        dates.append(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"))
        close.append(float(price))
        raw_close.append(float(r) if r is not None else float(price))
        volume.append(float(vol[i]) if i < len(vol) and vol[i] is not None else 0.0)

    return {
        "dates": dates,
        "close": close,
        "raw_close": raw_close,
        "volume": volume,
        "market_price": meta.get("regularMarketPrice"),
        "week52_high": meta.get("fiftyTwoWeekHigh"),
        "week52_low": meta.get("fiftyTwoWeekLow"),
        "currency": meta.get("currency", "BRL"),
    }


def _symbol_for(ticker_b3: str, backend: str, mercado: str = "BR") -> str:
    """Converte ticker → símbolo do backend. mercado='US' não anexa '.SA' (NYSE/Nasdaq)."""
    if backend == "yahoo":
        if ticker_b3.startswith("^") or mercado.upper() == "US":
            return ticker_b3                          # US: o ticker já é o símbolo Yahoo
        return f"{ticker_b3}.SA"                      # BR: B3 → sufixo .SA
    return ticker_b3


_BACKENDS = {"yahoo": _fetch_yahoo}


# ─── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path, ticker: str) -> Path:
    safe = ticker.replace("^", "_").replace("/", "_")
    return cache_dir / "precos" / f"{safe}.json"


def _read_cache(path: Path, max_age_days: int = 1) -> Optional[dict]:
    """Lê o cache se existir e for recente (carimbo 'fetched_on' <= max_age_days)."""
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched = blob.get("fetched_on")
    if not fetched:
        return None
    try:
        age = (date.today() - date.fromisoformat(fetched)).days
    except ValueError:
        return None
    return blob if age < max_age_days else None


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "fetched_on": date.today().isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ─── Entry point público ────────────────────────────────────────────────────────

def load_prices(
    tickers: List[Dict[str, str]],
    cache_dir: Path = Path("_cache"),
    backend: str = "yahoo",
    range_: str = _DEFAULT_RANGE,
    force_download: bool = False,
    include_benchmark: bool = True,
    mercado: str = "BR",
    benchmark_symbol: Optional[str] = None,
) -> Dict[str, PriceSeries]:
    """
    Carrega séries de preço para uma lista de ações.

    Args:
        tickers: lista de dicts com chaves: ticker_b3 (obrigatório), cd_cvm, nome.
                 Linhas sem ticker_b3 são ignoradas.
        cache_dir: raiz do cache (séries vão em <cache_dir>/precos/).
        backend: "yahoo" (padrão). Plugável — adicione em _BACKENDS.
        range_: janela histórica ("2y" cobre momentum 12m + MM200).
        force_download: ignora cache.
        include_benchmark: também carrega o Ibovespa (chave "__IBOV__") p/ força relativa.

    Returns:
        dict {ticker_b3: PriceSeries}. Benchmark sob a chave _BENCHMARK_KEY.
    """
    if backend not in _BACKENDS:
        raise ValueError(f"backend desconhecido: {backend} (disponíveis: {list(_BACKENDS)})")
    fetch = _BACKENDS[backend]
    cache_dir = Path(cache_dir)

    # Deduplica e filtra linhas sem ticker_b3
    seen: set = set()
    fila: List[Dict[str, str]] = []
    for t in tickers:
        tb3 = (t.get("ticker_b3") or "").strip().upper()
        if not tb3 or tb3 in seen:
            continue
        seen.add(tb3)
        fila.append({"ticker_b3": tb3, "cd_cvm": t.get("cd_cvm", ""), "nome": t.get("nome", "")})

    bench_sym = benchmark_symbol or _BENCHMARK_SYMBOL
    if include_benchmark:
        bench_nome = "S&P 500" if mercado.upper() == "US" else "Ibovespa"
        fila.append({"ticker_b3": bench_sym, "cd_cvm": "", "nome": bench_nome})

    total = len(fila)
    _log(f"Preços ({backend}): {total} símbolos (range={range_})…")
    results: Dict[str, PriceSeries] = {}
    n_cache = n_net = n_err = 0

    for i, t in enumerate(fila, 1):
        tb3    = t["ticker_b3"]
        symbol = _symbol_for(tb3, backend, mercado)
        key    = _BENCHMARK_KEY if tb3 == bench_sym else tb3
        path   = _cache_path(cache_dir, tb3)

        bar = "█" * int(20 * i / total) + "░" * (20 - int(20 * i / total))
        _log(f"  {bar} {i}/{total}  {tb3:<8}        ", end="\r")

        raw: Optional[dict] = None
        if not force_download:
            cached = _read_cache(path)
            if cached:
                raw = cached
                n_cache += 1

        if raw is None:
            try:
                raw = fetch(symbol, range_)
                _write_cache(path, {**raw, "symbol": symbol})
                n_net += 1
                time.sleep(_THROTTLE_S)
            except Exception as exc:
                n_err += 1
                results[key] = PriceSeries(
                    ticker=tb3, symbol=symbol, cd_cvm=t["cd_cvm"], nome=t["nome"],
                    erro=f"{type(exc).__name__}: {exc}",
                )
                continue

        results[key] = PriceSeries(
            ticker=tb3, symbol=symbol, cd_cvm=t["cd_cvm"], nome=t["nome"],
            dates=raw.get("dates", []),
            close=raw.get("close", []),
            raw_close=raw.get("raw_close", []),
            volume=raw.get("volume", []),
            market_price=raw.get("market_price"),
            week52_high=raw.get("week52_high"),
            week52_low=raw.get("week52_low"),
            currency=raw.get("currency", "BRL"),
        )

    _log("")  # newline após barra
    _log(f"OK — {n_net} baixados, {n_cache} do cache, {n_err} erros")
    return results


# ─── CLI de teste rápido ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import io

    # Força UTF-8 no stdout do Windows (box-drawing chars / acentos)
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)

    p = argparse.ArgumentParser(description="Teste do price_client")
    p.add_argument("tickers", nargs="*", default=["WEGE3", "PETR4", "VALE3"],
                   help="Tickers B3 a testar")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--range", default=_DEFAULT_RANGE)
    args = p.parse_args()

    lista = [{"ticker_b3": t, "cd_cvm": "", "nome": t} for t in args.tickers]
    series = load_prices(lista, range_=args.range, force_download=args.no_cache)

    print(f"\n{'Ticker':<10}{'Pts':>6}{'Último':>12}{'Máx52s':>12}{'Erro'}")
    print("─" * 60)
    for key, s in series.items():
        if s.erro:
            print(f"{s.ticker:<10}{'—':>6}{'—':>12}{'—':>12}  {s.erro[:30]}")
        else:
            print(f"{s.ticker:<10}{len(s):>6}{s.last() or 0:>12.2f}{s.week52_high or 0:>12.2f}")
