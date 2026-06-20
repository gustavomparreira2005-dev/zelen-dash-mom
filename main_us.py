"""
Pipeline de momentum + valuation para ações AMERICANAS (S&P 1500).

Espelha o main_acoes (Brasil/CVM), mas com fundamentos da SEC (sec_client/
indicadores_us), preços via Yahoo (mercado US, benchmark ^GSPC) e constantes de
mercado americanas (Rf ~4,3%, ERP ~5%, imposto 21%). Roteia FCFF (geral) e FCFE
(financeiras). Gera relatorios/momentum_us.html via o mesmo html_generator.

    python main_us.py                  # universo inteiro (empresas_us.csv)
    python main_us.py --limit 30       # subconjunto p/ teste rápido
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path
from statistics import median

R = "\033[0m"; B = "\033[1m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"


def step(tag, msg): print(f"{C}{B}[{tag}]{R}  {msg}", file=sys.stderr, flush=True)
def ok(msg=""): return f"{G}OK{R}" + (f" — {msg}" if msg else "")


# Constantes de mercado US (editáveis)
RF, ERP, TAX = 0.043, 0.05, 0.21       # 10y UST · ERP maduro · imposto federal
WACC_LO, WACC_HI = 0.05, 0.14
RE_LO, RE_HI = 0.06, 0.16
BETA_LO, BETA_HI = 0.6, 1.8
UP_LO, UP_HI = -0.95, 3.0


def _clamp(x, lo, hi): return max(lo, min(hi, x))


def _rd_us(nd, ebitda):
    x = (nd / ebitda) if (ebitda and ebitda > 0) else 9.0
    spr = (0.005 if x < 0 else 0.008 if x < 1 else 0.012 if x < 2 else
           0.018 if x < 3 else 0.028 if x < 4 else 0.040 if x < 5 else 0.060)
    return RF + spr


def _cagr(serie):
    s = [x for x in serie if x and x > 0]
    if len(s) < 2:
        return None
    return (s[-1] / s[0]) ** (1 / (len(s) - 1)) - 1


def _liq_mediana(series, win=42):
    fin = [(series.raw_close[i] or 0) * (series.volume[i] or 0)
           for i in range(len(series.dates))]
    fin = [v for v in fin[-win:] if v > 0]
    return median(fin) if fin else None


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Momentum + Valuation US (S&P 1500) — Zelen")
    ap.add_argument("--limit", type=int, default=0, help="0 = universo inteiro")
    ap.add_argument("--range", default="2y")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("relatorios/momentum_us.html"))
    args = ap.parse_args()
    t0 = time.time()

    from indicadores_us import calcular_indicadores_us
    from price_client import load_prices, _BENCHMARK_KEY
    from momentum_tecnico import calcular_score_tecnico
    from valuation import (calcular_valuation, premissas_default, calcular_valuation_fcfe,
                           premissas_default_fcfe, calcular_beta, calcular_wacc, Anchors)
    from html_generator_acoes import gerar_relatorio

    with open("empresas_us.csv", encoding="utf-8", newline="") as f:
        universo = list(csv.DictReader(f))
    if args.limit:
        universo = universo[:args.limit]

    # ── Fundamentos + score operacional (SEC) ─────────────────────────────────
    step("SEC", f"Carregando fundamentos de {len(universo)} empresas (EDGAR)…")
    todos: dict = {}
    px_list: list = []
    for i, row in enumerate(universo, 1):
        tk = row["ticker"].strip().upper()
        print(f"\r  {i}/{len(universo)}  {tk:<8}   ", end="", file=sys.stderr, flush=True)
        try:
            res = calcular_indicadores_us(tk)
        except Exception:
            continue
        if res.get("erro") or res.get("score_operacional") is None:
            continue
        c = res.get("campos_brutos") or {}
        todos[tk] = {
            "ticker": tk, "nome": res.get("nome") or row.get("nome") or tk,
            "setor": res.get("setor", ""), "segmento": row.get("setor_gics") or res.get("setor", ""),
            "modelo_valuation": res.get("modelo_valuation", "FCFF"),
            "cagr3_norm": (_cagr(res.get("historico_brutos", {}).get("receita", [])) or 0) * 100 or None,
            "_c": c, "_ind": res.get("ind", {}),
            "_hist_rec": res.get("historico_brutos", {}).get("receita", []),
            **{k: res.get(k) for k in ("a1", "a2", "a3", "a4", "a5", "score_operacional",
                                       "det_a1", "det_a2", "det_a3", "det_a4", "det_a5", "invalido")},
        }
        px_list.append({"ticker_b3": tk, "cd_cvm": "", "nome": todos[tk]["nome"]})
    print("", file=sys.stderr)
    print(ok(f"{len(todos)} com fundamentos"))

    # ── Preços (Yahoo, mercado US, benchmark S&P 500) + score técnico ──────────
    step("PREÇOS", f"Baixando histórico (Yahoo US, range={args.range})…")
    series = load_prices(px_list, cache_dir=Path("_cache_us"), range_=args.range,
                         force_download=args.no_cache, mercado="US", benchmark_symbol="^GSPC")
    bench = series.get(_BENCHMARK_KEY)
    print(ok(f"{sum(1 for k,s in series.items() if k!=_BENCHMARK_KEY and s.ok)} com preço"))

    step("TÉCNICO", "Calculando scores B1-B3…")
    for tk, d in todos.items():
        s = series.get(tk)
        if s and s.ok:
            d.update(calcular_score_tecnico(s, bench))
        else:
            d["score_tecnico"] = None
            d["b1"] = d["b2"] = d["b3"] = 0
    for d in todos.values():
        so, st = d.get("score_operacional") or 0, d.get("score_tecnico") or 0
        d["score_total"] = min(so + st, 100) if (so or st) else None

    # ── Indicadores de mercado (preço×ações, EV/EBITDA, P/L, liquidez) ─────────
    step("MERCADO", "Calculando market cap, múltiplos e liquidez…")
    for tk, d in todos.items():
        s = series.get(tk)
        c = d["_c"]
        preco = (s.raw_close[-1] if (s and s.ok and s.raw_close) else None)
        shares = c.get("shares")
        mkt = (preco * shares) if (preco and shares) else None
        ebit, da = c.get("ebit"), c.get("da") or 0.0
        ebitda = (ebit + da) if ebit is not None else None
        nd = c.get("net_debt")
        d["_preco"] = preco
        d["mkt_cap"] = mkt
        d["ev_ebitda"] = ((mkt + (nd or 0.0)) / ebitda) if (mkt and ebitda and ebitda > 0) else None
        d["pl"] = (mkt / c["lucro_liq"]) if (mkt and c.get("lucro_liq") and c["lucro_liq"] > 0) else None
        d["roe"] = d["_ind"].get("roe")
        d["div_liq_pl"] = (nd / c["pl"]) if (nd is not None and c.get("pl") and c["pl"] > 0) else None
        d["liq_2m"] = _liq_mediana(s) if (s and s.ok) else None

    # ── Valuation (FCFF geral · FCFE financeiras · constantes US) ──────────────
    step("VALUATION", "Computando modelos (FCFF/FCFE · 5 anos · constantes US)…")
    n_val = 0
    MI = 1e6
    for tk, d in todos.items():
        c = d["_c"]
        s = series.get(tk)
        preco, shares = d["_preco"], c.get("shares")
        if not (preco and preco > 0 and shares and shares > 1):
            continue
        n_acoes = shares / MI
        beta_raw = beta_adj = None
        if s and s.ok and bench and bench.ok:
            beta_raw, beta_adj, _ = calcular_beta(s.dates, s.close, bench.dates, bench.close)
        beta_use = _clamp(beta_adj if beta_adj is not None else 1.0, BETA_LO, BETA_HI)
        nd = (c.get("net_debt") or 0.0) / MI
        roe = d["_ind"].get("roe")
        roic = d["_ind"].get("roic")
        cagr = _cagr(d["_hist_rec"])
        try:
            if d["modelo_valuation"] == "FCFE":
                if not (c.get("lucro_liq") and c.get("pl") and c["pl"] > 0):
                    continue
                a = Anchors(ticker=tk, preco=preco, n_acoes=n_acoes,
                            lucro_liq_ltm=c["lucro_liq"] / MI, pl=c["pl"] / MI,
                            roe=(roe / 100.0) if roe is not None else None, cagr_hist=cagr)
                p = premissas_default_fcfe(a)
                p.taxa_desconto = round(_clamp(RF + beta_use * ERP, RE_LO, RE_HI), 4)
                r = calcular_valuation_fcfe(a, p)
                d["val_modelo"] = "FCFE"
                d["val_re"] = p.taxa_desconto; d["val_roe"] = a.roe; d["val_roe_eff"] = r["roe_eff"]
                d["val_pvp_atual"] = r.get("pvp_atual"); d["val_pvp_justo"] = r.get("pvp_justo")
                d["val_ll_ltm"] = a.lucro_liq_ltm; d["val_pl"] = a.pl
                d["val_equity_justo"] = r["equity_justo"]; d["val_ll_ser"] = r["ll"]
                d["val_fcfe_ser"] = r["fcfe"]; d["val_tv"] = r["tv"]; d["val_cresc"] = p.cresc[1:]
            else:
                ebit = c.get("ebit")
                rec = c.get("receita")
                if not (ebit and rec and rec > 0):
                    continue
                ebitda = ebit + (c.get("da") or 0.0)
                a = Anchors(
                    ticker=tk, preco=preco, n_acoes=n_acoes, net_debt=nd,
                    receita_ltm=rec / MI, ebit_ltm=ebit / MI, margem_ebit=ebit / rec,
                    cagr_hist=cagr, roic=(roic / 100.0) if roic is not None else None,
                    ev_ebit_atual=((preco * n_acoes + nd) / (ebit / MI)) if ebit else None,
                    da_ltm=(c.get("da") or 0.0) / MI, capex_ltm=(c.get("capex") or 0.0) / MI,
                    cogs_ltm=(c.get("custo_vendas") or 0.0) / MI)
                p = premissas_default(a)
                p.tax = TAX
                w = calcular_wacc(preco * n_acoes, nd if nd > 0 else 0.0, beta_use,
                                  rf=RF, erp=ERP, custo_divida=_rd_us(nd, ebitda / MI), tax=TAX)
                p.taxa_desconto = round(_clamp(w.wacc, WACC_LO, WACC_HI), 4)
                r = calcular_valuation(a, p)
                d["val_modelo"] = "FCFF"
                d["val_wacc"] = p.taxa_desconto; d["val_beta"] = beta_use
                d["val_rev"] = r["rev"]; d["val_ebit_ser"] = r["ebit"]; d["val_cresc"] = p.cresc[1:]
                d["val_nopat_ser"] = r["nopat"]; d["val_da_ser"] = r["da"]; d["val_capex_ser"] = r["capex"]
                d["val_fcff_ser"] = r["fcff"]; d["val_tv"] = r["tv"]
                d["val_da_pct"] = p.da_pct; d["val_capex_pct"] = p.capex_pct; d["val_cogs_pct"] = p.cogs_pct
                d["val_dso"] = p.dso; d["val_dio"] = p.dio; d["val_dpo"] = p.dpo
                d["val_net_debt"] = nd; d["val_receita_ltm"] = a.receita_ltm; d["val_ebit_ltm"] = a.ebit_ltm
                d["val_margem"] = a.margem_ebit; d["val_ev_ebit"] = a.ev_ebit_atual
            if r.get("tir") is None:
                continue
            # Guarda-corpo: P/L absurdamente baixo (< 2) sinaliza market cap irreal —
            # tipicamente contagem de ações errada em dual-class (ex.: Berkshire conta
            # só uma classe). Nesses casos o valuation é lixo; marca e não pontua TIR.
            plv = d.get("pl")
            if plv is not None and 0 < plv < 2.0:
                d["val_flag"] = "market cap/ações suspeitos (dual-class?)"
                d["val_modelo"] = d.get("val_modelo")
                continue
            up = r.get("upside")
            tir = r["tir"]
            # Winsoriza a TIR: num DCF de 5 anos, TIR > 60% é quase sempre artefato
            # (lucro volátil de seguradora, ações subcontadas). Mantém o ranking são.
            if not (-0.95 < tir < 0.60):
                d["val_flag"] = (d.get("val_flag") or "") + " · TIR extrema (winsorizada)"
            d["val_preco"] = preco
            d["val_tir"] = _clamp(tir, -0.95, 0.60)
            d["val_tir_raw"] = tir
            d["val_preco_justo"] = r.get("preco_justo")
            d["val_upside"] = _clamp(up, UP_LO, UP_HI) if up is not None else None
            d["val_upside_raw"] = up
            d["val_wacc"] = d.get("val_wacc") or d.get("val_re")
            d["val_beta"] = d.get("val_beta") or beta_use
            d["val_beta_raw"] = beta_raw
            d["val_wacc_rf"] = RF; d["val_wacc_erp"] = ERP; d["val_wacc_tax"] = TAX * 100
            d["val_g_perp"] = r.get("g_perp"); d["val_n_acoes"] = n_acoes
            d["val_base_year"] = p.base_year; d["val_flag"] = ""
            d["val_roic"] = (roic / 100.0) if roic is not None else None
            d["val_cagr_hist"] = cagr; d["val_mkt_eq"] = preco * n_acoes
            n_val += 1
        except Exception:
            continue

    # limpa transitórios
    for d in todos.values():
        for k in ("_c", "_ind", "_hist_rec", "_preco"):
            d.pop(k, None)
    print(ok(f"{n_val} modelos calculados"))

    itens = list(todos.values())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    gerar_relatorio(itens, args.out, pais="US")
    print(f"\n{G}{B}✓ US em {time.time()-t0:.1f}s{R} · {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
