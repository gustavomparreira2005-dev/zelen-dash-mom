"""
Indicadores de ações americanas — mapeia o SEC EDGAR (sec_client) para o mesmo
contrato que o pipeline consome (setor, ind, campos_brutos, historico_brutos),
classifica o setor por SIC e roteia o modelo de valuation (FCFF geral · FCFE
para financeiras), com um score operacional anual A1-A5.

Tudo em USD. Constantes de mercado US ficam no pipeline (main_us); aqui só os
fundamentos e o score.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from sec_client import fundamentos_us, perfil_us


# ─── Classificação de setor por SIC ───────────────────────────────────────────

def _classificar(sic: str, sic_desc: str) -> Tuple[str, bool]:
    """(setor_amplo, usa_fcfe) a partir do SIC code da SEC."""
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return "Outros", False
    # Financeiras → FCFE (bancos 6020-6199, securities 6200-6299, seguros 6300-6411)
    if 6020 <= s <= 6411:
        return "Financeiro", True
    div = [
        (1, 1499, "Materiais"), (1300, 1399, "Energia"), (1500, 1799, "Industria"),
        (2000, 2099, "Consumo Basico"), (2080, 2099, "Bebidas"),
        (2800, 2899, "Materiais"), (2830, 2836, "Saude"), (2833, 2836, "Saude"),
        (2900, 2999, "Energia"), (3000, 3599, "Industria"),
        (3570, 3579, "Tecnologia"), (3600, 3699, "Tecnologia"),
        (3674, 3674, "Tecnologia"), (3711, 3714, "Consumo Disc."),
        (3800, 3829, "Saude"), (3840, 3851, "Saude"),
        (4000, 4299, "Industria"), (4400, 4700, "Industria"),
        (4800, 4899, "Comunicacao"), (4900, 4999, "Utilities"),
        (5000, 5199, "Industria"), (5200, 5999, "Consumo Disc."),
        (6500, 6599, "Imobiliario"), (6798, 6798, "Imobiliario"),
        (7370, 7379, "Tecnologia"), (7300, 7399, "Tecnologia"),
        (8000, 8099, "Saude"), (2835, 2836, "Saude"),
    ]
    best = "Outros"
    span = 10 ** 9
    for lo, hi, lbl in div:
        if lo <= s <= hi and (hi - lo) < span:
            best, span = lbl, hi - lo
    return best, False


# ─── Score operacional anual (A1-A5) ──────────────────────────────────────────

def _cagr(serie: List[float]) -> Optional[float]:
    s = [x for x in serie if x is not None]
    if len(s) < 2 or s[0] <= 0 or s[-1] <= 0:
        return None
    return (s[-1] / s[0]) ** (1 / (len(s) - 1)) - 1


def _bucket(x: Optional[float], cortes: List[float], pts: List[int]) -> int:
    if x is None:
        return 0
    for c, p in zip(cortes, pts):
        if x < c:
            return p
    return pts[-1]


def _score_operacional_us(campos: Dict, series: Dict) -> Dict:
    rev = [v for _, v in series.get("receita", [])]
    ebit = [v for _, v in series.get("ebit", [])]
    out = {"score_operacional": None, "a1": 0, "a2": 0, "a3": 0, "a4": 0, "a5": 0,
           "det_a1": {}, "det_a2": {}, "det_a3": {}, "det_a4": {}, "det_a5": {},
           "invalido": None}
    if len(rev) < 2:
        out["invalido"] = "histórico insuficiente"
        return out
    # A1 crescimento (CAGR receita)
    g = _cagr(rev)
    out["a1"] = _bucket(g, [0.0, 0.05, 0.10, 0.20, 0.35], [0, 5, 9, 12, 14]) if g is not None else 3
    out["det_a1"] = {"cagr_rec": round(g * 100, 1) if g is not None else None}
    # A2 consistência (anos de crescimento)
    passos = sum(1 for i in range(1, len(rev)) if rev[i] > rev[i - 1])
    out["a2"] = round(passos / (len(rev) - 1) * 12)
    # A3 aceleração (último YoY vs anterior)
    if len(rev) >= 3 and rev[-3] > 0:
        yoy1 = rev[-1] / rev[-2] - 1 if rev[-2] else 0
        yoy0 = rev[-2] / rev[-3] - 1 if rev[-3] else 0
        out["a3"] = _bucket(yoy1 - yoy0, [-0.05, 0.0, 0.05, 0.15], [2, 5, 7, 10])
    else:
        out["a3"] = 5
    # A4 qualidade (margem EBIT atual)
    mg = campos.get("margem_ebit")
    out["a4"] = _bucket(mg, [0.0, 0.08, 0.15, 0.25, 0.40], [2, 5, 8, 11, 13]) if mg is not None else 4
    out["det_a4"] = {"margem_ebit": round(mg * 100, 1) if mg is not None else None}
    # A5 solidez (alavancagem Net Debt/EBITDA — quanto menor melhor)
    nd = campos.get("net_debt")
    ebt = campos.get("ebit")
    da = campos.get("da") or 0.0
    ebitda = (ebt + da) if ebt is not None else None
    ndl = (nd / ebitda) if (nd is not None and ebitda and ebitda > 0) else None
    if ndl is None:
        out["a5"] = 6
    else:
        out["a5"] = _bucket(ndl, [0.0, 1.0, 2.0, 3.0, 4.5], [10, 9, 7, 5, 3])  # net cash=10
    out["det_a5"] = {"nd_ebitda": round(ndl, 1) if ndl is not None else None}
    out["score_operacional"] = min(sum(out[k] for k in ("a1", "a2", "a3", "a4", "a5")), 60)
    return out


# ─── Função principal ─────────────────────────────────────────────────────────

def calcular_indicadores_us(ticker: str, as_of: Optional[str] = None) -> Dict:
    f = fundamentos_us(ticker, as_of=as_of)
    if f.get("erro"):
        return {"ticker": ticker, "erro": f["erro"], "ind": {}, "campos_brutos": {}}
    perfil = perfil_us(ticker)
    sic, sic_desc = perfil.get("sic", ""), perfil.get("sic_desc", "")
    setor, usa_fcfe = _classificar(sic, sic_desc)
    c = f["campos"]
    series = f["series"]

    roe = c.get("roe")
    roic = None
    ebt, ativo, caixa, pl = c.get("ebit"), c.get("ativo"), c.get("caixa"), c.get("pl")
    if ebt is not None and ativo and pl:
        cap_inv = max(ativo - (caixa or 0.0), pl)            # proxy de capital investido
        if cap_inv > 0:
            roic = ebt * (1 - 0.21) / cap_inv

    res: Dict = {
        "ticker": ticker, "nome": f.get("nome"), "cik": f.get("cik"),
        "setor": setor, "segmento": sic_desc or setor, "sic": sic,
        "pais": "US", "ifrs": f.get("ifrs", False),
        "modelo_valuation": "FCFE" if usa_fcfe else "FCFF",
        "campos_brutos": c,
        "historico_brutos": {k: [v for _, v in series.get(k, [])]
                             for k in ("receita", "ebit", "lucro_liq", "pl")},
        "ind": {
            "roe": roe * 100 if roe is not None else None,
            "roic": roic * 100 if roic is not None else None,
            "margem_ebit": (c.get("margem_ebit") * 100) if c.get("margem_ebit") is not None else None,
        },
    }
    if usa_fcfe:
        from momentum_financeiro import calcular_score_operacional_financeira
        # adapta historico p/ o score financeiro (espera lucro_liq + pl)
        res_fin = {"ind": {"roe": res["ind"]["roe"]},
                   "historico_brutos": {"lucro_liq": res["historico_brutos"]["lucro_liq"],
                                        "pl": res["historico_brutos"]["pl"]}}
        res.update(calcular_score_operacional_financeira(res_fin))
    else:
        res.update(_score_operacional_us(c, series))
    return res


if __name__ == "__main__":
    import sys
    for tk in (sys.argv[1:] or ["AAPL", "MSFT", "JPM", "NU", "MELI", "XOM", "PFE"]):
        r = calcular_indicadores_us(tk)
        if r.get("erro"):
            print(f"{tk}: {r['erro']}"); continue
        ind = r["ind"]
        print(f"\n{r['nome']} ({tk}) · {r['setor']} / {r['segmento'][:30]} · modelo {r['modelo_valuation']}")
        print(f"  A={r.get('score_operacional')} (a1-5: {r.get('a1')},{r.get('a2')},{r.get('a3')},{r.get('a4')},{r.get('a5')})"
              f" | ROE {ind['roe'] and round(ind['roe'],1)}% | ROIC {ind['roic'] and round(ind['roic'],1)}%"
              f" | Mrg {ind['margem_ebit'] and round(ind['margem_ebit'],1)}%")
        print(f"  hist receita (US$ bi): {[round(v/1e9,1) for v in r['historico_brutos']['receita']]}")
