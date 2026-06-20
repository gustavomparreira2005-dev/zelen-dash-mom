"""
Backtest point-in-time da estratégia de Momentum — Zelen Invest.

Em cada rebalanceamento mensal, recalcula o score combinado (A operacional +
B técnico) usando SOMENTE a informação disponível naquela data:
  · Score A: emp.as_of(T) → série CVM filtrada aos balanços já divulgados (DT_RECEB ≤ T)
  · Score B: série de preços truncada em T (sem week52 do futuro)
Seleciona o Top-N (peso igual), mantém até o próximo rebalanceamento e compara
o retorno acumulado da carteira com o Ibovespa.

Caveats:
  · Survivorship bias — universo = empresas_lista.csv atual (deslistadas ausentes).
  · Sharpe usa rf=0 (não desconta CDI).
  · Custos de transação não modelados.

Uso:
    python backtest.py                 # range 5y, anos_dfp 7, Top 10, mensal
    python backtest.py --top 15 --range 3y
"""

from __future__ import annotations

import argparse
import bisect
import io
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

R = "\033[0m"; B = "\033[1m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"


def tag(label: str, color: str = C) -> str:
    return f"{color}{B}[{label}]{R}"


def step(label: str, msg: str) -> None:
    print(f"{tag(label)}  {msg}", flush=True)


# ─── Preço point-in-time ──────────────────────────────────────────────────────

class _PxLookup:
    """Acesso O(log n) ao fechamento ajustado mais recente até uma data."""

    def __init__(self, dates: List[str], close: List[float]):
        self.dates = dates
        self.close = close

    def asof(self, dt: str) -> Optional[float]:
        i = bisect.bisect_right(self.dates, dt) - 1
        return self.close[i] if i >= 0 else None

    def n_ate(self, dt: str) -> int:
        return bisect.bisect_right(self.dates, dt)


class _LiqLookup:
    """Liquidez (R$/dia) point-in-time: mediana de preço×volume nos últimos `win` pregões."""

    def __init__(self, dates: List[str], raw_close: List[float], volume: List[float],
                 win: int = 42):
        self.dates = dates
        self.fin = [(raw_close[i] or 0.0) * (volume[i] or 0.0) for i in range(len(dates))]
        self.win = win

    def asof(self, dt: str) -> Optional[float]:
        import statistics
        i = bisect.bisect_right(self.dates, dt)
        if i <= 0:
            return None
        janela = [v for v in self.fin[max(0, i - self.win): i] if v > 0]
        return statistics.median(janela) if janela else None


def _truncar_series(series, dt: str):
    """Cópia da PriceSeries com dados até `dt` (inclusive) e week52 zerados."""
    from price_client import PriceSeries
    i = bisect.bisect_right(series.dates, dt)
    if i <= 0:
        return None
    return PriceSeries(
        ticker=series.ticker, symbol=series.symbol, cd_cvm=series.cd_cvm,
        nome=series.nome,
        dates=series.dates[:i], close=series.close[:i],
        raw_close=series.raw_close[:i], volume=series.volume[:i],
        market_price=None, week52_high=None, week52_low=None,
        currency=series.currency,
    )


# ─── Datas de rebalanceamento ─────────────────────────────────────────────────

def _datas_rebalance(bench_dates: List[str], warmup: int = 252) -> List[str]:
    """Último pregão de cada mês, a partir de quando há `warmup` pregões de história."""
    if len(bench_dates) <= warmup:
        return []
    fim_de_mes: Dict[str, str] = {}      # "YYYY-MM" → última data do mês
    for d in bench_dates[warmup:]:
        fim_de_mes[d[:7]] = d
    return sorted(fim_de_mes.values())


# ─── Métricas ─────────────────────────────────────────────────────────────────

def _metricas(rets: List[float], equity: List[float]) -> Dict:
    import math
    n = len(rets)
    if n == 0:
        return {}
    total = equity[-1] - 1.0
    cagr = equity[-1] ** (12.0 / n) - 1.0
    media = sum(rets) / n
    var = sum((r - media) ** 2 for r in rets) / n if n > 1 else 0.0
    vol_anual = math.sqrt(var) * math.sqrt(12)
    sharpe = (media * 12) / vol_anual if vol_anual > 0 else 0.0
    pico = equity[0]; maxdd = 0.0
    for v in equity:
        pico = max(pico, v)
        maxdd = min(maxdd, v / pico - 1.0)
    return {
        "n_meses": n, "total": total, "cagr": cagr,
        "vol_anual": vol_anual, "sharpe": sharpe, "max_dd": maxdd,
    }


def _beta_alpha(strat: List[float], ibov: List[float]) -> Tuple[float, float]:
    n = len(strat)
    if n < 2:
        return 0.0, 0.0
    ms = sum(strat) / n; mi = sum(ibov) / n
    cov = sum((strat[k] - ms) * (ibov[k] - mi) for k in range(n)) / n
    var_i = sum((ibov[k] - mi) ** 2 for k in range(n)) / n
    beta = cov / var_i if var_i > 0 else 0.0
    alpha_mensal = ms - beta * mi
    return beta, alpha_mensal * 12


# ─── Motor ────────────────────────────────────────────────────────────────────

_SINAL_LABEL = {"ab": "A+B (oper.+técn.)", "a": "A (operacional)", "b": "B (técnico)"}


def rodar_backtest(top_n: int = 10, range_: str = "5y", anos_dfp: int = 7,
                   no_cache: bool = False, sinal: str = "ab",
                   liq_min: float = 0.0, max_dl_ebitda: Optional[float] = None,
                   exclude: Optional[set] = None, band: float = 1.0,
                   max_ev_ebitda: Optional[float] = None) -> Dict:
    t0 = time.time()
    from cvm_client import load_companies_bulk
    from indicadores_empresas import calcular_indicadores, calcular_serie_trimestral
    from momentum_operacional import calcular_score_operacional
    from momentum_tecnico import calcular_score_tecnico
    from price_client import load_prices, _BENCHMARK_KEY
    import csv as _csv

    # ── Universo elegível ─────────────────────────────────────────────────────
    lista_path = Path("empresas_lista.csv")
    with lista_path.open("r", encoding="utf-8", newline="") as f:
        empresa_list = [e for e in _csv.DictReader(f)
                        if (e.get("ticker_b3") or "").strip()
                        and (e.get("tp_merc") or "").strip().upper() == "BOLSA"
                        and (e.get("sit_cvm") or "").strip().upper() != "CANCELADA"
                        and "RECUPER" not in (e.get("sit_emissor") or "").upper()
                        and "LIQUID" not in (e.get("sit_emissor") or "").upper()]
    step("CVM", f"Carregando {len(empresa_list)} ações (DFP: {anos_dfp}a)…")
    empresas = load_companies_bulk(empresa_list, anos_dfp=anos_dfp,
                                   force_download=no_cache)

    # mapa cd_cvm → ticker_b3 e ticker_b3 → CNPJ (para ações em circulação)
    cd_tb3 = {(_norm := str(e["cd_cvm"]).lstrip("0")): (e.get("ticker_b3") or "").strip().upper()
              for e in empresa_list}
    tb3_cnpj = {(e.get("ticker_b3") or "").strip().upper():
                "".join(c for c in (e.get("cnpj") or "") if c.isdigit())
                for e in empresa_list if (e.get("ticker_b3") or "").strip()}

    # Histórico de ações em circulação (FRE) — habilita EV/EBITDA point-in-time
    shares_lk = None
    if max_ev_ebitda is not None:
        from acoes_circulacao import load_shares
        from datetime import datetime as _dt
        anos_fre = list(range(2019, _dt.now().year + 1))
        step("FRE", "Carregando histórico de ações em circulação…")
        shares_lk = load_shares(anos_fre, force_download=no_cache)

    # ── Preços ────────────────────────────────────────────────────────────────
    tb3_list = [{"ticker_b3": tb3, "cd_cvm": cd, "nome": ""}
                for cd, tb3 in cd_tb3.items() if tb3]
    # Cache de preços separado (carimbado por range) — o cache do screener é 2y;
    # o backtest precisa do range completo sem colidir.
    step("PREÇOS", f"Baixando histórico (Yahoo, range={range_})…")
    series = load_prices(tb3_list, cache_dir=Path(f"_cache_bt_{range_}"),
                         range_=range_, force_download=no_cache)
    bench = series.get(_BENCHMARK_KEY)
    if not bench or not bench.ok:
        raise RuntimeError("benchmark Ibovespa indisponível")
    bench_px = _PxLookup(bench.dates, bench.close)

    # Lookups de preço (ajustado p/ retorno e bruto p/ market cap) e liquidez
    px:    Dict[str, _PxLookup]  = {}
    rawpx: Dict[str, _PxLookup]  = {}
    liq:   Dict[str, _LiqLookup] = {}
    for cd, tb3 in cd_tb3.items():
        s = series.get(tb3)
        if s and s.ok:
            px[tb3]    = _PxLookup(s.dates, s.close)
            rawpx[tb3] = _PxLookup(s.dates, s.raw_close)
            liq[tb3]   = _LiqLookup(s.dates, s.raw_close, s.volume)

    datas = _datas_rebalance(bench.dates)
    if len(datas) < 2:
        raise RuntimeError("histórico de preços insuficiente para rebalancear")
    step("BACKTEST", f"{len(datas)-1} rebalanceamentos mensais "
                     f"({datas[0]} → {datas[-1]}), Top {top_n} · "
                     f"sinal {_SINAL_LABEL.get(sinal, sinal)}…")

    # empresa por ticker_b3 (para as_of)
    emp_por_tb3: Dict[str, object] = {}
    for cd, emp in empresas.items():
        tb3 = cd_tb3.get(str(cd).lstrip("0"))
        if tb3:
            emp_por_tb3[tb3] = emp

    # ── Loop de rebalanceamento ───────────────────────────────────────────────
    historico: List[Dict] = []
    strat_rets: List[float] = []
    ibov_rets: List[float] = []
    # Atribuição por ação: quantas vezes segurada, retorno fwd e excesso vs IBOV
    contrib: Dict[str, Dict[str, float]] = {}
    n_filtrados_liq = 0
    prev_holdings: List[str] = []      # histerese: carteira do mês anterior

    for k in range(len(datas) - 1):
        T, T_next = datas[k], datas[k + 1]
        bar_n = int(28 * (k + 1) / (len(datas) - 1))
        print(f"\r  [{'█'*bar_n}{'░'*(28-bar_n)}] {k+1}/{len(datas)-1}  {T}   ",
              end="", file=sys.stderr, flush=True)

        scores: List[Tuple[str, int, int, int]] = []   # (tb3, métrica, A, B)
        for tb3, emp in emp_por_tb3.items():
            if tb3 not in px or px[tb3].asof(T) is None:
                continue   # precisa ser investável (ter preço) em T

            # Exclusão manual (blocklist)
            if exclude and tb3 in exclude:
                continue

            # Filtro de liquidez point-in-time
            if liq_min > 0:
                lv = liq[tb3].asof(T)
                if lv is None or lv < liq_min:
                    n_filtrados_liq += 1
                    continue

            # Score operacional as-of-T (sempre, exceto sinal puramente técnico)
            op = None
            if sinal != "b":
                empT = emp.as_of(T)
                res = calcular_indicadores(empT)
                serie_q = calcular_serie_trimestral(empT)
                op = calcular_score_operacional(res, serie_q).get("score_operacional")

                # Trava de alavancagem: DL/EBITDA dentro do teto (point-in-time)
                if max_dl_ebitda is not None:
                    dl = (res.get("ind") or {}).get("dl_ebitda")
                    if dl is None or dl > max_dl_ebitda:
                        continue

                # Trava de valuation: EV/EBITDA LTM as-of-T (= (MktCap+DívLíq)/EBITDA_LTM)
                if max_ev_ebitda is not None:
                    ev_eb = None
                    cnpj = tb3_cnpj.get(tb3)
                    sh = shares_lk.asof(cnpj, T) if (shares_lk and cnpj) else None
                    rp = rawpx[tb3].asof(T)
                    cb = res.get("campos_brutos") or {}
                    ebit, da = cb.get("ebit"), cb.get("da")
                    ebitda_ltm = (ebit + abs(da)) if (ebit is not None and da is not None) else None
                    if sh and rp and ebitda_ltm and ebitda_ltm > 0:
                        nd = (cb.get("divida_cp") or 0.0) + (cb.get("divida_lp") or 0.0) - (cb.get("caixa") or 0.0)
                        ev_eb = (sh * rp + nd) / ebitda_ltm
                    # Exclui caro, EBITDA negativo ou sem dado de ações
                    if ev_eb is None or ev_eb < 0 or ev_eb > max_ev_ebitda:
                        continue

            # Score técnico (só quando o sinal o usa)
            tec = None
            if sinal != "a":
                sT = _truncar_series(series[tb3], T)
                if sT is None or len(sT.close) < 200:
                    continue
                tec = calcular_score_tecnico(sT).get("score_tecnico")

            # Métrica de ranqueamento conforme o sinal escolhido
            if sinal == "a":
                if op is None:
                    continue
                metrica = op
            elif sinal == "b":
                if tec is None:
                    continue
                metrica = tec
            else:
                if op is None or tec is None:
                    continue
                metrica = min(op + tec, 100)

            scores.append((tb3, metrica, op or 0, tec or 0))

        scores.sort(key=lambda x: -x[1])

        # Seleção com histerese: mantém posições do mês anterior enquanto continuarem
        # dentro do top (band×N); preenche vagas com os melhores ainda não detidos.
        # band=1 → equivale ao Top-N puro.
        if band > 1.0 and prev_holdings:
            corte = int(band * top_n)
            rank = {tup[0]: i for i, tup in enumerate(scores)}
            por_tk = {tup[0]: tup for tup in scores}
            mantidos = [t for t in prev_holdings if t in rank and rank[t] < corte]
            sel = mantidos[:top_n]
            for tup in scores:
                if len(sel) >= top_n:
                    break
                if tup[0] not in sel:
                    sel.append(tup[0])
            carteira = [por_tk[t] for t in sel]
        else:
            carteira = scores[:top_n]
        prev_holdings = [t for t, *_ in carteira]

        ib0 = bench_px.asof(T); ib1 = bench_px.asof(T_next)
        ibov_ret = (ib1 / ib0 - 1.0) if (ib0 and ib1 and ib0 > 0) else 0.0

        # Retorno forward (T → T_next) — equal weight + atribuição por ação
        rs = []
        nomes = []
        for tb3, total, a, b in carteira:
            p0 = px[tb3].asof(T); p1 = px[tb3].asof(T_next)
            if not (p0 and p1 and p0 > 0):
                continue
            r = p1 / p0 - 1.0
            rs.append(r)
            nomes.append(tb3)
            c = contrib.setdefault(tb3, {"n": 0, "soma_ret": 0.0, "soma_exc": 0.0,
                                         "soma_liq": 0.0})
            c["n"] += 1
            c["soma_ret"] += r
            c["soma_exc"] += r - ibov_ret
            c["soma_liq"] += (liq[tb3].asof(T) or 0.0)
        if not rs:
            continue
        port_ret = sum(rs) / len(rs)

        strat_rets.append(port_ret)
        ibov_rets.append(ibov_ret)
        historico.append({
            "data": T, "data_fim": T_next,
            "holdings": nomes,
            "detalhe": [(t, tot, a, b) for t, tot, a, b in carteira],
            "port_ret": port_ret, "ibov_ret": ibov_ret,
        })

    print("", file=sys.stderr)

    # ── Equity curves ─────────────────────────────────────────────────────────
    eq_s = [1.0]; eq_i = [1.0]
    for r in strat_rets:
        eq_s.append(eq_s[-1] * (1 + r))
    for r in ibov_rets:
        eq_i.append(eq_i[-1] * (1 + r))

    m_s = _metricas(strat_rets, eq_s)
    m_i = _metricas(ibov_rets, eq_i)
    beta, alpha = _beta_alpha(strat_rets, ibov_rets)
    hit = (sum(1 for k in range(len(strat_rets)) if strat_rets[k] > ibov_rets[k])
           / len(strat_rets)) if strat_rets else 0.0

    # Turnover médio (fração da carteira trocada mês a mês)
    turnover = 0.0
    if len(historico) > 1:
        trocas = []
        for k in range(1, len(historico)):
            prev = set(historico[k - 1]["holdings"])
            cur = set(historico[k]["holdings"])
            if cur:
                trocas.append(len(cur - prev) / len(cur))
        turnover = sum(trocas) / len(trocas) if trocas else 0.0

    # ── Atribuição: traps e destaques ─────────────────────────────────────────
    attr = []
    for tb3, c in contrib.items():
        if c["n"] >= 3:                       # mínimo de presença para ser relevante
            attr.append({
                "ticker":  tb3,
                "n":       c["n"],
                "avg_ret": c["soma_ret"] / c["n"],
                "avg_exc": c["soma_exc"] / c["n"],
                "tot_exc": c["soma_exc"],      # arrasto/contribuição acumulada vs IBOV
                "liq_med": c["soma_liq"] / c["n"],
            })
    traps = sorted(attr, key=lambda a: a["tot_exc"])[:12]              # piores arrastos
    destaques = sorted(attr, key=lambda a: -a["tot_exc"])[:12]         # melhores
    import statistics as _st
    liq_carteira = [a["liq_med"] for a in attr if a["liq_med"] > 0]
    liq_mediana = _st.median(liq_carteira) if liq_carteira else 0.0

    print(f"\n  {B}Resultado ({m_s.get('n_meses',0)} meses){R}")
    print(f"  {'─'*52}")
    print(f"  {'':14}{'Estratégia':>14}{'Ibovespa':>14}")
    print(f"  {'Retorno total':14}{m_s['total']*100:>13.1f}%{m_i['total']*100:>13.1f}%")
    print(f"  {'CAGR':14}{m_s['cagr']*100:>13.1f}%{m_i['cagr']*100:>13.1f}%")
    print(f"  {'Vol. anual':14}{m_s['vol_anual']*100:>13.1f}%{m_i['vol_anual']*100:>13.1f}%")
    print(f"  {'Sharpe':14}{m_s['sharpe']:>14.2f}{m_i['sharpe']:>14.2f}")
    print(f"  {'Max DD':14}{m_s['max_dd']*100:>13.1f}%{m_i['max_dd']*100:>13.1f}%")
    print(f"  {'─'*52}")
    print(f"  Alpha (a.a.): {alpha*100:>5.1f}%   Beta: {beta:.2f}   "
          f"Meses>IBOV: {hit*100:.0f}%   Turnover: {turnover*100:.0f}%")
    print(f"  Liquidez mediana da carteira: R$ {liq_mediana/1e6:.1f} mi/dia"
          + (f"   ({n_filtrados_liq} exclusões por liq_min)" if liq_min > 0 else ""))

    print(f"\n  {B}🪤 Maiores armadilhas (pior excesso acumulado vs IBOV){R}")
    print(f"  {'Ticker':<8}{'vezes':>6}{'ret méd':>9}{'exc méd':>9}{'exc acum':>10}{'liq mi/d':>10}")
    for a in traps:
        print(f"  {a['ticker']:<8}{a['n']:>6}{a['avg_ret']*100:>8.1f}%"
              f"{a['avg_exc']*100:>8.1f}%{a['tot_exc']*100:>9.0f}%{a['liq_med']/1e6:>10.1f}")

    print(f"\n{G}{B}✓ Backtest em {time.time()-t0:.1f}s{R}")

    return {
        "top_n": top_n, "range": range_,
        "sinal": sinal, "sinal_label": _SINAL_LABEL.get(sinal, sinal),
        "historico": historico,
        "eq_s": eq_s, "eq_i": eq_i,
        "metr_s": m_s, "metr_i": m_i,
        "beta": beta, "alpha": alpha, "hit": hit, "turnover": turnover,
        "n_universo": len(emp_por_tb3),
        "liq_min": liq_min, "liq_mediana": liq_mediana,
        "n_filtrados_liq": n_filtrados_liq,
        "traps": traps, "destaques": destaques,
    }


def _norm_id(v: str) -> str:
    return str(v).lstrip("0")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Backtest de Momentum — Zelen Invest")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--range", default="5y")
    p.add_argument("--anos", type=int, default=7)
    p.add_argument("--sinal", choices=["ab", "a", "b"], default="ab",
                   help="ab=oper.+técn. (padrão) · a=só operacional · b=só técnico")
    p.add_argument("--liq-min", type=float, default=0.0,
                   help="liquidez mínima em R$/dia (ex.: 3e5 = R$300 mil/dia) para entrar na seleção")
    p.add_argument("--max-dl-ebitda", type=float, default=None,
                   help="teto de DL/EBITDA (ex.: 3.5) — exclui acima ou sem EBITDA")
    p.add_argument("--max-ev-ebitda", type=float, default=None,
                   help="teto de EV/EBITDA LTM point-in-time (ex.: 12) — exige histórico de ações (FRE)")
    p.add_argument("--exclude", nargs="*", default=[], metavar="TICKER",
                   help="blocklist de tickers a remover do universo")
    p.add_argument("--band", type=float, default=1.0,
                   help="histerese p/ reduzir turnover: mantém posições enquanto no top band×N (ex.: 2.0)")
    p.add_argument("--tag", default="", help="sufixo extra no nome do arquivo de saída")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    # Nome de saída por sinal/filtro (não sobrescreve variações)
    suf = args.sinal if args.sinal != "ab" else "ab"
    if args.liq_min > 0:
        suf += f"_liq{int(args.liq_min/1e3)}k"
    if args.max_dl_ebitda is not None:
        suf += f"_dl{str(args.max_dl_ebitda).replace('.','')}"
    if args.max_ev_ebitda is not None:
        suf += f"_ev{str(args.max_ev_ebitda).replace('.','')}"
    if args.band > 1.0:
        suf += f"_band{str(args.band).replace('.','')}"
    if args.tag:
        suf += f"_{args.tag}"
    output = args.output or (
        Path("relatorios") /
        ("backtest.html" if suf == "ab" else f"backtest_{suf}.html"))

    print(f"\n{C}{B}{'─'*60}{R}")
    print(f"{C}{B}  Backtest de Momentum · point-in-time · Zelen Invest{R}")
    print(f"{C}{B}{'─'*60}{R}\n")

    res = rodar_backtest(top_n=args.top, range_=args.range,
                         anos_dfp=args.anos, no_cache=args.no_cache,
                         sinal=args.sinal, liq_min=args.liq_min,
                         max_dl_ebitda=args.max_dl_ebitda,
                         exclude={t.strip().upper() for t in args.exclude},
                         band=args.band, max_ev_ebitda=args.max_ev_ebitda)

    from backtest_report import gerar_relatorio_backtest
    path = gerar_relatorio_backtest(res, output)
    abs_path = path.resolve()
    print(f"\n{tag('HTML')}  {abs_path}")
    print(f"  {C}URL:{R}  file:///" + str(abs_path).replace('\\', '/').replace(' ', '%20'))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
