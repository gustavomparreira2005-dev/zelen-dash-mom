"""
Momentum Operacional v4 — Score ABSOLUTO A1–A5 (spec Zelen 2026-06-10).

Hierarquia: trajectória de crescimento primeiro, aceleração depois.

  A1 — Nível de Crescimento  (receita YoY mais recente)  0–15 pts
  A2 — Consistência          (% trimestres com +YoY)     0–15 pts
  A3 — Aceleração da Receita (g0 > g1 > g2)              0–10 pts
  A4 — Qualidade do Lucro    (EBITDA level + margem)     0–10 pts
  A5 — Solidez Financeira    (alavancagem + FCO)         0–10 pts

  Bloco A total = min(A1+A2+A3+A4+A5, 60)

Filtros de invalidação → score_operacional = None:
  · < 4 períodos disponíveis
  · PL negativo em 2+ trimestres consecutivos (exceto financeiros)
  · Empresa em recuperação judicial / liquidação

Entry point:
  calcular_score_operacional(resultado, serie_q) → dict com a1..a5, score_operacional, det_*
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# ─── Helpers de série trimestral ─────────────────────────────────────────────

def _yoy_growth(serie_q: List[Dict], campo: str, idx: int) -> Optional[float]:
    """
    Crescimento YoY para o ponto serie_q[idx] no campo indicado.
    Busca (ano-1, quarter) na série. Retorna None se ausente ou base < 1000.
    """
    if idx < 0 or idx >= len(serie_q):
        return None
    p = serie_q[idx]
    ano, q = p["ano"], p["quarter"]
    for pp in serie_q:
        if pp["ano"] == ano - 1 and pp["quarter"] == q:
            v_cur = p.get(campo)
            v_prv = pp.get(campo)
            if v_cur is None or v_prv is None:
                return None
            if abs(v_prv) < 1_000:
                return None
            return (v_cur - v_prv) / abs(v_prv)
    return None


def _get_growths(
    serie_q: List[Dict], campo: str
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Retorna (g0, g1, g2) — 3 crescimentos YoY mais recentes, g0 = mais atual."""
    n = len(serie_q)
    return (
        _yoy_growth(serie_q, campo, n - 1) if n >= 1 else None,
        _yoy_growth(serie_q, campo, n - 2) if n >= 2 else None,
        _yoy_growth(serie_q, campo, n - 3) if n >= 3 else None,
    )


# ─── A1: Nível de Crescimento ─────────────────────────────────────────────────
# Pergunta primária: a empresa está crescendo? E quanto?
# Usa o YoY mais recente disponível como proxy do crescimento corrente.

def _score_a1(serie_q: List[Dict]) -> Tuple[int, Dict]:
    g0, g1, g2 = _get_growths(serie_q, "receita")

    if g0 is None:
        pts = 0
    elif g0 > 0.15:
        pts = 15
    elif g0 > 0.08:
        pts = 12
    elif g0 > 0.03:
        pts = 8
    elif g0 > 0:
        pts = 4
    else:
        pts = 0

    return pts, {
        "g0":       round(g0 * 100, 1) if g0 is not None else None,
        "g1":       round(g1 * 100, 1) if g1 is not None else None,
        "g2":       round(g2 * 100, 1) if g2 is not None else None,
        "threshold": ">15% / 8-15% / 3-8% / 0-3% / <0%",
    }


# ─── A2: Consistência de Crescimento ─────────────────────────────────────────
# Pergunta: o crescimento é sustentado ou pontual?
# Conta quantos dos últimos N trimestres tiveram YoY positivo.

def _score_a2(serie_q: List[Dict]) -> Tuple[int, Dict]:
    n = len(serie_q)
    k = min(6, n)
    growths = [_yoy_growth(serie_q, "receita", n - 1 - i) for i in range(k)]
    growths_validos = [(g, i) for i, g in enumerate(growths) if g is not None]

    if not growths_validos:
        return 0, {"n_positivos": 0, "n_total": 0, "ratio": 0}

    total = len(growths_validos)
    positivos = sum(1 for g, _ in growths_validos if g > 0)
    ratio = positivos / total

    if ratio == 1.0:
        pts = 15
    elif ratio >= 5 / 6:
        pts = 12
    elif ratio >= 4 / 6:
        pts = 9
    elif ratio >= 3 / 6:
        pts = 5
    elif ratio >= 2 / 6:
        pts = 2
    else:
        pts = 0

    return pts, {
        "n_positivos": positivos,
        "n_total":     total,
        "ratio":       round(ratio * 100, 0),
        "growths_pct": [round(g * 100, 1) for g, _ in growths_validos],
    }


# ─── A3: Aceleração da Receita ────────────────────────────────────────────────
# Pergunta secundária: o ritmo de crescimento está aumentando?
# Só agrega valor quando a empresa já demonstrou crescimento (A1>0 e A2>0).

def _score_a3(serie_q: List[Dict]) -> Tuple[int, Dict]:
    g0, g1, g2 = _get_growths(serie_q, "receita")

    if g0 is None:
        pts = 0
    elif g0 > 0:
        if g1 is not None and g2 is not None and g0 > g1 > g2 and g1 > 0 and g2 > 0:
            pts = 10   # aceleração consistente, todos positivos
        elif g1 is not None and g0 > g1 and g1 > 0:
            pts = 7    # acelerando, g1 também positivo
        elif g1 is not None and g0 > g1:
            pts = 5    # acelerando saindo de negativo
        else:
            pts = 3    # crescendo mas sem aceleração
    else:
        if g1 is not None and g0 > g1:
            pts = 1    # negativo mas melhorando
        else:
            pts = 0

    return pts, {
        "g0": round(g0 * 100, 1) if g0 is not None else None,
        "g1": round(g1 * 100, 1) if g1 is not None else None,
        "g2": round(g2 * 100, 1) if g2 is not None else None,
    }


# ─── A4: Qualidade do Lucro ───────────────────────────────────────────────────
# EBITDA/EBIT YoY nível + tendência de margem.

def _score_a4(serie_q: List[Dict]) -> Tuple[int, Dict]:
    n = len(serie_q)

    # --- EBITDA/EBIT YoY: nível de crescimento do lucro operacional ---
    campo_eb = "ebitda" if any(p.get("ebitda") is not None for p in serie_q) else "ebit"
    g0_eb, _, _ = _get_growths(serie_q, campo_eb)

    if g0_eb is None:
        pts_eb = 0
    elif g0_eb > 0.10:
        pts_eb = 5
    elif g0_eb > 0:
        pts_eb = 3
    else:
        pts_eb = 0

    # --- Margem: delta YoY do trimestre mais recente ---
    campo_mg = ("margem_ebitda"
                if any(p.get("margem_ebitda") is not None for p in serie_q)
                else "margem_bruta")

    def _mg_delta(idx: int) -> Optional[float]:
        if idx < 0 or idx >= n:
            return None
        p = serie_q[idx]
        for pp in serie_q:
            if pp["ano"] == p["ano"] - 1 and pp["quarter"] == p["quarter"]:
                mc, mp = p.get(campo_mg), pp.get(campo_mg)
                if mc is not None and mp is not None:
                    return mc - mp
        return None

    d0 = _mg_delta(n - 1)

    # Penalidade: margem bruta expande mas EBITDA contrai → descontrole despesas
    penalidade = False
    if campo_mg == "margem_ebitda" and d0 is not None and d0 < 0:
        def _delta_bruta(idx: int) -> Optional[float]:
            p = serie_q[idx]
            for pp in serie_q:
                if pp["ano"] == p["ano"] - 1 and pp["quarter"] == p["quarter"]:
                    mc, mp = p.get("margem_bruta"), pp.get("margem_bruta")
                    if mc is not None and mp is not None:
                        return mc - mp
            return None
        db = _delta_bruta(n - 1) if n > 0 else None
        if db is not None and db > 0:
            penalidade = True

    if d0 is None:
        pts_mg = 0
    elif d0 > 1.0:
        pts_mg = 5    # margem expandindo >1pp
    elif d0 > 0:
        pts_mg = 3    # margem expandindo levemente
    elif d0 >= -1.0:
        pts_mg = 1    # margem estável (±1pp)
    else:
        pts_mg = 0

    if penalidade:
        pts_mg = max(0, pts_mg - 2)

    total = min(pts_eb + pts_mg, 10)
    return total, {
        "campo_eb":      campo_eb,
        "g0_eb_pct":     round(g0_eb * 100, 1) if g0_eb is not None else None,
        "pts_eb":        pts_eb,
        "campo_mg":      campo_mg,
        "delta_mg":      round(d0, 2) if d0 is not None else None,
        "pts_mg":        pts_mg,
        "penalidade":    penalidade,
    }


# ─── A5: Solidez Financeira ───────────────────────────────────────────────────

def _score_a5(resultado: Dict) -> Tuple[int, Dict]:
    ind       = resultado.get("ind") or {}
    serie_ltm = resultado.get("serie_ltm") or []
    campos    = resultado.get("campos_brutos") or {}
    setor     = (resultado.get("setor") or "").lower()
    eh_fin    = any(s in setor for s in ("financ", "banco", "segur", "insur"))

    dl_ebitda = ind.get("dl_ebitda")
    pl        = campos.get("pl") or 0.0

    if eh_fin:
        pts_nivel = 3
    elif pl < 0:
        pts_nivel = 0
    elif dl_ebitda is None:
        pts_nivel = 2
    elif dl_ebitda <= 0:
        pts_nivel = 4
    elif dl_ebitda <= 1.5:
        pts_nivel = 3
    elif dl_ebitda <= 2.5:
        pts_nivel = 2
    elif dl_ebitda <= 3.5:
        pts_nivel = 1
    else:
        pts_nivel = 0

    pts_tend = 1
    delta_dl = None
    if len(serie_ltm) >= 5 and not eh_fin:
        dl_now = (serie_ltm[-1].get("ind") or {}).get("dl_ebitda")
        dl_4q  = (serie_ltm[-5].get("ind") or {}).get("dl_ebitda")
        if dl_now is not None and dl_4q is not None:
            delta_dl = dl_now - dl_4q
            if delta_dl < -0.25:
                pts_tend = 2
            elif abs(delta_dl) <= 0.50:
                pts_tend = 1
            else:
                pts_tend = 0

    fco = campos.get("fco")
    ll  = campos.get("lucro_liq")
    pts_fco   = 0
    razao_fco = None
    fco_ausente = fco is None or fco <= 0

    if fco is not None and ll is not None and ll > 0 and fco > 0:
        razao_fco = fco / ll
        if razao_fco >= 1.0:
            pts_fco = 4
        elif razao_fco >= 0.70:
            pts_fco = 2

    total = pts_nivel + pts_tend + pts_fco
    if fco_ausente:
        total = min(total, 9)
    total = min(total, 10)

    return total, {
        "dl_ebitda":     round(dl_ebitda, 2) if dl_ebitda is not None else None,
        "pts_nivel":     pts_nivel,
        "pts_tendencia": pts_tend,
        "delta_dl":      round(delta_dl, 2) if delta_dl is not None else None,
        "pts_fco":       pts_fco,
        "razao_fco":     round(razao_fco, 2) if razao_fco is not None else None,
        "fco_ausente":   fco_ausente,
    }


# ─── Filtros de Invalidação ───────────────────────────────────────────────────

def _check_invalidacao(resultado: Dict, serie_q: List[Dict]) -> Optional[str]:
    ind    = resultado.get("ind") or {}
    sit    = (resultado.get("sit_emissor") or "").upper()
    setor  = (resultado.get("setor") or "").lower()
    eh_fin = any(s in setor for s in ("financ", "banco", "segur"))

    if ("RECUPER" in sit or "LIQUID" in sit or "FALENC" in sit
            or ind.get("gov_situacao") == 1.0):
        return "em recuperação judicial/liquidação"

    if not eh_fin:
        serie_ltm = resultado.get("serie_ltm") or []
        consec = mx = 0
        for pt in serie_ltm:
            if pt.get("pl_negativo"):
                consec += 1
                mx = max(mx, consec)
            else:
                consec = 0
        if mx >= 2:
            return "PL negativo em 2+ trimestres consecutivos"

    n_periodos = len(serie_q) + len(resultado.get("serie_ltm") or [])
    if n_periodos < 4:
        return "histórico insuficiente"

    campos  = resultado.get("campos_brutos") or {}
    ausentes = sum(1 for c in ("receita", "ebit", "lucro_liq") if not campos.get(c))
    if ausentes > 2:
        return f"{ausentes} campos obrigatórios ausentes"

    return None


# ─── Entry point ──────────────────────────────────────────────────────────────

def calcular_score_operacional(resultado: Dict, serie_q: List[Dict]) -> Dict:
    """
    Calcula o score operacional absoluto A1–A5 para uma empresa.

    Args:
        resultado: saída de calcular_indicadores(emp)
        serie_q:   saída de calcular_serie_trimestral(emp)

    Returns dict com:
        score_operacional (0-60 ou None), a1..a5, det_a1..det_a5, invalido
    """
    out: Dict = {
        "score_operacional": None,
        "a1": 0, "a2": 0, "a3": 0, "a4": 0, "a5": 0,
        "det_a1": {}, "det_a2": {}, "det_a3": {}, "det_a4": {}, "det_a5": {},
        "invalido": None,
    }

    razao = _check_invalidacao(resultado, serie_q)
    if razao:
        out["invalido"] = razao
        return out

    a1, d1 = _score_a1(serie_q)
    a2, d2 = _score_a2(serie_q)
    a3, d3 = _score_a3(serie_q)
    a4, d4 = _score_a4(serie_q)
    a5, d5 = _score_a5(resultado)

    out.update({
        "score_operacional": min(a1 + a2 + a3 + a4 + a5, 60),
        "a1": a1, "a2": a2, "a3": a3, "a4": a4, "a5": a5,
        "det_a1": d1, "det_a2": d2, "det_a3": d3, "det_a4": d4, "det_a5": d5,
    })
    return out
