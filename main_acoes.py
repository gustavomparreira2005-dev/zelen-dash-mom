"""
Momentum de Ações — CLI principal (Zelen Invest).

Pipeline:
  1. CVM (DFP/ITR)         → indicadores + serie_ltm + serie_trimestral
  2. Momentum operacional  → score absoluto A1-A5 (0-60)
  3. Preços (Yahoo)        → momentum técnico B1-B3 (0-42)
  4. Score combinado       → A + B, cap 100
  5. HTML                  → relatorios/momentum_acoes.html

Uso:
    python main_acoes.py                          # 10 ações MVP (default)
    python main_acoes.py --tickers VALE3 PETR4    # subset
    python main_acoes.py --all                    # universo completo
    python main_acoes.py --lista empresas_lista.csv
    python main_acoes.py --no-cache
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

R = "\033[0m"; B = "\033[1m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"

def tag(label: str, color: str = C) -> str:
    return f"{color}{B}[{label}]{R}"

def step(label: str, msg: str) -> None:
    print(f"{tag(label)}  {msg}", flush=True)

def ok(msg: str = "") -> str:
    return f"{G}OK{R}" + (f" — {msg}" if msg else "")


def _downsample(close, alvo: int = 80):
    vals = [c for c in close if c is not None]
    if len(vals) <= alvo:
        return vals
    passo = len(vals) / alvo
    return [vals[int(i * passo)] for i in range(alvo)]


def _classificacao(score: int) -> str:
    if score >= 80: return "Forte"
    if score >= 65: return "Positivo"
    if score >= 50: return "Neutro"
    if score >= 35: return "Fraco"
    return "Negativo"


# Segmento de atuação (CVM SETOR_ATIV) normalizado: funde holdings ("Emp. Adm.
# Part. - X" → X) no segmento operacional e encurta o nome. (substr s/ acento, label)
_SEG_MAP = [
    ("const", "Construção Civil"), ("comercio", "Comércio/Varejo"),
    ("energia el", "Energia Elétrica"), ("transporte", "Transporte & Logística"),
    ("maquina", "Máquinas & Equip."), ("maqs", "Máquinas & Equip."),
    ("veic", "Máquinas & Equip."), ("textil", "Têxtil & Vestuário"),
    ("vestu", "Têxtil & Vestuário"), ("metalurgia", "Metalurgia & Siderurgia"),
    ("siderurgia", "Metalurgia & Siderurgia"), ("aliment", "Alimentos"),
    ("saneamento", "Saneamento & Água"), ("comunica", "Comunicação & TI"),
    ("informatica", "Comunicação & TI"), ("medico", "Serviços Médicos"),
    ("agricultura", "Agro (Açúcar/Álcool)"), ("acucar", "Agro (Açúcar/Álcool)"),
    ("educa", "Educação"), ("farmac", "Farma & Higiene"),
    ("petroquim", "Petroquímica & Borracha"), ("petroleo", "Petróleo & Gás"),
    ("telecom", "Telecomunicações"), ("brinquedo", "Brinquedos & Lazer"),
    ("mineral", "Mineração"), ("hospedagem", "Hospedagem & Turismo"),
    ("papel", "Papel & Celulose"), ("bebida", "Bebidas & Fumo"),
    ("sem setor", "Holding diversificada"),
]


def _segmento(raw):
    """Segmento granular normalizado a partir do SETOR_ATIV da CVM."""
    s = (raw or "").strip()
    if not s:
        return ""
    # remove o prefixo de holding ("Emp. Adm. Part. - …") → segmento operacional
    na = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    if "adm" in na and "part" in na and " - " in s:
        s = s.split(" - ", 1)[1].strip()
        na = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    for pat, lbl in _SEG_MAP:
        if pat in na:
            return lbl
    return s   # fallback: texto original


def _cagr3_normalizado(hist_rec, hist_gw=None, anos: int = 3):
    """CAGR de receita de até `anos` anos, EXCLUINDO os anos com salto de goodwill
    (= aquisição), em vez de capar. Crescimento orgânico — mesmo alto — passa intacto;
    só o pulo inorgânico de M&A é removido. Retorna % (média geométrica dos anos orgânicos).

    Um ano t é inorgânico se o goodwill subiu > 5% da receita do ano anterior (M&A
    material). Sem goodwill no histórico ⇒ trata tudo como orgânico (sem sinal de M&A).
    Se TODOS os anos da janela forem de M&A, retorna None (crescimento orgânico indeterminável)."""
    rec = list(hist_rec or [])
    n = len(rec)
    if n < 2:
        return None
    gw = (list(hist_gw or []) + [None] * n)[:n]   # alinha ao tamanho de rec
    rec, gw = rec[-anos:], gw[-anos:]
    growths = []
    for i in range(1, len(rec)):
        r0, r1 = rec[i - 1], rec[i]
        if not (r0 and r1 and r0 > 0 and r1 > 0):   # ambos > 0 ⇒ (1+g) > 0 (evita raiz complexa)
            continue
        g0, g1 = gw[i - 1], gw[i]
        inorganico = (g0 is not None and g1 is not None and (g1 - g0) > 0.05 * r0)
        if not inorganico:
            growths.append(r1 / r0 - 1)
    if not growths:
        return None
    fator = 1.0
    for g in growths:
        fator *= (1 + g)
    return (fator ** (1 / len(growths)) - 1) * 100


def _indicadores_mercado(serie_q: list, campos_brutos: dict, fund: dict) -> dict:
    """
    Combina série trimestral (CVM) + fundamentais de mercado (Fundamentus) nos
    indicadores do screener:
      · mkt_cap      — valor de mercado (R$)            [Fundamentus]
      · ev_ebitda    — EV/EBITDA anualizado último tri   [CVM + Fundamentus]
      · pl           — P/L anualizado último tri         [CVM + Fundamentus]
      · cresc_qoq    — crescimento médio receita tri a tri (%)  [CVM]
      · liq_2m       — liquidez média diária 2 meses (R$/dia)   [Fundamentus]
    EV/EBITDA e P/L caem para o valor LTM do Fundamentus quando o anualizado
    não é computável (EBITDA/lucro do tri ≤ 0 ou dívida ausente).
    """
    fund = fund or {}
    out = {
        "mkt_cap":   fund.get("mkt_cap"),
        "liq_2m":    fund.get("liq_2m"),
        "roe":       fund.get("roe"),
        "div_liq_pl": fund.get("div_liq_pl"),
        "ev_ebitda": None,
        "pl":        None,
        "cresc_qoq": None,
        "_ev_anual": False,    # flag: EV/EBITDA veio do cálculo anualizado?
        "_pl_anual": False,
    }
    mkt = fund.get("mkt_cap")

    # Dívida líquida (CVM, posição mais recente)
    cb  = campos_brutos or {}
    dcp, dlp, cx = cb.get("divida_cp"), cb.get("divida_lp"), cb.get("caixa")
    div_liq = None
    if dcp is not None or dlp is not None:
        div_liq = (dcp or 0.0) + (dlp or 0.0) - (cx or 0.0)

    if serie_q:
        ult      = serie_q[-1]
        ebitda_q = ult.get("ebitda")
        lucro_q  = ult.get("lucro_liq")

        # EV/EBITDA anualizado: (MktCap + DívLíq) / (EBITDA_tri × 4)
        if mkt and ebitda_q and ebitda_q > 0 and div_liq is not None:
            out["ev_ebitda"] = (mkt + div_liq) / (ebitda_q * 4)
            out["_ev_anual"] = True

        # P/L anualizado: MktCap / (Lucro_tri × 4)
        if mkt and lucro_q and lucro_q > 0:
            out["pl"] = mkt / (lucro_q * 4)
            out["_pl_anual"] = True

        # Crescimento médio receita tri a tri (últimos 8 tri)
        recs = [p.get("receita") for p in serie_q if p.get("receita")]
        tail = recs[-8:]
        growths = [tail[i] / tail[i - 1] - 1
                   for i in range(1, len(tail)) if tail[i - 1] and tail[i - 1] > 0]
        if growths:
            out["cresc_qoq"] = sum(growths) / len(growths) * 100

    # Fallback LTM (Fundamentus) quando o anualizado não pôde ser calculado
    if out["ev_ebitda"] is None:
        out["ev_ebitda"] = fund.get("ev_ebitda")
    if out["pl"] is None:
        out["pl"] = fund.get("pl")

    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Momentum de Ações — Zelen Invest")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--tickers", nargs="+", metavar="TICKER")
    grp.add_argument("--all", action="store_true")
    grp.add_argument("--lista", type=Path, metavar="CSV")
    p.add_argument("--anos",      type=int,  default=4)
    p.add_argument("--no-cache",  action="store_true")
    p.add_argument("--range",     default="2y")
    p.add_argument("--output",    type=Path, default=None)
    p.add_argument("--cache-dir", type=Path, default=Path("cache_cvm"))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    t0 = time.time()

    print(f"\n{C}{B}{'─'*60}{R}")
    print(f"{C}{B}  Momentum de Ações · A1-A5 + B1-B3 · Zelen Invest{R}")
    print(f"{C}{B}{'─'*60}{R}\n")

    step("IMPORT", "Carregando módulos…")
    try:
        from cvm_client import load_companies, load_companies_bulk, EMPRESAS_MVP
        from indicadores_empresas import calcular_indicadores, calcular_serie_trimestral
        from momentum_operacional import calcular_score_operacional
        from indicadores_financeiras import calcular_indicadores_financeira
        from momentum_financeiro import calcular_score_operacional_financeira
        from price_client import load_prices, _BENCHMARK_KEY
        from momentum_tecnico import calcular_score_tecnico
        from fundamentus_client import load_fundamentus
        from html_generator_acoes import gerar_relatorio
    except ImportError as exc:
        print(f"ERRO ao importar: {exc}", file=sys.stderr)
        return 1
    print(ok())

    # ── CVM ──────────────────────────────────────────────────────────────────
    modo_bulk  = args.all or args.lista
    lista_path = args.lista or Path("empresas_lista.csv")

    if modo_bulk:
        if not lista_path.exists():
            print(f"\n{tag('ERRO', Y)}  {lista_path} não encontrado.")
            return 1
        import csv as _csv
        with lista_path.open("r", encoding="utf-8", newline="") as f:
            empresa_list = list(_csv.DictReader(f))
        empresa_list = [e for e in empresa_list
                        if (e.get("ticker_b3") or "").strip()
                        and (e.get("tp_merc") or "").strip().upper() == "BOLSA"
                        # Exclui canceladas (sem negociação) e em RJ (fundamentos distorcidos)
                        and (e.get("sit_cvm") or "").strip().upper() not in ("CANCELADA",)
                        and "RECUPER" not in (e.get("sit_emissor") or "").upper()
                        and "LIQUID" not in (e.get("sit_emissor") or "").upper()]
        step("CVM", f"Carregando {len(empresa_list)} ações elegíveis (DFP: {args.anos}a)…")
        empresas = load_companies_bulk(empresa_list, anos_dfp=args.anos,
                                       cache_dir=args.cache_dir, force_download=args.no_cache)
    else:
        tickers = args.tickers or list(EMPRESAS_MVP.keys())
        step("CVM", f"Carregando {len(tickers)} ações (DFP: {args.anos}a)…")
        empresas = load_companies(tickers=tickers, anos_dfp=args.anos,
                                  cache_dir=args.cache_dir, force_download=args.no_cache)
    print(ok(f"{sum(1 for e in empresas.values() if not e.erro)} OK"))

    # ── Score operacional ─────────────────────────────────────────────────────
    step("OPERAC", "Calculando scores A1-A5…")
    todos: dict = {}
    ticker_b3_list: list = []

    for chave, emp in empresas.items():
        is_fin = (getattr(emp, "setor_gics", "") == "Financeiro")
        if is_fin:
            # Financeiras: extração e score próprios (template bancário, modelo FCFE)
            res    = calcular_indicadores_financeira(emp)
            serie_q = None
        else:
            res    = calcular_indicadores(emp)
            serie_q = calcular_serie_trimestral(emp)
        tb3    = (res.get("ticker_b3") or "").strip().upper() or str(chave).strip().upper()
        if not tb3 or not any(ch.isdigit() for ch in tb3):
            continue

        cb = res.get("campos_brutos") or {}
        if is_fin:
            result_op = calcular_score_operacional_financeira(res)
            hist_ll = (res.get("historico_brutos") or {}).get("lucro_liq") or []
            todos[tb3] = {
                "ticker":  tb3,
                "nome":    res.get("nome") or emp.nome,
                "setor":   res.get("setor", ""),
                "segmento": "Financeiro",
                "cd_cvm":  res.get("cd_cvm", ""),
                "modelo_valuation": "FCFE",
                "tipo_financeira":  res.get("tipo_financeira", "financeira"),
                "cagr3_norm": _cagr3_normalizado(hist_ll),   # CAGR do lucro p/ a coluna
                # Âncoras do FCFE (removidas antes do HTML)
                "_fin_ll":     cb.get("lucro_liq"),
                "_fin_pl":     cb.get("pl"),
                "_fin_receita": cb.get("receita"),
                "_fin_ativo":  cb.get("ativo_total"),
                "_fin_roe":    (res.get("ind") or {}).get("roe"),   # ROE %
                "_hist_ll":    hist_ll,
                "_hist_rec":   (res.get("historico_brutos") or {}).get("receita") or [],
                **result_op,
            }
        else:
            result_op = calcular_score_operacional(res, serie_q)
            todos[tb3] = {
                "ticker":  tb3,
                "nome":    res.get("nome") or emp.nome,
                "setor":   res.get("setor", ""),
                "segmento": _segmento(res.get("setor_cvm")),
                "cd_cvm":  res.get("cd_cvm", ""),
                "cagr3_norm": _cagr3_normalizado(
                    (res.get("historico_brutos") or {}).get("receita"),
                    (res.get("historico_brutos") or {}).get("goodwill")),
                # Dados transitórios para indicadores de mercado (removidos antes do HTML)
                "_serie_q":       serie_q,
                "_campos_brutos": cb,
                # Âncoras para o modelo de valuation (removidas antes do HTML)
                "_rec_ltm":   cb.get("receita"),
                "_ebit_ltm":  cb.get("ebit"),
                "_divida_cp": cb.get("divida_cp"),
                "_divida_lp": cb.get("divida_lp"),
                "_caixa":     cb.get("caixa"),
                "_desp_fin":  cb.get("desp_fin"),                   # juros financeiros (limpo) p/ custo da dívida
                "_roic":      (res.get("ind") or {}).get("roic"),   # ROIC % p/ reinvestimento perpétuo
                # Insumos do build-up FCFF (D&A, Capex, CMV, capital de giro)
                "_da":        cb.get("da"),
                "_capex":     cb.get("capex"),
                "_cogs":      cb.get("custo_vendas"),
                "_receber":   cb.get("contas_receber"),
                "_estoque":   cb.get("estoques"),
                "_fornec":    cb.get("fornecedores"),
                "_hist_rec":  (res.get("historico_brutos") or {}).get("receita") or [],
                "_hist_ebit": (res.get("historico_brutos") or {}).get("ebit") or [],
                "_hist_da":   (res.get("historico_brutos") or {}).get("da") or [],
                "_hist_capex":(res.get("historico_brutos") or {}).get("capex") or [],
                **result_op,          # score_operacional, a1..a5, det_a1..det_a5, invalido
            }
        ticker_b3_list.append({"ticker_b3": tb3, "cd_cvm": res.get("cd_cvm", ""),
                               "nome": todos[tb3]["nome"]})

    n_op = sum(1 for d in todos.values() if d.get("score_operacional") is not None)
    print(ok(f"{n_op} pontuadas"))

    # ── Score técnico ─────────────────────────────────────────────────────────
    step("PREÇOS", f"Baixando histórico (Yahoo, range={args.range})…")
    series = load_prices(ticker_b3_list, cache_dir=Path("_cache"),
                         range_=args.range, force_download=args.no_cache)
    bench = series.get(_BENCHMARK_KEY)
    print(ok(f"{sum(1 for k,s in series.items() if k!=_BENCHMARK_KEY and s.ok)} com preço"))

    step("TÉCNICO", "Calculando scores B1-B3…")
    for tb3, d in todos.items():
        s = series.get(tb3)
        if s and s.ok:
            result_tec = calcular_score_tecnico(s, bench)
            d.update(result_tec)        # score_tecnico, b1..b4, det_b1..det_b4, metricas_tec
            d["spark_close"] = _downsample(s.close[-252:])
        else:
            d["score_tecnico"] = None
            d["b1"] = d["b2"] = d["b3"] = d["b4"] = 0
            d["metricas_tec"] = {}

    # Score combinado
    for d in todos.values():
        so = d.get("score_operacional") or 0
        st = d.get("score_tecnico") or 0
        d["score_total"] = min(so + st, 100) if (so or st) else None

    n_tec = sum(1 for d in todos.values() if d.get("score_tecnico") is not None)
    print(ok(f"{n_tec} pontuadas"))

    # ── Indicadores de mercado (Fundamentus) ──────────────────────────────────
    step("MERCADO", "Carregando fundamentais (Fundamentus)…")
    fund = load_fundamentus(cache_dir=Path("_cache"), force_download=args.no_cache)
    for tb3, d in todos.items():
        mercado = _indicadores_mercado(d.pop("_serie_q", None),
                                       d.pop("_campos_brutos", None),
                                       fund.get(tb3))
        d.update(mercado)
    n_mkt = sum(1 for d in todos.values() if d.get("mkt_cap") is not None)
    print(ok(f"{n_mkt} com market cap"))

    # ── Filtro de liquidez: remove da base nomes intradáveis (< R$20k/dia) ────────
    # liq_2m = volume financeiro médio diário (Fundamentus). Sem dado ⇒ tratado como
    # 0 (sem negociação registrada ⇒ fora). Aplicado antes do valuation/HTML.
    LIQ_MIN = 20_000.0
    _n_antes = len(todos)
    todos = {tb3: d for tb3, d in todos.items() if (d.get("liq_2m") or 0.0) >= LIQ_MIN}
    print(ok(f"{_n_antes - len(todos)} removidas por liquidez < R$ 20 mil/dia ({len(todos)} restantes)"))

    # ── Valuation (DCF de firma · FCFF · WACC · fade setorial · beta bottom-up) ──
    step("VALUATION", "Computando modelos (DCF de firma · FCFF · 5 anos · premissas automáticas)…")
    try:
        from valuation import (calcular_valuation, premissas_default,
                               calcular_beta, calcular_wacc, Anchors as ValAnchors)
        from statistics import median
        # Parâmetros de mercado (Brasil) — defaults editáveis no dashboard
        WACC_RF, WACC_ERP, WACC_RD, WACC_TAX = 0.105, 0.075, 0.12, 0.34
        # Guarda-corpos da metodologia
        FADE = 0.50                       # peso do fade do múltiplo de saída → mediana setorial
        ANCHOR_LO, ANCHOR_HI = 4.0, 14.0  # faixa sã p/ a âncora setorial de EV/EBIT
        WACC_LO, WACC_HI = 0.08, 0.18     # banda do WACC (corta a "espiral" de beta alto)
        BETA_LO, BETA_HI = 0.6, 1.8       # banda do beta final

        def _rd_sintetico(nd, ebitda, rf):
            """Custo de dívida = Rf + spread de crédito por alavancagem (Net Debt/EBITDA
            → proxy de rating, à la Damodaran). NÃO usa a despesa financeira reportada:
            o `desp_fin` agregado da CVM é contaminado por variação cambial, derivativos
            e juros de lease — até quando 'parece' plausível (ex.: PETR 16% é FX, não juro;
            WEG 50%, RADL 36%). Em WACC NOMINAL em BRL este sintético também é mais correto
            que o cupom em USD (o custo BRL-equivalente da dívida em dólar já embute a
            desvalorização esperada do real, ≈ Rf brasileiro)."""
            x = (nd / ebitda) if (ebitda and ebitda > 0) else 9.0   # sem EBITDA → distress
            spr = (0.010 if x < 0 else 0.015 if x < 1 else 0.020 if x < 2 else
                   0.030 if x < 3 else 0.045 if x < 4 else 0.065 if x < 5 else 0.090)
            return rf + spr
        UP_LO, UP_HI = -0.95, 3.0         # winsorização do upside (ranking)

        def _clamp(x, lo, hi):
            return max(lo, min(hi, x))

        def _med(xs):
            xs = [x for x in xs if x is not None]
            return median(xs) if xs else None

        # ── Passe 1: coleta de inputs validados (p/ agregados setoriais) ───────
        vin: dict = {}
        for tb3, d in todos.items():
            rec  = (d.get("_rec_ltm")  or 0.0) / 1e6          # R$ mi
            ebt  = (d.get("_ebit_ltm") or 0.0) / 1e6
            dcp  = d.get("_divida_cp") or 0.0
            dlp  = d.get("_divida_lp") or 0.0
            cx   = d.get("_caixa")     or 0.0
            roic = d.get("_roic")                              # ROIC % (ou None)
            hist = d.get("_hist_rec")  or []

            preco   = (fund.get(tb3) or {}).get("cotacao") or 0.0
            mkt     = d.get("mkt_cap") or 0.0
            n_acoes = mkt / preco / 1e6 if preco > 0 else 0.0
            nd        = (dcp + dlp - cx) / 1e6                 # R$ mi
            div_bruta = (dcp + dlp) / 1e6                      # R$ mi
            if not (preco > 0 and n_acoes > 0.01 and rec > 0 and ebt > 0):
                continue

            mkt_eq  = mkt / 1e6
            ev_ebit = (mkt_eq + nd) / ebt
            # Beta de regressão vs IBOV + Blume, depois desalavancado p/ bottom-up
            s = series.get(tb3)
            beta_raw = beta_adj = None
            if s and s.ok and bench and bench.ok:
                beta_raw, beta_adj, _ = calcular_beta(s.dates, s.close, bench.dates, bench.close)
            beta_blume = beta_adj if beta_adj is not None else 1.0
            de = _clamp(div_bruta / mkt_eq if mkt_eq > 0 else 0.0, 0.0, 5.0)  # D/E
            beta_u = beta_blume / (1 + (1 - WACC_TAX) * de)                   # desalavancado

            hist_f = [h for h in hist if h and h > 0]
            cagr = ((hist_f[-1] / hist_f[0]) ** (1 / (len(hist_f) - 1)) - 1
                    if len(hist_f) >= 2 else None)

            vin[tb3] = dict(rec=rec, ebt=ebt, nd=nd, div_bruta=div_bruta, preco=preco,
                            n_acoes=n_acoes, mkt_eq=mkt_eq, ev_ebit=ev_ebit, roic=roic,
                            cagr=cagr, beta_raw=beta_raw, beta_blume=beta_blume,
                            beta_u=beta_u, de=de, setor=d.get("setor") or "",
                            desp_fin=abs(d.get("_desp_fin") or 0.0) / 1e6,
                            da=abs(d.get("_da") or 0.0) / 1e6,
                            capex=abs(d.get("_capex") or 0.0) / 1e6,
                            cogs=abs(d.get("_cogs") or 0.0) / 1e6,
                            receber=(d.get("_receber") or 0.0) / 1e6,
                            estoque=(d.get("_estoque") or 0.0) / 1e6,
                            fornec=(d.get("_fornec") or 0.0) / 1e6)

        # ── Agregados setoriais (mediana robusta) ──────────────────────────────
        by_setor: dict = {}
        for v in vin.values():
            by_setor.setdefault(v["setor"], []).append(v)
        glob_ev = _med([v["ev_ebit"] for v in vin.values() if v["ev_ebit"] and v["ev_ebit"] > 0]) or 8.0
        glob_bu = _med([v["beta_u"] for v in vin.values()]) or 0.70
        sec_ev = {s: _med([v["ev_ebit"] for v in g if v["ev_ebit"] and v["ev_ebit"] > 0])
                  for s, g in by_setor.items()}
        sec_bu = {s: (_med([v["beta_u"] for v in g]) if len(g) >= 3 else None)
                  for s, g in by_setor.items()}

        # ── Passe 2: valuation por empresa ─────────────────────────────────────
        n_val = 0
        for tb3, v in vin.items():
            d = todos[tb3]
            # Front 2 — fade do múltiplo de saída p/ a mediana setorial
            anchor = _clamp(sec_ev.get(v["setor"]) or glob_ev, ANCHOR_LO, ANCHOR_HI)
            cur    = v["ev_ebit"] if 0 < v["ev_ebit"] < 40 else anchor
            exit_mult = cur + FADE * (anchor - cur)
            # Front 3 — beta bottom-up: mediana setorial desalavancada → relavancada
            bu = sec_bu.get(v["setor"]) or glob_bu
            beta_bottomup = bu * (1 + (1 - WACC_TAX) * v["de"])
            beta_use = _clamp(0.5 * v["beta_blume"] + 0.5 * beta_bottomup, BETA_LO, BETA_HI)
            # Custo da dívida por empresa: sintético por alavancagem (ver _rd_sintetico).
            ebitda_emp = v["ebt"] + v["da"]
            rd_emp = _rd_sintetico(v["nd"], ebitda_emp, WACC_RF)
            # WACC (com banda)
            w = calcular_wacc(v["mkt_eq"], v["div_bruta"], beta_use,
                              rf=WACC_RF, erp=WACC_ERP, custo_divida=rd_emp, tax=WACC_TAX)
            wacc = _clamp(w.wacc, WACC_LO, WACC_HI)

            roic_frac = (v["roic"] / 100.0) if v["roic"] is not None else None
            a = ValAnchors(
                ticker=tb3, n_acoes=v["n_acoes"], preco=v["preco"], net_debt=v["nd"],
                receita_ltm=v["rec"], ebit_ltm=v["ebt"], margem_ebit=v["ebt"] / v["rec"],
                cagr_hist=v["cagr"], ev_ebit_atual=v["ev_ebit"], roic=roic_frac,
                da_ltm=v["da"], capex_ltm=v["capex"], cogs_ltm=v["cogs"],
                receber=v["receber"], estoque=v["estoque"], fornecedores=v["fornec"],
            )
            p = premissas_default(a)
            p.ev_ebit_saida = round(exit_mult, 1)   # cross-check informativo
            p.taxa_desconto = round(wacc, 4)
            p.tax = WACC_TAX
            r = calcular_valuation(a, p)
            if r.get("tir") is None:
                continue

            # Front 4 — flags de qualidade + winsorização do upside
            up_raw = r.get("upside")
            flags = []
            if v["roic"] is None:                       flags.append("ROIC ausente (usou 12%)")
            elif not (5.0 <= v["roic"] <= 50.0):        flags.append("ROIC fora de faixa (limitado a 5–50%)")
            if v["beta_raw"] is None:                   flags.append("beta insuf. (usou 1,0)")
            if not (0 < v["ev_ebit"] < 40):             flags.append("EV/EBIT atual fora de faixa")
            if v["nd"] < 0 and abs(v["nd"]) > v["mkt_eq"]: flags.append("caixa líq. > market cap")
            if up_raw is not None and not (UP_LO < up_raw < UP_HI): flags.append("upside extremo (winsorizado)")
            up_clamp = _clamp(up_raw, UP_LO, UP_HI) if up_raw is not None else None

            d["val_preco"]        = v["preco"]
            d["val_tir"]          = r["tir"]
            d["val_preco_justo"]  = r.get("preco_justo")
            d["val_upside"]       = up_clamp
            d["val_upside_raw"]   = up_raw
            d["val_flag"]         = " · ".join(flags)
            d["val_ev_ebit"]      = v["ev_ebit"]
            d["val_saida_mult"]   = p.ev_ebit_saida
            d["val_anchor_mult"]  = round(anchor, 1)
            d["val_cagr_hist"]    = v["cagr"]
            d["val_receita_ltm"]  = v["rec"]
            d["val_ebit_ltm"]     = v["ebt"]
            d["val_net_debt"]     = v["nd"]
            d["val_margem"]       = v["ebt"] / v["rec"]
            d["val_roic"]         = roic_frac
            d["val_n_acoes"]      = v["n_acoes"]
            d["val_rev"]          = r["rev"]      # [R0..R5] R$ mi
            d["val_ebit_ser"]     = r["ebit"]     # [E0..E5] R$ mi
            # Histórico (até 3 anos) p/ o schedule horizontal — R$ mi
            d["val_hist_rev"]     = [x / 1e6 for x in (d.get("_hist_rec") or [])][-3:]
            d["val_hist_ebit"]    = [x / 1e6 for x in (d.get("_hist_ebit") or [])][-3:]
            d["val_hist_da"]      = [abs(x) / 1e6 for x in (d.get("_hist_da") or [])][-3:]
            d["val_hist_capex"]   = [abs(x) / 1e6 for x in (d.get("_hist_capex") or [])][-3:]
            d["val_nopat_ser"]    = r["nopat"]    # build-up FCFF
            d["val_da_ser"]       = r["da"]
            d["val_capex_ser"]    = r["capex"]
            d["val_dwc_ser"]      = r["dwc"]
            d["val_fcff_ser"]     = r["fcff"]     # [0,F1..F5] R$ mi
            d["val_tv"]           = r["tv"]
            d["val_ev_justo"]     = r["ev_justo"]
            d["val_saida_impl"]   = r.get("ev_ebit_saida_impl")
            d["val_cresc"]        = p.cresc[1:]   # [g1..g5]
            # Drivers do FCFF (editáveis no card)
            d["val_da_pct"]       = p.da_pct
            d["val_capex_pct"]    = p.capex_pct
            d["val_cogs_pct"]     = p.cogs_pct
            d["val_dso"]          = p.dso
            d["val_dio"]          = p.dio
            d["val_dpo"]          = p.dpo
            d["val_g_perp"]       = r["g_perp"]
            d["val_desconto"]     = p.taxa_desconto
            d["val_base_year"]    = p.base_year
            # Componentes do WACC (editáveis no dashboard)
            d["val_wacc"]         = wacc
            d["val_beta"]         = beta_use
            d["val_beta_raw"]     = v["beta_raw"]
            d["val_wacc_rf"]      = WACC_RF
            d["val_wacc_erp"]     = WACC_ERP
            d["val_wacc_rd"]      = rd_emp          # custo de dívida por empresa
            d["val_wacc_rd_src"]  = "alavancagem"   # método: Rf + spread por Net Dív/EBITDA
            d["val_wacc_tax"]     = WACC_TAX
            d["val_wacc_we"]      = w.peso_equity
            d["val_wacc_wd"]      = w.peso_divida
            d["val_div_bruta"]    = v["div_bruta"]
            d["val_mkt_eq"]       = v["mkt_eq"]
            n_val += 1

        # ── Passe financeiro: valuation por FCFE (bancos, seguradoras, B3) ───────
        # Desconto ao custo de equity Re (= Rf + β·ERP), NÃO ao WACC — para uma
        # financeira a dívida/depósito é matéria-prima, não financiamento.
        from valuation import calcular_valuation_fcfe, premissas_default_fcfe
        RE_LO, RE_HI = 0.10, 0.22
        n_fin = 0
        for tb3, d in todos.items():
            if d.get("modelo_valuation") != "FCFE":
                continue
            ll = (d.get("_fin_ll") or 0.0) / 1e6                 # R$ mi
            pl = (d.get("_fin_pl") or 0.0) / 1e6
            roe_pct = d.get("_fin_roe")                          # %
            preco = (fund.get(tb3) or {}).get("cotacao") or 0.0
            mkt   = d.get("mkt_cap") or 0.0
            n_acoes = mkt / preco / 1e6 if preco > 0 else 0.0
            if not (preco > 0 and n_acoes > 0.01 and ll != 0 and pl > 0):
                continue
            s = series.get(tb3)
            beta_raw = beta_adj = None
            if s and s.ok and bench and bench.ok:
                beta_raw, beta_adj, _ = calcular_beta(s.dates, s.close, bench.dates, bench.close)
            beta_use = _clamp(beta_adj if beta_adj is not None else 1.0, BETA_LO, BETA_HI)
            re = _clamp(WACC_RF + beta_use * WACC_ERP, RE_LO, RE_HI)
            roe_frac = (roe_pct / 100.0) if roe_pct is not None else None
            hist_ll = [x / 1e6 for x in (d.get("_hist_ll") or []) if x]
            cagr = ((hist_ll[-1] / hist_ll[0]) ** (1 / (len(hist_ll) - 1)) - 1
                    if len(hist_ll) >= 2 and hist_ll[0] > 0 and hist_ll[-1] > 0 else None)
            a = ValAnchors(ticker=tb3, preco=preco, n_acoes=n_acoes,
                           lucro_liq_ltm=ll, pl=pl, roe=roe_frac, cagr_hist=cagr)
            p = premissas_default_fcfe(a)
            p.taxa_desconto = round(re, 4)
            r = calcular_valuation_fcfe(a, p)
            if r.get("tir") is None:
                continue
            up_raw = r.get("upside")
            flags = []
            if roe_frac is None:                        flags.append("ROE ausente (usou 15%)")
            elif not (0.05 <= roe_frac <= 0.40):        flags.append("ROE fora de faixa (limitado 5–40%)")
            if d.get("tipo_financeira") == "seguradora": flags.append("seguradora — receita não comparável")
            if up_raw is not None and not (UP_LO < up_raw < UP_HI): flags.append("upside extremo (winsorizado)")
            up_clamp = _clamp(up_raw, UP_LO, UP_HI) if up_raw is not None else None
            d["val_modelo"]       = "FCFE"
            d["val_preco"]        = preco
            d["val_tir"]          = r["tir"]
            d["val_preco_justo"]  = r.get("preco_justo")
            d["val_upside"]       = up_clamp
            d["val_upside_raw"]   = up_raw
            d["val_flag"]         = " · ".join(flags)
            d["val_wacc"]         = re                  # custo de equity (Re) — rótulo no HTML
            d["val_re"]           = re
            d["val_beta"]         = beta_use
            d["val_beta_raw"]     = beta_raw
            d["val_roe"]          = roe_frac
            d["val_roe_eff"]      = r["roe_eff"]
            d["val_pvp_atual"]    = r.get("pvp_atual")
            d["val_pvp_justo"]    = r.get("pvp_justo")
            d["val_ll_ltm"]       = ll
            d["val_pl"]           = pl
            d["val_equity_justo"] = r["equity_justo"]
            d["val_g_perp"]       = r["g_perp"]
            d["val_cresc"]        = p.cresc[1:]
            d["val_ll_ser"]       = r["ll"]
            d["val_fcfe_ser"]     = r["fcfe"]
            d["val_tv"]           = r["tv"]
            d["val_n_acoes"]      = n_acoes
            d["val_mkt_eq"]       = mkt / 1e6
            d["val_wacc_rf"]      = WACC_RF
            d["val_wacc_erp"]     = WACC_ERP
            d["val_wacc_tax"]     = WACC_TAX
            d["val_base_year"]    = p.base_year
            n_fin += 1

        # limpa âncoras transitórias
        for d in todos.values():
            for k in ("_rec_ltm", "_ebit_ltm", "_divida_cp", "_divida_lp", "_caixa", "_desp_fin", "_roic",
                      "_da", "_capex", "_cogs", "_receber", "_estoque", "_fornec",
                      "_hist_rec", "_hist_ebit", "_hist_da", "_hist_capex",
                      "_fin_ll", "_fin_pl", "_fin_receita", "_fin_ativo", "_fin_roe", "_hist_ll"):
                d.pop(k, None)
        print(ok(f"{n_val} modelos FCFF + {n_fin} FCFE calculados"))
    except Exception as exc:
        import traceback
        print(f"  AVISO: valuation desativado ({exc})", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        for d in todos.values():
            for k in ("_rec_ltm", "_ebit_ltm", "_divida_cp", "_divida_lp", "_caixa", "_desp_fin", "_roic",
                      "_da", "_capex", "_cogs", "_receber", "_estoque", "_fornec",
                      "_hist_rec", "_hist_ebit", "_hist_da", "_hist_capex",
                      "_fin_ll", "_fin_pl", "_fin_receita", "_fin_ativo", "_fin_roe", "_hist_ll"):
                d.pop(k, None)

    # Resumo terminal (top 15)
    ordenados = sorted(
        (d for d in todos.values() if d.get("score_total") is not None),
        key=lambda d: -d["score_total"]
    )
    print(f"\n  {B}Top 15 — Score combinado (A+B){R}")
    print(f"  {'#':>3}  {'Ticker':<8}  {'Total':>5}  {'A':>4}  {'B':>4}  {'Classe'}")
    print(f"  {'─'*55}")
    for i, d in enumerate(ordenados[:15], 1):
        cl = _classificacao(d["score_total"])
        print(f"  {i:>3}. {d['ticker']:<8}  {d['score_total']:>5}  "
              f"{d.get('score_operacional') or 0:>4}  "
              f"{d.get('score_tecnico') or 0:>4}  {cl}")

    # ── HTML ──────────────────────────────────────────────────────────────────
    print()
    step("HTML", "Gerando dashboard…")
    output_path = args.output or (Path("relatorios") / "momentum_acoes.html")
    path = gerar_relatorio(list(todos.values()), output_path)
    abs_path = path.resolve()
    file_url = "file:///" + str(abs_path).replace("\\", "/").replace(" ", "%20")
    print(ok(str(abs_path)))
    print(f"  {C}URL:{R}  {file_url}")

    print(f"\n{G}{B}✓ Concluído em {time.time()-t0:.1f}s{R}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
