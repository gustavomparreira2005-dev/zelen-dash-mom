"""
Momentum Técnico v4 — Score ABSOLUTO B1–B3 (revisão 2026-06-12).

  B1 — Posição relativa ao máximo 52s  0–20 pts  (18 + 2 bônus)
  B2 — Momentum de preço 12-1          0–12 pts
  B3 — Estrutura de médias móveis      0–10 pts  (5 condições × 2 pts)

  Score B = B1 + B2 + B3   (máx teórico 42 — não é capado)

REVISÃO v4: o pilar de Volume (antigo B4) foi RETIRADO do score. O estudo de
fator por quartis (factor_study.py, 7 anos) mostrou que o volume não tinha poder
preditivo isolado (t=0,25, IC-IR 0,08, não-monotônico). Seus 5 pontos foram
realocados aos pilares com valor comprovado: +3 em B1 (52s, maior IC) e +2 em B2
(momentum 12-1). O volume 20d/60d segue calculado, mas apenas como informação.

Entry point:
  calcular_score_tecnico(series, bench) → dict com b1..b3, score_tecnico, det_b1..det_b3
  (det_b4 = ratio de volume informativo, não pontua)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from price_client import PriceSeries

# Janelas em pregões (aprox.)
_D1M, _D3M, _D6M, _D12M = 21, 63, 126, 252


# ─── Math helpers ────────────────────────────────────────────────────────────

def _ret(close: List[float], dias: int) -> Optional[float]:
    if len(close) <= dias:
        return None
    p0 = close[-1 - dias]
    if not p0:
        return None
    return (close[-1] / p0 - 1) * 100


def _ma(close: List[float], janela: int) -> Optional[float]:
    if len(close) < janela:
        return None
    return sum(close[-janela:]) / janela


def _ma_n_ago(close: List[float], janela: int, n_pregoes_atras: int) -> Optional[float]:
    """Média móvel de `janela` dias calculada `n_pregoes_atras` pregões atrás."""
    fim = len(close) - n_pregoes_atras
    if fim < janela:
        return None
    return sum(close[fim - janela: fim]) / janela


def _vol_avg(volume: List[float], dias: int) -> Optional[float]:
    vols = [v for v in volume[-dias:] if v and v > 0]
    return sum(vols) / len(vols) if vols else None


# ─── B1: Posição relativa ao máximo de 52 semanas ────────────────────────────

def _score_b1(series: PriceSeries) -> Tuple[int, Dict]:
    c = series.close
    if not c:
        return 0, {}

    last = c[-1]
    high52 = series.week52_high or (max(c[-_D12M:]) if len(c) >= _D12M else max(c))
    if not high52 or not last:
        return 0, {}

    ratio = last / high52

    # Tiers ampliados na v4 (base 0–18 + bônus 2 = 0–20); absorveu +3 do antigo B4.
    if ratio >= 0.90:
        pts = 18
    elif ratio >= 0.80:
        pts = 14
    elif ratio >= 0.70:
        pts = 10
    elif ratio >= 0.60:
        pts = 5
    else:
        pts = 0

    # Bônus: máximo atingido há menos de 3 meses (≈ 63 pregões)
    bonus = 0
    n_busca = min(_D12M, len(c))
    idx_max = max(range(len(c) - n_busca, len(c)), key=lambda i: c[i])
    dias_desde_max = len(c) - 1 - idx_max
    if dias_desde_max <= 63:
        bonus = 2

    total = pts + bonus
    return total, {
        "ratio_52w":       round(ratio, 3),
        "high52":          round(high52, 2),
        "last_price":      round(last, 2),
        "bonus_max_recente": bonus > 0,
        "dias_desde_max":  dias_desde_max,
    }


# ─── B2: Momentum de preço 12-1 ──────────────────────────────────────────────

def _score_b2(series: PriceSeries) -> Tuple[int, Dict]:
    c = series.close
    if len(c) <= _D12M:
        return 0, {"mom_12_1": None}

    p12 = c[-1 - _D12M]
    p1  = c[-1 - _D1M]
    if not p12 or not p1:
        return 0, {"mom_12_1": None}

    mom = (p1 / p12 - 1) * 100

    # Tiers ampliados na v4 (0–12); absorveu +2 do antigo B4.
    if mom > 40:
        pts = 12
    elif mom > 20:
        pts = 9
    elif mom > 10:
        pts = 6
    elif mom >= 0:
        pts = 4
    elif mom >= -10:
        pts = 2
    else:
        pts = 0

    return pts, {"mom_12_1": round(mom, 1)}


# ─── B3: Estrutura de médias móveis ──────────────────────────────────────────

def _score_b3(series: PriceSeries) -> Tuple[int, Dict]:
    c    = series.close
    last = c[-1] if c else None
    if not last:
        return 0, {}

    ma50  = _ma(c, 50)
    ma150 = _ma(c, 150)
    ma200 = _ma(c, 200)
    ma200_4w = _ma_n_ago(c, 200, 20)   # MM200 calculada 4 semanas atrás

    low52 = series.week52_low or (min(c[-_D12M:]) if len(c) >= _D12M else min(c))

    # 5 condições × 2 pts cada
    c1 = last > ma200  if (ma200 and last)                              else False
    c2 = (ma50 > ma150 > ma200)  if (ma50 and ma150 and ma200)         else False
    c3 = (ma200 > ma200_4w)      if (ma200 and ma200_4w)               else False
    c4 = (last > low52 * 1.30)   if (last and low52)                   else False
    c5 = (ma150 > ma200)         if (ma150 and ma200)                  else False

    pts = sum(2 for cond in (c1, c2, c3, c4, c5) if cond)
    return pts, {
        "c1_px_gt_ma200":    c1,
        "c2_ma50_ma150_ma200": c2,
        "c3_ma200_slope_pos": c3,
        "c4_px_gt_low52_30": c4,
        "c5_ma150_gt_ma200":  c5,
        "ma50":   round(ma50, 2)  if ma50  else None,
        "ma150":  round(ma150, 2) if ma150 else None,
        "ma200":  round(ma200, 2) if ma200 else None,
    }


# ─── Volume (informativo — RETIRADO do score na v4) ──────────────────────────

def _vol_info(series: PriceSeries) -> Dict:
    """Razão de volume 20d/60d, apenas para exibição. Não pontua (ver docstring)."""
    vol = series.volume
    if not vol or all(v == 0 for v in vol):
        return {"vol_ratio": None, "neutro": True}
    vol20 = _vol_avg(vol, 20)
    vol60 = _vol_avg(vol, 60)
    if not vol20 or not vol60:
        return {"vol_ratio": None, "neutro": True}
    return {"vol_ratio": round(vol20 / vol60, 2)}


# ─── Entry point ──────────────────────────────────────────────────────────────

def calcular_score_tecnico(
    series: PriceSeries,
    bench: Optional[PriceSeries] = None,
) -> Dict:
    """
    Calcula o score técnico absoluto B1–B4.

    Args:
        series: PriceSeries da ação
        bench:  PriceSeries do Ibovespa (opcional, para força relativa)

    Returns dict com:
        score_tecnico (0-42, = B1+B2+B3), b1..b3, det_b1..det_b3,
        det_b4 (volume informativo, não pontua),
        spark_close (série de preço para sparkline), metricas_tec (backward compat)
    """
    if not series.ok or len(series.close) < 30:
        return {
            "score_tecnico": None,
            "b1": 0, "b2": 0, "b3": 0, "b4": 0,
            "det_b1": {}, "det_b2": {}, "det_b3": {}, "det_b4": {},
        }

    b1, d1 = _score_b1(series)
    b2, d2 = _score_b2(series)
    b3, d3 = _score_b3(series)
    d4 = _vol_info(series)          # volume: informativo, NÃO entra no score

    # Força relativa vs benchmark (para exibição, não entra no score B)
    rel_str_6m = None
    if bench and bench.ok:
        r_acao  = _ret(series.close, _D6M)
        r_bench = _ret(bench.close, _D6M)
        if r_acao is not None and r_bench is not None:
            rel_str_6m = round(r_acao - r_bench, 1)

    # Retornos para exibição
    ret_3m  = _ret(series.close, _D3M)
    ret_12m = _ret(series.close, _D12M)

    return {
        "score_tecnico": b1 + b2 + b3,        # v4: volume retirado do score
        "b1": b1, "b2": b2, "b3": b3, "b4": 0,
        "det_b1": d1, "det_b2": d2, "det_b3": d3, "det_b4": d4,
        # Campos extras para display
        "metricas_tec": {
            "mom_12_1":    d2.get("mom_12_1"),
            "ret_3m":      round(ret_3m, 1)  if ret_3m  is not None else None,
            "ret_12m":     round(ret_12m, 1) if ret_12m is not None else None,
            "rel_str_6m":  rel_str_6m,
            "dist_52w_high": round((d1.get("ratio_52w") or 1) * 100 - 100, 1),
            "px_ma200":    round(
                (series.close[-1] / (d3.get("ma200") or series.close[-1]) - 1) * 100, 1
            ) if d3.get("ma200") else None,
        },
    }
