"""
Estudo estatístico (backtest de fator) — cada indicador A1-A5 / B1-B4 gera valor?

Metodologia (estilo Alphalens / análise por quantis, cross-seccional point-in-time):
  Em cada rebalanceamento mensal T:
    1. Para cada ação investável, calcula os sub-scores (A1..A5, B1..B4) usando
       SÓ a informação disponível em T (CVM as_of via DT_RECEB; preços truncados).
    2. Para cada indicador, ranqueia as ações em QUARTIS (Q1=pior, Q4=melhor).
    3. Mede o retorno forward T→T_next (equal-weight) de cada quartil.
  Agrega sobre todos os T:
    · Retorno mensal médio por quartil (Q1..Q4).
    · Spread long-short Q4−Q1: média, t-stat, % de meses positivo (hit).
    · Information Coefficient (IC): correlação de Spearman fator×retorno fwd,
      média e IR (IC médio / desvio). É a medida mais robusta com scores discretos.
    · Monotonicidade: o retorno cresce de Q1→Q4?

Um indicador "gera valor" se: spread Q4−Q1 positivo e significante (t≳2), IC>0
consistente (IR≳0.3) e quartis aproximadamente monotônicos.

Fator TIR (valuation): em cada T, reconstrói a TIR do DCF de firma EXATAMENTE como o
dashboard (FCFF · Gordon · WACC bottom-up setorial · custo de dívida sintético), mas
100% point-in-time — fundamentos CVM as_of T, preço bruto e ações (FRE) truncados em
T, beta por regressão na janela até T, medianas setoriais cross-seccionais do próprio
T. Testa a hipótese central: TIR alta (barato) prevê retorno futuro? Também testa o
spread TIR−WACC (corrige o viés de WACC entre empresas). Cobertura começa só quando há
balanço CVM (~2020), por isso esses fatores rodam em menos meses que A/B.

Caveats: survivorship bias (universo = lista atual), rf=0, sem custos, fundamentos
CVM só a partir de ~2020 (anos_dfp). Uso:
    python factor_study.py                       # range 7y, anos 9, mensal
    python factor_study.py --range 5y --liq-min 3e5
"""

from __future__ import annotations

import argparse
import math
import statistics as st
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Importa do backtest no topo: ele já faz o wrap UTF-8 de sys.stdout (uma vez só).
# Reusa a máquina point-in-time (lookups de preço/liquidez, truncagem, datas).
from backtest import _PxLookup, _LiqLookup, _truncar_series, _datas_rebalance, step

R = "\033[0m"; B = "\033[1m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"

# Indicadores testados (chave no dict de score, rótulo, máx teórico de pontos)
FACTORS: List[Tuple[str, str, str]] = [
    ("a1", "A1 Nível crescimento",   "oper"),
    ("a2", "A2 Consistência",        "oper"),
    ("a3", "A3 Aceleração",          "oper"),
    ("a4", "A4 Qualidade do lucro",  "oper"),
    ("a5", "A5 Solidez financeira",  "oper"),
    ("b1", "B1 Prox. máx 52s",       "tec"),
    ("b2", "B2 Momentum 12-1",       "tec"),
    ("b3", "B3 Estrutura de médias", "tec"),
    ("A",  "A operacional (total)",  "comp"),
    ("B",  "B técnico (total)",      "comp"),
    ("AB", "A+B combinado",          "comp"),
    ("tir",   "TIR valuation",        "val"),
    ("spread", "TIR − WACC (spread)", "val"),
    ("a_tir",  "A + TIR",             "mix"),
    ("ab_tir", "A+B + TIR (tudo)",    "mix"),
    ("liq",    "Liquidez (R$/dia)",   "liqz"),
]


# ─── Estatística ──────────────────────────────────────────────────────────────

def _ranks(xs: List[float]) -> List[float]:
    """Ranks 1-based com média em empates (para Spearman)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: List[float], b: List[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def _spearman(f: List[float], r: List[float]) -> float:
    return _pearson(_ranks(f), _ranks(r))


def _pctrank(xs: List[float]) -> List[float]:
    """Percentil cross-seccional (0..1) — base para combinar fatores de unidades
    diferentes (score 0-100 vs TIR %). Empates recebem o rank médio."""
    n = len(xs)
    if n <= 1:
        return [0.5] * n
    r = _ranks(xs)
    return [(x - 1) / (n - 1) for x in r]


def _tstat(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    sd = st.pstdev(xs) * math.sqrt(n / (n - 1))  # amostral
    return (sum(xs) / n) / (sd / math.sqrt(n)) if sd > 0 else 0.0


def _quartis(vals: List[float]) -> List[List[int]]:
    """Divide índices em 4 quartis por posição de rank (Q1=menor fator)."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    cortes = [round(n * q / 4) for q in range(5)]
    return [order[cortes[q]:cortes[q + 1]] for q in range(4)]


# ─── TIR do valuation (point-in-time) ───────────────────────────────────────────
# Replica fielmente a metodologia do dashboard (main_acoes.py · valuation.py):
# DCF de firma · FCFF · perpetuidade de Gordon · WACC bottom-up setorial · custo de
# dívida sintético por alavancagem. Tudo as_of T (fundamentos CVM as_of, preço/ações
# truncados em T, beta por regressão na janela até T, medianas setoriais cross-secc.).
_WACC_RF, _WACC_ERP, _WACC_TAX = 0.105, 0.075, 0.34
_FADE = 0.50
_ANCHOR_LO, _ANCHOR_HI = 4.0, 14.0
_WACC_LO, _WACC_HI = 0.08, 0.18
_BETA_LO, _BETA_HI = 0.6, 1.8


def _rd_sintetico(nd: float, ebitda: float, rf: float = _WACC_RF) -> float:
    x = (nd / ebitda) if (ebitda and ebitda > 0) else 9.0
    spr = (0.010 if x < 0 else 0.015 if x < 1 else 0.020 if x < 2 else
           0.030 if x < 3 else 0.045 if x < 4 else 0.065 if x < 5 else 0.090)
    return rf + spr


def _clampf(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _median_or(xs: List[Optional[float]], default: float) -> float:
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else default


def _computar_tir(vins: Dict[str, Dict]) -> None:
    """Adiciona 'tir' e 'wacc' (in-place) a cada empresa elegível em `vins`,
    replicando os dois passes do valuation de produção (medianas setoriais → DCF)."""
    from valuation import calcular_valuation, premissas_default, calcular_wacc, Anchors

    by_setor: Dict[str, List[Dict]] = {}
    for v in vins.values():
        by_setor.setdefault(v["setor"], []).append(v)
    glob_ev = _median_or([v["ev_ebit"] for v in vins.values()
                          if v["ev_ebit"] and v["ev_ebit"] > 0], 8.0)
    glob_bu = _median_or([v["beta_u"] for v in vins.values()], 0.70)
    sec_ev = {s: _median_or([v["ev_ebit"] for v in g if v["ev_ebit"] and v["ev_ebit"] > 0], glob_ev)
              for s, g in by_setor.items()}
    sec_bu = {s: (_median_or([v["beta_u"] for v in g], glob_bu) if len(g) >= 3 else glob_bu)
              for s, g in by_setor.items()}

    for v in vins.values():
        anchor = _clampf(sec_ev.get(v["setor"], glob_ev), _ANCHOR_LO, _ANCHOR_HI)
        cur = v["ev_ebit"] if 0 < v["ev_ebit"] < 40 else anchor
        exit_mult = cur + _FADE * (anchor - cur)
        bu = sec_bu.get(v["setor"], glob_bu)
        beta_bottomup = bu * (1 + (1 - _WACC_TAX) * v["de"])
        beta_use = _clampf(0.5 * v["beta_blume"] + 0.5 * beta_bottomup, _BETA_LO, _BETA_HI)
        ebitda_emp = v["ebt"] + v["da"]
        rd_emp = _rd_sintetico(v["nd"], ebitda_emp)
        w = calcular_wacc(v["mkt_eq"], v["div_bruta"], beta_use,
                          rf=_WACC_RF, erp=_WACC_ERP, custo_divida=rd_emp, tax=_WACC_TAX)
        wacc = _clampf(w.wacc, _WACC_LO, _WACC_HI)
        roic_frac = (v["roic"] / 100.0) if v["roic"] is not None else None
        a = Anchors(ticker="", n_acoes=v["n_acoes"], preco=v["preco"], net_debt=v["nd"],
                    receita_ltm=v["rec"], ebit_ltm=v["ebt"], margem_ebit=v["ebt"] / v["rec"],
                    cagr_hist=v["cagr"], ev_ebit_atual=v["ev_ebit"], roic=roic_frac,
                    da_ltm=v["da"], capex_ltm=v["capex"], cogs_ltm=v["cogs"],
                    receber=v["receber"], estoque=v["estoque"], fornecedores=v["fornec"])
        p = premissas_default(a)
        p.ev_ebit_saida = round(exit_mult, 1)
        p.taxa_desconto = round(wacc, 4)
        p.tax = _WACC_TAX
        r = calcular_valuation(a, p)
        v["tir"] = r.get("tir")
        v["wacc"] = wacc


# ─── Estudo ────────────────────────────────────────────────────────────────────

def rodar_estudo(range_: str = "7y", anos_dfp: int = 9, no_cache: bool = False,
                 liq_min: float = 0.0, nmin: int = 20, diag_illiq: float = 0.0) -> Dict:
    t0 = time.time()
    from cvm_client import load_companies_bulk
    from indicadores_empresas import calcular_indicadores, calcular_serie_trimestral
    from momentum_operacional import calcular_score_operacional
    from momentum_tecnico import calcular_score_tecnico
    from price_client import load_prices, _BENCHMARK_KEY
    import csv as _csv

    lista_path = Path("empresas_lista.csv")
    with lista_path.open("r", encoding="utf-8", newline="") as f:
        empresa_list = [e for e in _csv.DictReader(f)
                        if (e.get("ticker_b3") or "").strip()
                        and (e.get("tp_merc") or "").strip().upper() == "BOLSA"
                        and (e.get("sit_cvm") or "").strip().upper() != "CANCELADA"
                        and "RECUPER" not in (e.get("sit_emissor") or "").upper()
                        and "LIQUID" not in (e.get("sit_emissor") or "").upper()]
    step("CVM", f"Carregando {len(empresa_list)} ações (DFP: {anos_dfp}a)…")
    empresas = load_companies_bulk(empresa_list, anos_dfp=anos_dfp, force_download=no_cache)

    cd_tb3 = {str(e["cd_cvm"]).lstrip("0"): (e.get("ticker_b3") or "").strip().upper()
              for e in empresa_list}

    tb3_list = [{"ticker_b3": tb3, "cd_cvm": cd, "nome": ""}
                for cd, tb3 in cd_tb3.items() if tb3]
    step("PREÇOS", f"Baixando histórico (Yahoo, range={range_})…")
    series = load_prices(tb3_list, cache_dir=Path(f"_cache_bt_{range_}"),
                         range_=range_, force_download=no_cache)
    bench = series.get(_BENCHMARK_KEY)
    if not bench or not bench.ok:
        raise RuntimeError("benchmark Ibovespa indisponível")
    bench_px = _PxLookup(bench.dates, bench.close)

    px: Dict[str, _PxLookup] = {}       # close ajustado → retornos forward
    pxraw: Dict[str, _PxLookup] = {}    # close BRUTO → preço real p/ market cap do valuation
    liq: Dict[str, _LiqLookup] = {}
    for cd, tb3 in cd_tb3.items():
        s = series.get(tb3)
        if s and s.ok:
            px[tb3] = _PxLookup(s.dates, s.close)
            pxraw[tb3] = _PxLookup(s.dates, s.raw_close)
            liq[tb3] = _LiqLookup(s.dates, s.raw_close, s.volume)

    # Ações em circulação point-in-time (FRE) e CNPJ por ticker — p/ o fator TIR.
    from acoes_circulacao import load_shares
    from valuation import calcular_beta
    from datetime import date as _date
    shares = load_shares(list(range(2019, _date.today().year + 1)), force_download=no_cache)
    cnpj_por_tb3 = {(e.get("ticker_b3") or "").strip().upper():
                    "".join(c for c in (e.get("cnpj") or "") if c.isdigit())
                    for e in empresa_list if (e.get("ticker_b3") or "").strip()}

    # Beta (Blume) por empresa — calculado UMA vez sobre a série inteira e reutilizado
    # em todos os meses. É um parâmetro estrutural (varia pouco no tempo); recalcular
    # por regressão a cada rebalanceamento dominava o custo (~17 mil regressões). O
    # leak point-in-time é desprezível e a alavancagem (D/E) continua sendo aplicada
    # mês a mês sobre este beta, então o WACC ainda responde ao preço corrente.
    beta_blume_emp: Dict[str, float] = {}
    for tb3 in pxraw:
        s = series.get(tb3)
        if s and s.ok and bench and bench.ok:
            _, ba, _ = calcular_beta(s.dates, s.close, bench.dates, bench.close)
            if ba is not None:
                beta_blume_emp[tb3] = ba

    datas = _datas_rebalance(bench.dates)
    if len(datas) < 6:
        raise RuntimeError("histórico insuficiente")

    emp_por_tb3: Dict[str, object] = {}
    for cd, emp in empresas.items():
        tb3 = cd_tb3.get(str(cd).lstrip("0"))
        if tb3:
            emp_por_tb3[tb3] = emp

    step("ESTUDO", f"{len(datas)-1} rebalanceamentos ({datas[0]} → {datas[-1]}), "
                   f"{len(FACTORS)} indicadores, quartis cross-seccionais…")

    # Acumuladores por fator
    keys = [k for k, _, _ in FACTORS]
    q_series: Dict[str, List[List[float]]] = {k: [] for k in keys}  # por data: [médiaQ1..Q4] retorno fwd
    qval_series: Dict[str, List[List[float]]] = {k: [] for k in keys}  # por data: [médiaQ1..Q4] do FATOR
    spread_series: Dict[str, List[float]] = {k: [] for k in keys}   # por data: Q4-Q1
    ic_series: Dict[str, List[float]] = {k: [] for k in keys}       # por data: IC
    bench_rets: List[float] = []
    n_obs_total = 0
    # Diagnóstico: nomes < diag_illiq que caem no Q4 da TIR (quem puxa o quartil barato)
    diag: Dict[str, Dict] = {}
    diag_q4_illiq = 0          # slots (mês,nome) do Q4-TIR abaixo do piso
    diag_q4_total = 0          # total de slots do Q4-TIR
    diag_sum_fwd_illiq = 0.0   # soma dos retornos fwd dos ilíquidos no Q4
    diag_sum_fwd_liq = 0.0     # soma dos retornos fwd dos líquidos no Q4
    nome_por_tb3 = {tb3: getattr(emp, "nome", "") for tb3, emp in emp_por_tb3.items()}

    # Cache: score operacional + âncoras do valuation. Mudam só quando um novo
    # balanço é divulgado (chave = assinatura de quantos balanços estão visíveis em T).
    op_cache: Dict[Tuple[str, int, int], Tuple[Dict, Optional[Dict]]] = {}

    for idx in range(len(datas) - 1):
        T, T_next = datas[idx], datas[idx + 1]
        bar = int(28 * (idx + 1) / (len(datas) - 1))
        print(f"\r  [{'█'*bar}{'░'*(28-bar)}] {idx+1}/{len(datas)-1}  {T}   ",
              end="", file=sys.stderr, flush=True)

        ib0, ib1 = bench_px.asof(T), bench_px.asof(T_next)
        bench_rets.append((ib1 / ib0 - 1.0) if (ib0 and ib1 and ib0 > 0) else 0.0)

        linhas: List[List] = []                # [valores_fator, ret_fwd] (mutável p/ injetar TIR)
        vins: Dict[str, Dict] = {}             # subconjunto elegível ao valuation (→ TIR)
        for tb3, emp in emp_por_tb3.items():
            if tb3 not in px or px[tb3].asof(T) is None:
                continue
            if liq_min > 0:
                lv = liq[tb3].asof(T)
                if lv is None or lv < liq_min:
                    continue
            p0, p1 = px[tb3].asof(T), px[tb3].asof(T_next)
            if not (p0 and p1 and p0 > 0):
                continue
            fwd = p1 / p0 - 1.0

            empT = emp.as_of(T)
            sig = (tb3, len(empT.anos_dfp), len(empT.trimestres_itr))
            cached = op_cache.get(sig)
            if cached is None:
                res = calcular_indicadores(empT)
                serie_q = calcular_serie_trimestral(empT)
                op = calcular_score_operacional(res, serie_q)
                cb = res.get("campos_brutos") or {}
                ind = res.get("ind") or {}
                hist_rec = [h for h in ((res.get("historico_brutos") or {}).get("receita") or [])
                            if h and h > 0]
                anc = {
                    "rec": (cb.get("receita") or 0.0) / 1e6,
                    "ebt": (cb.get("ebit") or 0.0) / 1e6,
                    "divida_cp": cb.get("divida_cp") or 0.0,
                    "divida_lp": cb.get("divida_lp") or 0.0,
                    "caixa": cb.get("caixa") or 0.0,
                    "roic": ind.get("roic"),
                    "da": abs(cb.get("da") or 0.0) / 1e6,
                    "capex": abs(cb.get("capex") or 0.0) / 1e6,
                    "cogs": abs(cb.get("custo_vendas") or 0.0) / 1e6,
                    "receber": (cb.get("contas_receber") or 0.0) / 1e6,
                    "estoque": (cb.get("estoques") or 0.0) / 1e6,
                    "fornec": (cb.get("fornecedores") or 0.0) / 1e6,
                    "setor": res.get("setor") or "",
                    "cagr": ((hist_rec[-1] / hist_rec[0]) ** (1 / (len(hist_rec) - 1)) - 1
                             if len(hist_rec) >= 2 else None),
                }
                op_cache[sig] = (op, anc)
            else:
                op, anc = cached
            if op.get("score_operacional") is None:
                continue

            sT = _truncar_series(series[tb3], T)
            if sT is None or len(sT.close) < 200:
                continue
            tec = calcular_score_tecnico(sT)
            if tec.get("score_tecnico") is None:
                continue

            vals = {
                "a1": op["a1"], "a2": op["a2"], "a3": op["a3"], "a4": op["a4"], "a5": op["a5"],
                "b1": tec["b1"], "b2": tec["b2"], "b3": tec["b3"],
                "A": op["score_operacional"], "B": tec["score_tecnico"],
                "AB": min(op["score_operacional"] + tec["score_tecnico"], 100),
                "tir": None, "spread": None, "a_tir": None, "ab_tir": None,
                "liq": (liq[tb3].asof(T) if tb3 in liq else None),  # vol. fin. médio R$/dia
            }
            linhas.append([vals, fwd, tb3])

            # ── Inputs do valuation (point-in-time): preço real, ações FRE, beta ──
            preco = pxraw[tb3].asof(T) if tb3 in pxraw else None
            rec, ebt = anc["rec"], anc["ebt"]
            if not (preco and preco > 0 and rec > 0 and ebt > 0):
                continue
            sh_raw = shares.asof(cnpj_por_tb3.get(tb3, ""), T)
            n_acoes = (sh_raw / 1e6) if sh_raw else 0.0
            if n_acoes <= 0.01:
                continue
            nd = (anc["divida_cp"] + anc["divida_lp"] - anc["caixa"]) / 1e6
            div_bruta = (anc["divida_cp"] + anc["divida_lp"]) / 1e6
            mkt_eq = preco * n_acoes
            ev_ebit = (mkt_eq + nd) / ebt
            beta_blume = beta_blume_emp.get(tb3, 1.0)   # pré-calculado (série inteira)
            de = _clampf(div_bruta / mkt_eq if mkt_eq > 0 else 0.0, 0.0, 5.0)
            beta_u = beta_blume / (1 + (1 - _WACC_TAX) * de)
            vins[tb3] = dict(rec=rec, ebt=ebt, nd=nd, div_bruta=div_bruta, preco=preco,
                             n_acoes=n_acoes, mkt_eq=mkt_eq, ev_ebit=ev_ebit, roic=anc["roic"],
                             cagr=anc["cagr"], beta_blume=beta_blume, beta_u=beta_u, de=de,
                             setor=anc["setor"], da=anc["da"], capex=anc["capex"], cogs=anc["cogs"],
                             receber=anc["receber"], estoque=anc["estoque"], fornec=anc["fornec"],
                             _vals=vals)

        if len(linhas) < nmin:
            continue
        n_obs_total += len(linhas)

        # TIR do valuation (dois passes: medianas setoriais → DCF por empresa)
        if vins:
            _computar_tir(vins)
            for v in vins.values():
                tir, wacc = v.get("tir"), v.get("wacc")
                v["_vals"]["tir"] = tir
                v["_vals"]["spread"] = (tir - wacc) if (tir is not None and wacc is not None) else None

            # Compostos com valuation: blend 50/50 de percentis cross-seccionais
            # (rank padroniza unidades). Calculado no conjunto com TIR válida.
            elig = [v for v in vins.values() if v.get("tir") is not None]
            if len(elig) >= nmin:
                pr_tir = _pctrank([v["tir"] for v in elig])
                pr_a   = _pctrank([v["_vals"]["A"] for v in elig])
                pr_ab  = _pctrank([v["_vals"]["AB"] for v in elig])
                for i, v in enumerate(elig):
                    v["_vals"]["a_tir"]  = 0.5 * pr_a[i]  + 0.5 * pr_tir[i]
                    v["_vals"]["ab_tir"] = 0.5 * pr_ab[i] + 0.5 * pr_tir[i]

        for k in keys:
            pares = [(lv[0][k], lv[1]) for lv in linhas if lv[0].get(k) is not None]
            if len(pares) < nmin:        # cobertura insuficiente p/ formar quartis neste mês
                continue
            fvals = [pp[0] for pp in pares]
            frets = [pp[1] for pp in pares]
            quart = _quartis(fvals)
            medias = [st.mean([frets[i] for i in q]) if q else 0.0 for q in quart]
            medias_fator = [st.mean([fvals[i] for i in q]) if q else 0.0 for q in quart]
            q_series[k].append(medias)
            qval_series[k].append(medias_fator)
            spread_series[k].append(medias[3] - medias[0])
            ic_series[k].append(_spearman(fvals, frets))

        # ── Diagnóstico: quem é ilíquido (< diag_illiq) dentro do Q4 da TIR ──────
        if diag_illiq > 0:
            trip = [(lv[0]["tir"], lv[1], lv[2], lv[0].get("liq"))
                    for lv in linhas if lv[0].get("tir") is not None]
            if len(trip) >= nmin:
                q4 = _quartis([x[0] for x in trip])[3]
                for i in q4:
                    tir_i, fwd_i, tb3_i, liq_i = trip[i]
                    diag_q4_total += 1
                    if liq_i is not None and liq_i < diag_illiq:
                        diag_q4_illiq += 1
                        diag_sum_fwd_illiq += fwd_i
                        d = diag.setdefault(tb3_i, {"n": 0, "sum_fwd": 0.0, "sum_tir": 0.0,
                                                    "max_fwd": -9.9, "max_mes": "", "min_liq": 9e18})
                        d["n"] += 1
                        d["sum_fwd"] += fwd_i
                        d["sum_tir"] += tir_i
                        d["min_liq"] = min(d["min_liq"], liq_i)
                        if fwd_i > d["max_fwd"]:
                            d["max_fwd"], d["max_mes"] = fwd_i, T
                    else:
                        diag_sum_fwd_liq += fwd_i

    print("", file=sys.stderr)

    n_meses = len(spread_series[keys[0]])
    bench_m = sum(bench_rets) / len(bench_rets) if bench_rets else 0.0

    resultados = []
    for k, label, grupo in FACTORS:
        qs = q_series[k]
        if not qs:
            continue
        qmean = [sum(m[q] for m in qs) / len(qs) for q in range(4)]   # média mensal por quartil
        sp = spread_series[k]
        ic = ic_series[k]
        spread_m = sum(sp) / len(sp)
        hit = sum(1 for x in sp if x > 0) / len(sp)
        mono = sum(1 for q in range(3) if qmean[q + 1] > qmean[q])     # passos crescentes (0-3)
        qv = qval_series[k]
        qvalmean = [sum(m[q] for m in qv) / len(qv) for q in range(4)] if qv else [0.0] * 4
        resultados.append({
            "key": k, "label": label, "grupo": grupo, "n_k": len(qs),
            "q": qmean, "qval": qvalmean,
            "spread_m": spread_m, "spread_ann": (1 + spread_m) ** 12 - 1,
            "t": _tstat(sp), "ic": sum(ic) / len(ic),
            "ic_ir": (sum(ic) / len(ic)) / st.pstdev(ic) if len(ic) > 1 and st.pstdev(ic) > 0 else 0.0,
            "hit": hit, "mono": mono,
        })

    diag_out = None
    if diag_illiq > 0:
        linhas_diag = []
        for tb3, d in diag.items():
            linhas_diag.append({
                "tb3": tb3, "nome": nome_por_tb3.get(tb3, ""), "n": d["n"],
                "avg_tir": d["sum_tir"] / d["n"], "avg_fwd": d["sum_fwd"] / d["n"],
                "sum_fwd": d["sum_fwd"], "max_fwd": d["max_fwd"], "max_mes": d["max_mes"],
                "min_liq": d["min_liq"],
            })
        linhas_diag.sort(key=lambda x: x["sum_fwd"], reverse=True)
        diag_out = {
            "floor": diag_illiq, "q4_total": diag_q4_total, "q4_illiq": diag_q4_illiq,
            "avg_fwd_illiq": (diag_sum_fwd_illiq / diag_q4_illiq) if diag_q4_illiq else 0.0,
            "avg_fwd_liq": (diag_sum_fwd_liq / (diag_q4_total - diag_q4_illiq))
                           if (diag_q4_total - diag_q4_illiq) else 0.0,
            "nomes": linhas_diag,
        }

    return {"range": range_, "anos_dfp": anos_dfp, "n_meses": n_meses,
            "n_obs": n_obs_total, "n_universo": len(emp_por_tb3),
            "bench_m": bench_m, "liq_min": liq_min,
            "datas": (datas[0], datas[-1]), "resultados": resultados,
            "diag": diag_out, "tempo": time.time() - t0}


def _imprimir(res: Dict) -> None:
    print(f"\n  {B}Estudo de fator por quartis — {res['n_meses']} meses "
          f"({res['datas'][0]} → {res['datas'][1]}){R}")
    print(f"  universo {res['n_universo']} ações · {res['n_obs']:,} observações ação-mês · "
          f"retorno IBOV médio {res['bench_m']*100:+.2f}%/mês"
          + (f" · liq_min R${res['liq_min']/1e3:.0f}k/dia" if res['liq_min'] > 0 else ""))
    print("  Retornos = forward mensal médio, equal-weight, por quartil (Q1=pior fator, Q4=melhor)")
    print(f"  {'─'*94}")
    hdr = (f"  {'Indicador':<22}{'Q1':>7}{'Q2':>7}{'Q3':>7}{'Q4':>7}"
           f"{'Q4-Q1aa':>9}{'t':>6}{'IC':>7}{'IC-IR':>7}{'hit':>6}{'mono':>6}")
    grupo_atual = None
    for r in res["resultados"]:
        if r["grupo"] != grupo_atual:
            grupo_atual = r["grupo"]
            nome = {"oper": "OPERACIONAL (A)", "tec": "TÉCNICO (B)", "comp": "COMPOSTOS",
                    "val": "VALUATION (DCF · point-in-time)",
                    "mix": "MOMENTUM × VALUATION (blend de percentis)",
                    "liqz": "LIQUIDEZ / TAMANHO (Q1=ilíquido · Q4=líquido)"}[grupo_atual]
            print(f"\n  {C}{B}{nome}{R}")
            print(hdr)
        q = [x * 100 for x in r["q"]]
        # destaque: gera valor se t>=2 e IC-IR>=0.3 e mono>=2
        bom = r["t"] >= 2.0 and r["ic_ir"] >= 0.3 and r["mono"] >= 2
        marca = f" {G}◄ gera valor{R}" if bom else (f" {Y}~{R}" if r["t"] >= 1.5 else "")
        # fatores com menos meses que o painel (ex.: TIR só após CVM ~2020) mostram cobertura
        cobertura = f" {Y}[{r['n_k']}m]{R}" if r["n_k"] < res["n_meses"] else ""
        print(f"  {r['label']:<22}{q[0]:>6.2f}{q[1]:>6.2f}{q[2]:>6.2f}{q[3]:>6.2f}"
              f"{r['spread_ann']*100:>8.1f}%{r['t']:>6.1f}{r['ic']:>7.3f}{r['ic_ir']:>7.2f}"
              f"{r['hit']*100:>5.0f}%{r['mono']:>5}/3{marca}{cobertura}")
        # Para TIR/spread, mostra o VALOR médio do fator por quartil (em %) — termômetro do hurdle
        if r["key"] in ("tir", "spread"):
            qv = [x * 100 for x in r["qval"]]
            print(f"  {'  └ '+r['key']+' médio/quartil:':<22}{qv[0]:>5.1f}%{qv[1]:>6.1f}%"
                  f"{qv[2]:>6.1f}%{qv[3]:>6.1f}%")
    print(f"\n  {'─'*94}")
    print("  Leitura: spread Q4-Q1 anualizado · t = significância do spread mensal (|t|>2 ≈ 95%)")
    print("  IC = Spearman médio (fator × retorno) · IC-IR = IC médio/desvio (consistência) · mono = passos Q1→Q4 crescentes")
    print(f"  {G}◄ gera valor{R} = t≥2 E IC-IR≥0.3 E mono≥2/3 · {Y}[Nm]{R} = nº de meses com cobertura (TIR depende de balanço CVM)")

    d = res.get("diag")
    if d:
        share = (d["q4_illiq"] / d["q4_total"] * 100) if d["q4_total"] else 0.0
        print(f"\n  {C}{B}DIAGNÓSTICO — ilíquidos (< R${d['floor']/1e3:.0f}k/dia) dentro do Q4 da TIR{R}")
        print(f"  {d['q4_illiq']:,} de {d['q4_total']:,} slots (mês×ação) do Q4 são ilíquidos ({share:.0f}%) · "
              f"retorno médio: ilíquidos {d['avg_fwd_illiq']*100:+.2f}%/mês vs líquidos {d['avg_fwd_liq']*100:+.2f}%/mês")
        print(f"  {'Ação':<9}{'Nome':<26}{'vezes':>6}{'TIRméd':>8}{'fwdméd':>8}{'Σfwd':>9}{'maiorfwd':>10}{'liqmín':>9}")
        for x in d["nomes"][:20]:
            nome = (x["nome"] or "")[:24]
            print(f"  {x['tb3']:<9}{nome:<26}{x['n']:>6}{x['avg_tir']*100:>7.0f}%{x['avg_fwd']*100:>7.1f}%"
                  f"{x['sum_fwd']*100:>8.0f}%{x['max_fwd']*100:>8.0f}% ({x['max_mes'][:7]}){x['min_liq']/1e3:>7.0f}k")


def main() -> int:
    p = argparse.ArgumentParser(description="Estudo de fator por quartis — Zelen")
    p.add_argument("--range", default="7y")
    p.add_argument("--anos", type=int, default=9)
    p.add_argument("--liq-min", type=float, default=0.0)
    p.add_argument("--nmin", type=int, default=20, help="mínimo de ações por data p/ formar quartis")
    p.add_argument("--diag-illiq", type=float, default=0.0,
                   help="lista nomes abaixo deste piso (R$/dia) que caem no Q4 da TIR")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--csv", type=Path, default=Path("relatorios/factor_study.csv"))
    args = p.parse_args()

    print(f"\n{C}{B}{'─'*60}{R}")
    print(f"{C}{B}  Estudo estatístico de indicadores · por quartis · Zelen{R}")
    print(f"{C}{B}{'─'*60}{R}\n")

    res = rodar_estudo(range_=args.range, anos_dfp=args.anos,
                       no_cache=args.no_cache, liq_min=args.liq_min, nmin=args.nmin,
                       diag_illiq=args.diag_illiq)
    _imprimir(res)

    # CSV
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    import csv as _csv
    with args.csv.open("w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["indicador", "grupo", "Q1_m", "Q2_m", "Q3_m", "Q4_m",
                    "spread_mensal", "spread_anual", "t_stat", "IC", "IC_IR", "hit", "mono"])
        for r in res["resultados"]:
            w.writerow([r["label"], r["grupo"], *[f"{x:.5f}" for x in r["q"]],
                        f"{r['spread_m']:.5f}", f"{r['spread_ann']:.5f}",
                        f"{r['t']:.2f}", f"{r['ic']:.4f}", f"{r['ic_ir']:.3f}",
                        f"{r['hit']:.3f}", r["mono"]])
    print(f"\n{G}{B}✓ Estudo em {res['tempo']:.1f}s{R} · CSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
