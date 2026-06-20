"""
Indicadores e valuation de FIIs de TIJOLO — foco em QUALIDADE e renda (não em P/VP).

Eixos (instrução Parreira):
  · Não-diluição ⭐ — VP/cota e provento/cota se sustentam ATRAVÉS das emissões?
                      (gestão acretiva vs diluidora — o sinal de qualidade central)
  · Volatilidade anualizada (winsorizada) — FII é renda; menos vol = melhor
  · Consistência da distribuição — DY estável > DY alto e errático
  · Valuation por RENDA (DDM/Gordon), não P/VP — preço justo = D·(1+g)/(r−g)
    r = NTN-B real + inflação + prêmio FII; g = repasse de inflação (contratos IPCA)
  · P/VP e DY ficam como leitura secundária.

CVM (fii_client) → VP/cota, patrimônio, cotas, classificação tijolo.
Yahoo (fii_client.serie_preco_div) → preço, dividendos (DY), volatilidade.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from fii_client import FiiData, classificar_tijolo, serie_preco_div

# ── Premissas do DDM (editáveis) ──────────────────────────────────────────────
NTNB_REAL = 0.07          # juro real longo (NTN-B)
INFLACAO  = 0.045         # repasse de inflação esperado (contratos IPCA)
PREMIO_FII = 0.015        # prêmio de risco do FII sobre o título público
# Retorno nominal requerido e crescimento nominal (Gordon)
R_NOM = (1 + NTNB_REAL) * (1 + INFLACAO) - 1 + PREMIO_FII
G_NOM = INFLACAO


def _winsor(x: float, lim: float) -> float:
    return max(-lim, min(lim, x))


def _vol_anual(closes: List[float]) -> Optional[float]:
    """Vol anualizada dos retornos diários, winsorizada a ±15%/dia (corta ticks ruins)."""
    cl = [c for c in closes if c]
    if len(cl) < 30:
        return None
    rets = [_winsor(math.log(cl[i] / cl[i - 1]), 0.15)
            for i in range(1, len(cl)) if cl[i] and cl[i - 1] and cl[i - 1] > 0]
    n = len(rets)
    if n < 20:
        return None
    m = sum(rets) / n
    sd = (sum((r - m) ** 2 for r in rets) / (n - 1)) ** 0.5
    return sd * math.sqrt(252)


def _cagr(v0: float, v1: float, anos: float) -> Optional[float]:
    if not v0 or v0 <= 0 or v1 <= 0 or anos <= 0:
        return None
    return (v1 / v0) ** (1 / anos) - 1


def _bucket(x: Optional[float], cortes: List[float], pts: List[int], reverso=False) -> int:
    if x is None:
        return pts[len(pts) // 2]
    seq = list(zip(cortes, pts))
    for c, p in seq:
        if (x > c) if reverso else (x < c):
            return p
    return pts[-1]


def calcular_indicadores_fii(fii: FiiData) -> Dict:
    out: Dict = {"ticker": "", "nome": fii.nome, "cnpj": fii.cnpj, "erro": None}
    classe, frac_im = classificar_tijolo(fii)
    out["classe"] = classe
    out["frac_imoveis"] = frac_im

    serie = [m for m in fii.serie if m.get("vp_cota") and m.get("cotas")]
    if len(serie) < 6:
        out["erro"] = "histórico CVM insuficiente"
        return out
    u = serie[-1]
    out["vp_cota"] = u.get("vp_cota")
    out["pl"] = u.get("pl")
    out["cotas"] = u.get("cotas")
    out["data_ref"] = u.get("data")

    # ── Não-diluição: trajetória de VP/cota e cotas ao longo da história CVM ────
    anos = len(serie) / 12.0
    vp_cagr = _cagr(serie[0]["vp_cota"], u["vp_cota"], anos)
    cota_cagr = _cagr(serie[0]["cotas"], u["cotas"], anos)
    out["vp_cota_cagr"] = vp_cagr            # NOMINAL — teste de não-diluição do book
    out["cota_cagr"] = cota_cagr
    # Real (vs inflação) fica como leitura de qualidade do ativo, não de diluição
    out["vp_cota_real_cagr"] = (((1 + vp_cagr) / (1 + INFLACAO) - 1)
                                if vp_cagr is not None else None)
    # Não-diluição = VP/cota NOMINAL não caiu enquanto as cotas cresceram.
    # (BTLG quadruplicou cotas e segurou R$101→103 = disciplina; XPML/VISC caíram = diluiu)
    out["acretivo"] = (vp_cagr is not None and vp_cagr >= -0.002)
    return out


def avaliar_fii(fii: FiiData, ticker: str) -> Dict:
    """Indicadores CVM + métricas de mercado (Yahoo) + valuation DDM + score de qualidade."""
    out = calcular_indicadores_fii(fii)
    out["ticker"] = ticker
    if out.get("erro"):
        return out

    mkt = serie_preco_div(ticker)
    preco = mkt.get("preco") if mkt else None
    out["preco"] = preco
    out["pvp"] = (preco / out["vp_cota"]) if (preco and out.get("vp_cota")) else None
    out["vol_anual"] = _vol_anual(mkt.get("close", [])) if mkt else None
    # Liquidez: mediana do volume financeiro (preço×volume) dos últimos ~42 pregões
    if mkt and mkt.get("close") and mkt.get("volume"):
        cl, vol = mkt["close"], mkt["volume"]
        fin = sorted(c * v for c, v in zip(cl[-42:], vol[-42:]) if c and v)
        out["liq_2m"] = fin[len(fin) // 2] if fin else None
    else:
        out["liq_2m"] = None

    # ── DY 12m + consistência (CV das distribuições) ───────────────────────────
    import time
    now = time.time()
    divs12 = [a for (d, a) in (mkt.get("dividendos") if mkt else [])
              if now - d <= 365 * 24 * 3600]
    soma12 = sum(divs12)
    out["dist_12m"] = soma12
    out["dy"] = (soma12 / preco) if (preco and soma12) else None
    if len(divs12) >= 6:
        m = sum(divs12) / len(divs12)
        cv = ((sum((x - m) ** 2 for x in divs12) / len(divs12)) ** 0.5 / m) if m else None
        out["dist_cv"] = cv          # menor = mais consistente
    else:
        out["dist_cv"] = None

    # ── Valuation por RENDA (DDM/Gordon) — não P/VP ────────────────────────────
    if soma12 > 0:
        d_fwd = soma12 * (1 + G_NOM)
        preco_justo = d_fwd / (R_NOM - G_NOM)
        out["preco_justo"] = preco_justo
        out["upside"] = (preco_justo / preco - 1) if preco else None
        out["dy_justo"] = R_NOM - G_NOM        # yield de equilíbrio do modelo
    else:
        out["preco_justo"] = out["upside"] = out["dy_justo"] = None

    # ── Score de QUALIDADE (0-100) ─────────────────────────────────────────────
    # Não-diluição (0-35): VP/cota NOMINAL sustentado/crescente através das emissões.
    q_dil = _bucket(out.get("vp_cota_cagr"),
                    [-0.02, 0.0, 0.015, 0.03], [6, 16, 24, 30], reverso=False)
    q_dil = 35 if (out.get("vp_cota_cagr") or -1) >= 0.03 else q_dil
    # Consistência da renda (0-25): CV baixo.
    q_cons = _bucket(out.get("dist_cv"), [0.05, 0.12, 0.25, 0.50], [25, 20, 13, 7], reverso=False)
    # Baixa volatilidade (0-25): vol baixa.
    q_vol = _bucket(out.get("vol_anual"), [0.12, 0.18, 0.25, 0.35], [25, 19, 12, 6], reverso=False)
    # Porte por PL (0-15): fundos maiores = mais robustos/líquidos.
    # Sem reverso: PL pequeno cai no 1º corte (poucos pts), PL grande "passa" → pts[-1].
    pl_bi = (out.get("pl") or 0) / 1e9
    q_liq = _bucket(pl_bi, [0.5, 1.5, 3.0, 6.0], [4, 8, 11, 15])
    out["q_diluicao"] = q_dil
    out["q_consistencia"] = q_cons
    out["q_volatilidade"] = q_vol
    out["q_porte"] = q_liq
    out["score_qualidade"] = min(q_dil + q_cons + q_vol + q_liq, 100)
    return out


if __name__ == "__main__":
    from fii_client import carregar_fiis, _norm_cnpj
    alvos = {"11.728.688/0001-47": "HGLG11", "11.839.593/0001-09": "BTLG11",
             "28.757.546/0001-00": "XPML11", "17.554.274/0001-25": "VISC11"}
    base = carregar_fiis([2021, 2022, 2023, 2024, 2025])
    by = {_norm_cnpj(k): v for k, v in base.items()}
    print(f"DDM: r_nom={R_NOM*100:.1f}% g={G_NOM*100:.1f}% → DY justo {(R_NOM-G_NOM)*100:.1f}%\n")
    for cnpj, tk in alvos.items():
        f = by.get(_norm_cnpj(cnpj))
        if not f:
            print(f"{tk}: n/d"); continue
        r = avaliar_fii(f, tk)
        def pc(x): return f"{x*100:.1f}%" if x is not None else "—"
        print(f"{tk} ({r['classe']}) Q={r.get('score_qualidade')}  "
              f"[dil {r.get('q_diluicao')} cons {r.get('q_consistencia')} vol {r.get('q_volatilidade')} porte {r.get('q_porte')}]")
        print(f"   P/VP {r.get('pvp') and round(r['pvp'],2)} · DY {pc(r.get('dy'))} · vol {pc(r.get('vol_anual'))} · "
              f"VP/cota real {pc(r.get('vp_cota_real_cagr'))}/a · acretivo={r.get('acretivo')}")
        print(f"   preço R$ {r.get('preco')} · justo R$ {r.get('preco_justo') and round(r['preco_justo'],2)} · upside {pc(r.get('upside'))}")
