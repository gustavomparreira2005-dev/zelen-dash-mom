"""
Score operacional (pilar A) adaptado para FINANCEIRAS.

O score A padrão (momentum_operacional) é construído sobre receita/EBIT/margem/
capital de giro — que não existem para banco. Aqui o A é reconstruído sobre os
drivers que importam para instituição financeira, mantendo a MESMA escala (0-60)
e o mesmo contrato de saída (score_operacional, a1..a5, det_a1..det_a5, invalido):

  A1 (0-15) Crescimento do lucro líquido (CAGR histórico)
  A2 (0-12) Consistência (anos de lucro positivo e crescente)
  A3 (0-10) Aceleração (último ano vs média histórica)
  A4 (0-13) Rentabilidade (nível de ROE)
  A5 (0-10) Solidez (composição de capital crescente + lucro positivo)
"""

from __future__ import annotations

from typing import Dict, List, Optional


def _cagr(serie: List[float]) -> Optional[float]:
    s = [x for x in serie if x is not None]
    if len(s) < 2 or s[0] <= 0 or s[-1] <= 0:
        return None
    return (s[-1] / s[0]) ** (1 / (len(s) - 1)) - 1


def _bucket(x: Optional[float], cortes: List[float], pontos: List[int]) -> int:
    """Pontua x por faixas: pontos[i] se x < cortes[i]; último ponto se acima de tudo."""
    if x is None:
        return 0
    for c, p in zip(cortes, pontos):
        if x < c:
            return p
    return pontos[-1]


def calcular_score_operacional_financeira(res: Dict) -> Dict:
    ind = res.get("ind") or {}
    hist = [h for h in ((res.get("historico_brutos") or {}).get("lucro_liq") or [])
            if h is not None]
    hist_pl = [h for h in ((res.get("historico_brutos") or {}).get("pl") or [])
               if h is not None]
    roe = ind.get("roe")            # %

    out: Dict = {
        "score_operacional": None,
        "a1": 0, "a2": 0, "a3": 0, "a4": 0, "a5": 0,
        "det_a1": {}, "det_a2": {}, "det_a3": {}, "det_a4": {}, "det_a5": {},
        "invalido": None,
    }
    if len(hist) < 2 or roe is None:
        out["invalido"] = "histórico financeiro insuficiente"
        return out

    # ── A1 Crescimento do lucro (CAGR) ─────────────────────────────────────────
    cagr = _cagr(hist)
    a1 = _bucket(cagr, [0.0, 0.05, 0.10, 0.15, 0.25], [0, 5, 9, 12, 14]) if cagr is not None else 3
    if cagr is None and hist[-1] > 0:
        a1 = 5                       # lucrativo mas CAGR indefinido (algum ano negativo)
    out["a1"] = a1
    out["det_a1"] = {"cagr_lucro": round(cagr * 100, 1) if cagr is not None else None}

    # ── A2 Consistência (passos YoY positivos + anos lucrativos) ───────────────
    passos = sum(1 for i in range(1, len(hist)) if hist[i] > hist[i - 1])
    lucrativos = sum(1 for x in hist if x > 0)
    frac = (passos / (len(hist) - 1)) * 0.6 + (lucrativos / len(hist)) * 0.4
    a2 = round(frac * 12)
    out["a2"] = a2
    out["det_a2"] = {"passos_cresc": passos, "anos_lucro": lucrativos, "anos": len(hist)}

    # ── A3 Aceleração (último YoY vs média dos anteriores) ─────────────────────
    a3 = 5
    if len(hist) >= 3 and all(h > 0 for h in hist[-3:]):
        yoy_ult = hist[-1] / hist[-2] - 1
        yoy_med = (hist[-2] / hist[-3] - 1)
        a3 = _bucket(yoy_ult - yoy_med, [-0.05, 0.0, 0.05, 0.15], [2, 5, 7, 9])
    out["a3"] = a3

    # ── A4 Rentabilidade (ROE) ─────────────────────────────────────────────────
    a4 = _bucket(roe, [8.0, 12.0, 16.0, 20.0, 28.0], [2, 5, 8, 11, 13])
    out["a4"] = a4
    out["det_a4"] = {"roe": round(roe, 1)}

    # ── A5 Solidez (capital compondo + lucro positivo) ─────────────────────────
    pl_cagr = _cagr(hist_pl)
    a5 = 4
    if hist[-1] > 0:
        a5 += 3                      # lucrativo no último ano
    a5 += _bucket(pl_cagr, [0.0, 0.05, 0.12], [0, 1, 2]) if pl_cagr is not None else 1
    a5 = min(a5, 10)
    out["a5"] = a5
    out["det_a5"] = {"pl_cagr": round(pl_cagr * 100, 1) if pl_cagr is not None else None}

    out["score_operacional"] = min(a1 + a2 + a3 + a4 + a5, 60)
    return out
