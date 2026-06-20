"""
Backtest point-in-time do modelo do dashboard nos EUA (S&P 500):
    MOMENTUM OPERACIONAL (A) + MOMENTUM TÉCNICO (B) + VALUATION NÃO-CARO

Análogo ao factor_study.py (Brasil), com fontes americanas e SEM look-ahead:
  · A operacional — score A1-A5 (indicadores_us._score_operacional_us) sobre a série
    anual de receita/EBIT filtrada pela DATA DE DIVULGAÇÃO real (`filed` ≤ T).
  · B técnico — score B1-B3 (momentum_tecnico) sobre a série de preço TRUNCADA em T
    (proximidade máx 52s, momentum 12-1, estrutura de médias).
  · Valuation não-caro — earnings yield (lucro LTM as_of T ÷ market cap em T).
Em cada mês T: ranqueia o combo em quartis, mede o retorno forward 1m, agrega vs S&P.
Reporta também A+B isolado e valor isolado, para ver se o combo soma (como no BR).

    python us_backtest.py --range 5y
"""
from __future__ import annotations
import argparse, bisect, csv, dataclasses, io, math, statistics as st, sys, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sec_client import cik_for_ticker, _get, _FACTS_URL, _full_year
from price_client import load_prices, PriceSeries, _BENCHMARK_KEY
from indicadores_us import _score_operacional_us
from momentum_tecnico import calcular_score_tecnico

_CONC = {
    "receita": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenue"],
    "ebit": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities", "OperatingProfitLoss"],
    "lucro_liq": ["NetIncomeLoss", "ProfitLoss"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndOther", "DepreciationAndAmortization"],
}
_SHARES = ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
           "WeightedAverageNumberOfDilutedSharesOutstanding"]


def _node(facts, concept):
    f = facts.get("facts", {})
    for tax in ("us-gaap", "ifrs-full", "dei"):
        n = f.get(tax, {}).get(concept)
        if n:
            return n
    return None


def _pit(facts, concepts) -> List[Tuple[str, str, float]]:
    by_end: Dict[str, Tuple[str, str, float]] = {}
    for c in concepts:
        n = _node(facts, c)
        if not n:
            continue
        arr = n.get("units", {}).get("USD") or n.get("units", {}).get("shares") or []
        for x in arr:
            if x.get("form") not in ("10-K", "20-F", "40-F") or x.get("val") is None or not _full_year(x):
                continue
            end, filed = x.get("end"), x.get("filed")
            if end and filed and (end not in by_end or filed > by_end[end][1]):
                by_end[end] = (end, filed, float(x["val"]))
    return sorted(by_end.values(), key=lambda t: t[1])


def _asof_list(series, T):
    """[(end,val)] anuais já divulgados em T, em ordem cronológica de período."""
    cand = sorted((x for x in series if x[1] <= T), key=lambda x: x[0])
    return [(e, v) for e, _, v in cand]


def _asof(series, T):
    lst = _asof_list(series, T)
    return lst[-1][1] if lst else None


def _truncar(s: PriceSeries, T: str) -> Optional[PriceSeries]:
    i = bisect.bisect_right(s.dates, T)
    if i < 220:
        return None
    return dataclasses.replace(s, dates=s.dates[:i], close=s.close[:i],
                               raw_close=s.raw_close[:i], volume=s.volume[:i],
                               week52_high=None, week52_low=None)


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(order):
        r[i] = pos / (len(xs) - 1) if len(xs) > 1 else 0.5
    return r


def _spearman(a, b):
    ra, rb = _ranks(a), _ranks(b); n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in ra); vb = sum((x - mb) ** 2 for x in rb)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def _tstat(xs):
    n = len(xs)
    sd = st.pstdev(xs) * math.sqrt(n / (n - 1)) if n > 1 else 0
    return (sum(xs) / n) / (sd / math.sqrt(n)) if sd > 0 else 0.0


def _quartis_ret(fac, fwd):
    order = sorted(range(len(fac)), key=lambda i: fac[i])
    cut = [round(len(order) * q / 4) for q in range(5)]
    med = []
    for q in range(4):
        grp = order[cut[q]:cut[q + 1]]
        med.append(sum(fwd[i] for i in grp) / len(grp) if grp else 0.0)
    return med


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="5y")
    ap.add_argument("--nmin", type=int, default=100)
    args = ap.parse_args()
    t0 = time.time()

    uni = [r["ticker"] for r in csv.DictReader(open("empresas_us.csv", encoding="utf-8"))
           if r.get("indice") == "500"]
    print(f"[SEC] Fundamentos PIT de {len(uni)} ações S&P 500…", file=sys.stderr)
    fund: Dict[str, dict] = {}
    for i, tk in enumerate(uni, 1):
        print(f"\r  {i}/{len(uni)} {tk:6}", end="", file=sys.stderr, flush=True)
        hit = cik_for_ticker(tk)
        if not hit:
            continue
        try:
            facts = _get(_FACTS_URL.format(cik10=str(hit[0]).zfill(10)), f"facts_{str(hit[0]).zfill(10)}.json")
            d = {k: _pit(facts, _CONC[k]) for k in _CONC}
            sh = _pit(facts, _SHARES)
            d["shares"] = sh[-1][2] if sh else None
            fund[tk] = d
        except Exception:
            continue
    print(f"\n  {len(fund)} com fundamentos", file=sys.stderr)

    print(f"[PREÇOS] Yahoo US range={args.range}…", file=sys.stderr)
    px = load_prices([{"ticker_b3": tk, "cd_cvm": "", "nome": tk} for tk in fund],
                     cache_dir=Path(f"_cache_us_bt_{args.range}"), range_=args.range,
                     mercado="US", benchmark_symbol="^GSPC")
    bench = px.get(_BENCHMARK_KEY)
    bd, bc = bench.dates, bench.raw_close
    monthly = {}
    for i, dte in enumerate(bd):
        monthly[dte[:7]] = i
    months = sorted(monthly); idx = [monthly[m] for m in months]

    def pat(s, T):
        j = bisect.bisect_right(s.dates, T) - 1
        return s.raw_close[j] if j >= 0 else None

    qAB = [[] for _ in range(4)]; qVAL = [[] for _ in range(4)]; qCOMBO = [[] for _ in range(4)]
    sAB = []; sVAL = []; sCOMBO = []; icC = []; bret = []
    tec_cache: Dict[Tuple[str, int], Optional[float]] = {}

    for k in range(12, len(months) - 1):
        T, Tn = bd[idx[k]], bd[idx[k + 1]]
        bT = _truncar(bench, T)
        ib0, ib1 = pat(bench, T), pat(bench, Tn)
        bret.append(ib1 / ib0 - 1 if (ib0 and ib1 and ib0 > 0) else 0.0)
        A = []; B = []; VAL = []; FWD = []
        for tk, fd in fund.items():
            s = px.get(tk)
            if not (s and s.ok):
                continue
            p0, pn = pat(s, T), pat(s, Tn)
            if not (p0 and pn and p0 > 0):
                continue
            # A operacional PIT
            rec = _asof_list(fd["receita"], T); ebt = _asof_list(fd["ebit"], T)
            if len(rec) < 2:
                continue
            ebit_at = ebt[-1][1] if ebt else None
            rec_at = rec[-1][1]
            campos = {"margem_ebit": (ebit_at / rec_at) if (ebit_at and rec_at) else None,
                      "ebit": ebit_at, "da": _asof(fd["da"], T), "net_debt": None}
            serie = {"receita": rec, "ebit": ebt}
            a = _score_operacional_us(campos, serie).get("score_operacional")
            # B técnico PIT (cacheia por nº de pregões disponíveis)
            sig = (tk, bisect.bisect_right(s.dates, T))
            b = tec_cache.get(sig, "x")
            if b == "x":
                sT = _truncar(s, T)
                b = (calcular_score_tecnico(sT, bT).get("score_tecnico") if sT else None) if bT else None
                tec_cache[sig] = b
            # valuation não-caro: earnings yield as_of T
            ni = _asof(fd["lucro_liq"], T); sh = fd.get("shares")
            ey = (ni / (p0 * sh)) if (ni is not None and sh) else None
            if a is None or b is None or ey is None:
                continue
            A.append(a); B.append(b); VAL.append(ey); FWD.append(pn / p0 - 1)
        if len(FWD) < args.nmin:
            continue
        rA = _ranks(A); rB = _ranks(B); rV = _ranks(VAL)
        ab = [(rA[i] + rB[i]) / 2 for i in range(len(A))]          # A+B (momentum op+téc)
        combo = [(rA[i] + rB[i] + rV[i]) / 3 for i in range(len(A))]  # A+B+valuation
        for fac, qd, sd_ in ((ab, qAB, sAB), (VAL, qVAL, sVAL), (combo, qCOMBO, sCOMBO)):
            med = _quartis_ret(fac, FWD)
            for q in range(4):
                qd[q].append(med[q])
            sd_.append(med[3] - med[0])
        icC.append(_spearman(combo, FWD))

    n = len(sCOMBO)
    bm = sum(bret[:n]) / n if n else 0.0
    def ann(m): return ((1 + m) ** 12 - 1) * 100

    print(f"\n{'='*66}")
    print(f"Backtest US point-in-time · S&P 500 · {len(fund)} ações · {n} meses")
    print(f"Modelo: Momentum Operacional (A) + Técnico (B) + Valuation não-caro")
    print(f"{'='*66}")
    print(f"S&P 500 (^GSPC): {bm*100:+.2f}%/mês ({ann(bm):+.1f}%/ano)\n")
    def linha(nome, q, s):
        qm = [sum(x) / len(x) for x in q]
        print(f"  {nome:24} Q1 {qm[0]*100:+.2f}  Q4 {qm[3]*100:+.2f}  |  Q4-Q1 {ann(sum(s)/len(s)):+.1f}%/ano  t={_tstat(s):.1f}")
        return qm[3]
    q4_ab = linha("A+B (momentum op+téc)", qAB, sAB)
    q4_v  = linha("Valuation (não-caro)", qVAL, sVAL)
    q4_c  = linha("COMBO A+B+Valuation", qCOMBO, sCOMBO)
    print(f"\nIC do combo: {sum(icC)/n:.3f} · hit {sum(1 for x in sCOMBO if x>0)/n*100:.0f}%")
    print(f"Top quartil do COMBO: {ann(q4_c):+.1f}%/ano vs S&P {ann(bm):+.1f}%/ano "
          f"→ {'BATE' if q4_c>bm else 'PERDE'} ({(q4_c-bm)*100:+.2f} pp/mês)")
    print(f"\n✓ {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
