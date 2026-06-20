"""
Fundamentus Client — múltiplos fundamentais de mercado para ações B3.

Fonte: https://www.fundamentus.com.br/resultado.php — um único request traz
TODAS as ações negociadas, com P/L, P/VP, EV/EBITDA, ROE, liquidez média
diária (2m), patrimônio líquido e dívida líquida/PL. Complementa a CVM
(fundamentos contábeis) e o Yahoo (preço/momentum técnico) com as métricas
de valuation de mercado que dependem de market cap.

Mkt Cap não vem explícito na tabela bulk, mas é derivável:
    Mkt Cap = P/VP × Patrimônio Líquido

Cache: um JSON em <cache_dir>/fundamentus/resultado.json carimbado pela data
do dia (mesmo padrão diário do price_client) — valuation muda a cada pregão.
"""

from __future__ import annotations

import html as _html
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, Optional

_URL = "https://www.fundamentus.com.br/resultado.php"
_MAX_RETRIES = 3

# Índice da célula (0-based, após a coluna "Papel") → chave de saída.
# Ordem fixa do resultado.php:
#   0 Cotação · 1 P/L · 2 P/VP · 3 PSR · 4 Div.Yield · 5 P/Ativo ·
#   6 P/Cap.Giro · 7 P/EBIT · 8 P/Ativ Circ.Liq · 9 EV/EBIT · 10 EV/EBITDA ·
#   11 Mrg Bruta · 12 Mrg Ebit · 13 Mrg.Líq · 14 Liq.Corr · 15 ROIC · 16 ROE ·
#   17 Liq.2meses · 18 Patrim.Líq · 19 Dív.Líq/Patrim · 20 Cresc.Rec.5a
_COLS = {
    0:  "cotacao",
    1:  "pl",
    2:  "pvp",
    10: "ev_ebitda",
    13: "mrg_liq",
    16: "roe",
    17: "liq_2m",
    18: "patrim_liq",
    19: "div_liq_pl",
}


def _log(msg: str, end: str = "\n") -> None:
    print(msg, end=end, file=sys.stderr, flush=True)


def _parse_num(txt: str) -> Optional[float]:
    """
    Converte número no formato brasileiro para float.
    '17.746.100.000,00' → 17746100000.0 · '35,43%' → 35.43 · '-0,19' → -0.19
    '-' / '' → None.
    """
    if txt is None:
        return None
    s = txt.strip().replace("%", "").strip()
    if not s or s == "-":
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _http_get(url: str) -> str:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
                return resp.read().decode("latin-1")
        except Exception as exc:                 # noqa: BLE001
            last_exc = exc
            import time
            time.sleep(0.6 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError("falha desconhecida")


def _parse_resultado(page: str) -> Dict[str, Dict]:
    """Extrai {ticker: {cotacao, pl, pvp, ev_ebitda, ..., mkt_cap}} da tabela."""
    out: Dict[str, Dict] = {}
    # Cada linha de dado contém um link detalhes.php?papel=XXXX seguido das <td>.
    for m in re.finditer(r'detalhes\.php\?papel=([A-Z0-9]+)["\'].*?</tr>',
                         page, re.S):
        papel = m.group(1).upper()
        cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(0), re.S)
        clean = [_html.unescape(re.sub(r"<.*?>", "", c)).strip() for c in cells]
        # clean[0] é o próprio papel (dentro do <a>); valores começam em clean[1].
        vals = clean[1:] if clean and clean[0].upper() == papel else clean
        rec: Dict[str, Optional[float]] = {}
        for idx, chave in _COLS.items():
            rec[chave] = _parse_num(vals[idx]) if idx < len(vals) else None

        # Mkt Cap = P/VP × Patrimônio Líquido (apenas quando ambos > 0).
        pvp, pl_eq = rec.get("pvp"), rec.get("patrim_liq")
        rec["mkt_cap"] = (pvp * pl_eq
                          if pvp and pl_eq and pvp > 0 and pl_eq > 0 else None)
        out[papel] = rec
    return out


# ─── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / "fundamentus" / "resultado.json"


def _read_cache(path: Path, max_age_days: int = 1) -> Optional[Dict]:
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
    return blob.get("dados") if age < max_age_days else None


def _write_cache(path: Path, dados: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_on": date.today().isoformat(), "dados": dados}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ─── Entry point ──────────────────────────────────────────────────────────────

def load_fundamentus(
    cache_dir: Path = Path("_cache"),
    force_download: bool = False,
) -> Dict[str, Dict]:
    """
    Carrega os fundamentais de mercado de todas as ações da B3 (um request).

    Returns:
        dict {ticker: {cotacao, pl, pvp, ev_ebitda, mrg_liq, roe, liq_2m,
                       patrim_liq, div_liq_pl, mkt_cap}}.
        Em falha de rede, retorna {} (pipeline segue sem esses indicadores).
    """
    cache_dir = Path(cache_dir)
    path = _cache_path(cache_dir)

    if not force_download:
        cached = _read_cache(path)
        if cached:
            _log(f"Fundamentus: {len(cached)} ações do cache")
            return cached

    _log("Fundamentus: baixando resultado.php…", end="")
    try:
        page = _http_get(_URL)
        dados = _parse_resultado(page)
        _write_cache(path, dados)
        _log(f" OK — {len(dados)} ações")
        return dados
    except Exception as exc:                      # noqa: BLE001
        _log(f" ERRO ({type(exc).__name__}: {exc}) — seguindo sem fundamentais")
        stale = _read_cache(path, max_age_days=10_000)   # usa cache velho se houver
        return stale or {}


# ─── CLI de teste ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    dados = load_fundamentus(force_download="--no-cache" in sys.argv)
    print(f"\nTotal: {len(dados)} ações\n")
    for tk in ("WEGE3", "ALPK3", "PETR4", "VALE3"):
        d = dados.get(tk)
        if not d:
            print(f"{tk:<8} não encontrado")
            continue
        mc = d.get("mkt_cap")
        print(f"{tk:<8} P/L={d.get('pl')}  EV/EBITDA={d.get('ev_ebitda')}  "
              f"Liq2m={d.get('liq_2m')}  MktCap={mc/1e9:.1f}bi" if mc else
              f"{tk:<8} P/L={d.get('pl')}  EV/EBITDA={d.get('ev_ebitda')}  "
              f"Liq2m={d.get('liq_2m')}  MktCap=—")
