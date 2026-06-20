"""
Cliente SEC EDGAR — equivalente americano do cvm_client (Brasil).

A SEC publica de graça os fundamentos XBRL de todo filer via a API "companyfacts"
(data.sec.gov), com a DATA DE DIVULGAÇÃO de cada número (`filed`) — essencial para
o point-in-time, igual ao DT_RECEB da CVM.

Dois detalhes que o teste do Nubank revelou e que este cliente trata:
  · Domésticas reportam em `us-gaap`; foreign private issuers (Nubank, MELI…) em
    `ifrs-full`. O resolver tenta as duas taxonomias.
  · FPIs entregam só XBRL ANUAL (Form 20-F); domésticas têm 10-K + 10-Q (trimestral).

Uso:
    from sec_client import fundamentos_us
    f = fundamentos_us("AAPL")          # dict de fundamentos anuais + filed dates
    f = fundamentos_us("NU")            # foreign private issuer (IFRS)

Requisito da SEC: User-Agent identificável. Configurável via env SEC_UA.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_UA = {"User-Agent": os.environ.get(
    "SEC_UA", "Zelen Invest research gustavomparreira2005@gmail.com")}
_CTX = ssl.create_default_context()
_CACHE_DIR = Path("_cache_sec")
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
_SUBM_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

# Formas anuais (domésticas 10-K, FPIs 20-F) e trimestrais
_ANUAIS = ("10-K", "20-F", "40-F")


def _full_year(x: dict) -> bool:
    """True se o fato cobre o ano inteiro. Um 10-K traz tanto a linha anual quanto a
    do 4º trimestre (mesma form/concept) — sem este filtro, anos antigos pegavam o
    valor do TRIMESTRE (ex.: receita JNJ ~21bi = ¼ do ano). Itens de balanço (PL,
    ativo) são instantâneos (sem `start`) e passam direto."""
    start = x.get("start")
    end = x.get("end")
    if not start or not end:
        return True
    try:
        from datetime import date as _d
        d0 = _d.fromisoformat(start[:10]); d1 = _d.fromisoformat(end[:10])
        return (d1 - d0).days >= 300
    except ValueError:
        return True


def _get(url: str, cache_name: Optional[str] = None, ttl_h: float = 24.0) -> dict:
    """GET com cache local em disco (json). ttl em horas."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = (_CACHE_DIR / cache_name) if cache_name else None
    if cache and cache.exists() and (time.time() - cache.stat().st_mtime) < ttl_h * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
        data = json.load(r)
    if cache:
        cache.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(0.12)                       # respeita o rate limit da SEC (~10 req/s)
    return data


# ─── Resolução de ticker → CIK ────────────────────────────────────────────────

_TICKER_MAP: Optional[Dict[str, Tuple[int, str]]] = None


def _load_ticker_map() -> Dict[str, Tuple[int, str]]:
    global _TICKER_MAP
    if _TICKER_MAP is None:
        raw = _get(_TICKERS_URL, "company_tickers.json", ttl_h=168.0)
        _TICKER_MAP = {v["ticker"].upper(): (int(v["cik_str"]), v["title"])
                       for v in raw.values()}
    return _TICKER_MAP


def cik_for_ticker(ticker: str) -> Optional[Tuple[int, str]]:
    """(CIK, nome) para um ticker US, ou None."""
    return _load_ticker_map().get(ticker.strip().upper())


# ─── Conceitos XBRL por campo interno (us-gaap E ifrs-full) ────────────────────
# Cada campo tenta os conceitos na ordem; o 1º com dados vence. Mistura as duas
# taxonomias de propósito (o resolver olha em ambas), cobrindo domésticas e FPIs.
_CONCEITOS: Dict[str, List[str]] = {
    "receita": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers"],
    "ebit": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities",
             "OperatingProfitLoss"],
    "lucro_liq": ["NetIncomeLoss", "ProfitLoss",
                  "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "lair": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
             "ProfitLossBeforeTax"],
    "ativo": ["Assets"],
    "pl": ["StockholdersEquity",
           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
           "EquityAttributableToOwnersOfParent", "Equity"],
    "caixa": ["CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
              "CashAndCashEquivalents"],
    "divida_lp": ["LongTermDebtNoncurrent", "LongTermDebt",
                  "NoncurrentPortionOfBorrowings", "NoncurrentBorrowings"],
    "divida_cp": ["LongTermDebtCurrent", "DebtCurrent", "ShorttermBorrowings",
                  "CurrentPortionOfBorrowings", "CurrentBorrowings"],
    "da": ["DepreciationDepletionAndAmortization",
           "DepreciationAmortizationAndAccretionNet",
           "DepreciationAmortizationAndOther", "DepreciationAndAmortization",
           "DepreciationAndAmortisationExpense"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipment"],
    "custo_vendas": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfSales"],
}

# Ações em circulação (instantâneo) — taxonomia dei
_CONCEITOS_SHARES = ["EntityCommonStockSharesOutstanding"]
_CONCEITOS_SHARES_FALLBACK = ["CommonStockSharesOutstanding",
                              "NumberOfSharesOutstanding",
                              "WeightedAverageNumberOfDilutedSharesOutstanding",
                              "WeightedAverageNumberOfSharesOutstandingBasic",
                              "WeightedAverageShares"]


def _facts_node(facts: dict, concept: str) -> Optional[dict]:
    """Procura um conceito em us-gaap, depois ifrs-full, depois dei."""
    f = facts.get("facts", {})
    for tax in ("us-gaap", "ifrs-full", "dei"):
        node = f.get(tax, {}).get(concept)
        if node:
            return node
    return None


def _latest_annual(node: dict, as_of: Optional[str] = None) -> Optional[dict]:
    """Fato anual mais recente (form 10-K/20-F) de um nó de conceito.
    Se as_of for dado, considera só o que foi DIVULGADO até essa data (point-in-time)."""
    if not node:
        return None
    # unidade preferida: USD; senão a primeira (shares para dei)
    units = node.get("units", {})
    arr = units.get("USD") or units.get("USD/shares") or units.get("shares") \
        or (next(iter(units.values()), []))
    cand = [x for x in arr if x.get("form") in _ANUAIS and _full_year(x)]
    cand = cand or arr
    if as_of:
        cand = [x for x in cand if (x.get("filed") or "") <= as_of]
    if not cand:
        return None
    # mais recente por fim de período, desempate por data de divulgação
    cand.sort(key=lambda x: (x.get("end", ""), x.get("filed", "")))
    return cand[-1]


def _annual_series(facts: dict, campos: List[str], as_of: Optional[str] = None,
                   n: int = 6) -> List[Tuple[str, float]]:
    """Série anual (end, valor) do conceito PRIMÁRIO — aquele cujo fato anual mais
    recente é o de período mais novo. Usar um único conceito evita misturar tags
    diferentes ano a ano (ex.: a receita da JNJ saltando 21→85bi por pegar um
    segmento num ano e a linha cheia no outro). A migração de tag é tolerada porque
    o conceito novo costuma ter 5+ anos de histórico próprio."""
    per_concept: Dict[str, Dict[str, dict]] = {}
    best_c, best_end = None, ""
    for c in campos:
        node = _facts_node(facts, c)
        if not node:
            continue
        units = node.get("units", {})
        arr = units.get("USD") or next(iter(units.values()), [])
        by_end: Dict[str, dict] = {}
        for x in arr:
            if x.get("form") not in _ANUAIS or x.get("val") is None or not _full_year(x):
                continue
            if as_of and (x.get("filed") or "") > as_of:
                continue
            end = x.get("end")
            if not end:
                continue
            prev = by_end.get(end)
            if prev is None or (x.get("filed", "") > prev.get("filed", "")):
                by_end[end] = x
        if not by_end:
            continue
        per_concept[c] = by_end
        mx = max(by_end)
        if mx > best_end:
            best_end, best_c = mx, c
    if not best_c:
        return []
    by_end = per_concept[best_c]
    ends = sorted(by_end)[-n:]
    return [(e, float(by_end[e]["val"])) for e in ends]


def perfil_us(ticker: str, force: bool = False) -> Dict:
    """Perfil do emissor (submissions API): nome, SIC, descrição do setor, país."""
    hit = cik_for_ticker(ticker)
    if not hit:
        return {"erro": "ticker não encontrado"}
    cik, nome = hit
    cik10 = str(cik).zfill(10)
    sub = _get(_SUBM_URL.format(cik10=cik10), f"subm_{cik10}.json",
               ttl_h=0.0 if force else 168.0)
    return {"cik": cik, "nome": sub.get("name") or nome,
            "sic": sub.get("sic", ""), "sic_desc": sub.get("sicDescription", ""),
            "pais": sub.get("stateOfIncorporation", ""),
            "exchange": (sub.get("exchanges") or [None])[0]}


def _resolve(facts: dict, campos: List[str], as_of: Optional[str] = None
             ) -> Tuple[Optional[float], Optional[dict]]:
    """Escolhe, entre os conceitos candidatos, o fato anual de período MAIS RECENTE.

    Crucial porque empresas migram de tag XBRL ao longo do tempo (ex.: Apple trocou
    `Revenues` por `RevenueFromContractWithCustomerExcludingAssessedTax` em 2018) — o
    1º conceito com dados pode estar congelado em anos antigos. Em empate de período,
    mantém a prioridade da lista (`>` estrito favorece o conceito avaliado antes)."""
    best: Optional[Tuple[str, float, dict]] = None
    for c in campos:
        node = _facts_node(facts, c)
        rec = _latest_annual(node, as_of)
        if rec is None or rec.get("val") is None:
            continue
        end = rec.get("end", "")
        if best is None or end > best[0]:
            best = (end, float(rec["val"]),
                    {"concept": c, "end": end, "filed": rec.get("filed"),
                     "form": rec.get("form")})
    if best:
        return best[1], best[2]
    return None, None


# ─── API principal ────────────────────────────────────────────────────────────

def fundamentos_us(ticker: str, as_of: Optional[str] = None,
                   force: bool = False) -> Dict:
    """Fundamentos anuais (USD) de um ticker US a partir do SEC companyfacts.

    as_of (YYYY-MM-DD) restringe ao que estava público nessa data (point-in-time).
    Retorna dict com os campos internos + metadados (taxonomia, filed dates).
    """
    ticker = ticker.strip().upper()
    hit = cik_for_ticker(ticker)
    if not hit:
        return {"ticker": ticker, "erro": "ticker não encontrado na SEC"}
    cik, nome = hit
    cik10 = str(cik).zfill(10)
    facts = _get(_FACTS_URL.format(cik10=cik10), f"facts_{cik10}.json",
                 ttl_h=0.0 if force else 24.0)
    taxonomias = list(facts.get("facts", {}).keys())

    out: Dict = {"ticker": ticker, "cik": cik, "nome": facts.get("entityName") or nome,
                 "taxonomias": taxonomias,
                 "ifrs": "ifrs-full" in taxonomias and "us-gaap" not in taxonomias,
                 "campos": {}, "fontes": {}, "series": {}, "erro": None}
    for campo, cands in _CONCEITOS.items():
        val, meta = _resolve(facts, cands, as_of)
        if val is not None:
            out["campos"][campo] = val
            out["fontes"][campo] = meta
    # Série anual (histórico) p/ crescimento/score — campos que importam
    for campo in ("receita", "ebit", "lucro_liq", "pl"):
        s = _annual_series(facts, _CONCEITOS[campo], as_of)
        if s:
            out["series"][campo] = s

    # Ações em circulação (dei, instantâneo) com fallback para média ponderada
    sh, sh_meta = _resolve(facts, _CONCEITOS_SHARES, as_of)
    if sh is None:
        sh, sh_meta = _resolve(facts, _CONCEITOS_SHARES_FALLBACK, as_of)
    if sh is not None:
        out["campos"]["shares"] = sh
        out["fontes"]["shares"] = sh_meta

    # Derivados convenientes
    c = out["campos"]
    if c.get("receita") and c.get("ebit") is not None:
        c["margem_ebit"] = c["ebit"] / c["receita"]
    if c.get("lucro_liq") is not None and c.get("pl"):
        c["roe"] = c["lucro_liq"] / c["pl"]
    nd = None
    if any(k in c for k in ("divida_cp", "divida_lp", "caixa")):
        nd = (c.get("divida_cp", 0.0) + c.get("divida_lp", 0.0) - c.get("caixa", 0.0))
        c["net_debt"] = nd
    return out


if __name__ == "__main__":
    import sys
    tks = sys.argv[1:] or ["AAPL", "MSFT", "JPM", "NU"]
    for tk in tks:
        f = fundamentos_us(tk)
        if f.get("erro"):
            print(f"{tk}: {f['erro']}"); continue
        c = f["campos"]
        def bn(k):
            v = c.get(k)
            return f"{v/1e9:>9,.1f}" if v is not None else "      —"
        print(f"\n{f['nome']} ({tk}) · {'IFRS' if f['ifrs'] else 'US-GAAP'} · CIK {f['cik']}")
        print(f"  Receita {bn('receita')}  EBIT {bn('ebit')}  Lucro {bn('lucro_liq')}  (US$ bi)")
        print(f"  Ativo   {bn('ativo')}  PL   {bn('pl')}  Caixa {bn('caixa')}")
        print(f"  D&A {bn('da')}  Capex {bn('capex')}  NetDebt {bn('net_debt')}")
        roe = c.get("roe"); mg = c.get("margem_ebit"); sh = c.get("shares")
        print(f"  ROE {roe*100:.1f}%" if roe else "  ROE —",
              f"| Mrg EBIT {mg*100:.1f}%" if mg else "| Mrg —",
              f"| Ações {sh/1e6:,.0f} mi" if sh else "| Ações —")
        rec = f["fontes"].get("receita", {})
        print(f"  fonte receita: {rec.get('concept')} (fim {rec.get('end')}, divulgado {rec.get('filed')}, {rec.get('form')})")
