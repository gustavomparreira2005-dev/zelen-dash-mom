"""
Gerador de HTML — Screener de Momentum de Ações (Zelen Invest).

Tabela estilo TradingView: cada ação é uma linha com os scores (A operacional,
B técnico, Total) tratados como indicadores, ao lado de fundamentais de mercado
(Mkt Cap, EV/EBITDA anualizado, P/L anualizado, Cresc. médio tri a tri, Liq. 2m,
ROE, DL/PL). Colunas ordenáveis por clique e construtor de filtros (indicador +
operador + valor, múltiplos filtros acumuláveis). Cada linha expande para o
detalhe A1-A5 / B1-B3.

Classificação do score total:
  80-100  Forte · 65-79 Positivo · 50-64 Neutro · 35-49 Fraco · 0-34 Negativo
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_CSS = """
:root{
  --verde:#284B23;--verde-esc:#1A3018;--verde-med:#3D6B36;--verde-cl:#5A8C52;
  --marrom:#B3703C;--marrom-esc:#8A5530;
  --areia:#F3E5D0;--areia-cl:#FAF4EB;--areia-esc:#E8D4B8;
  --bg-base:#F3E5D0;--bg-card:#FAF4EB;
  --text-pri:#1A2E17;--text-sec:#4A5E47;--text-mut:#8A9E87;--text-inv:#F3E5D0;
  --border:#D4C4A8;--verm:#8B2020;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:"Inter","Segoe UI",sans-serif;background:var(--bg-base);color:var(--text-pri);font-size:13px;padding:0 0 60px;}
.wrap{max-width:1640px;margin:0 auto;padding:0 20px;}
header.top{background:var(--verde-esc);color:var(--areia);padding:22px 0;margin-bottom:18px;border-bottom:4px solid var(--marrom);}
header.top .wrap{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px;}
header.top h1{font-size:22px;font-weight:700;letter-spacing:.3px;}
header.top h1 span{color:var(--marrom);}
header.top .sub{font-size:12px;color:rgba(243,229,208,.7);font-weight:500;}
.panel{background:var(--bg-card);border:1px solid var(--border);border-radius:10px;overflow:visible;box-shadow:0 1px 3px rgba(0,0,0,.06);}
.panel-head{padding:13px 18px;background:var(--verde);color:var(--areia);display:flex;align-items:center;justify-content:space-between;border-radius:9px 9px 0 0;}
.panel-head h2{font-size:14px;font-weight:700;}
.panel-head .tag{font-size:10px;font-weight:600;background:rgba(243,229,208,.18);padding:3px 9px;border-radius:10px;letter-spacing:.4px;}

/* ── Construtor de filtros (screener) ───────────────────────────────────── */
.screener{padding:14px 18px;border-bottom:1px solid var(--border);background:#F7F2E8;}
.filt-build{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}
.filt-build select,.filt-build input{font-family:inherit;font-size:12px;padding:6px 9px;border:1px solid var(--border);border-radius:6px;background:#fff;color:var(--text-pri);}
.filt-build input[type=number]{width:130px;}
.filt-build .funit{font-size:12px;font-weight:600;color:var(--text-sec);min-width:38px;}
.filt-build .q{flex:1;min-width:160px;}
.btn{font-family:inherit;font-size:12px;font-weight:600;padding:6px 13px;border:none;border-radius:6px;background:var(--verde);color:var(--areia);cursor:pointer;transition:background .12s;}
.btn:hover{background:var(--verde-med);}
.btn.ghost{background:transparent;color:var(--text-sec);border:1px solid var(--border);}
.btn.ghost:hover{background:var(--areia-esc);}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px;min-height:0;}
.chip{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:600;background:var(--verde);color:var(--areia);padding:4px 6px 4px 11px;border-radius:14px;}
.chip b{font-weight:700;}
.chip .x{cursor:pointer;width:16px;height:16px;line-height:15px;text-align:center;border-radius:50%;background:rgba(243,229,208,.22);font-size:12px;}
.chip .x:hover{background:rgba(243,229,208,.4);}
.chips:empty{display:none;}

table{width:100%;border-collapse:collapse;}
th{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--text-mut);text-align:right;padding:9px 9px;border-bottom:2px solid var(--border);font-weight:700;white-space:nowrap;position:sticky;top:0;z-index:5;background:var(--bg-card);box-shadow:0 2px 0 var(--border);}
th.l,td.l{text-align:left;}
th.sortable{cursor:pointer;user-select:none;}
th.sortable:hover{color:var(--text-sec);}
th .arr{display:inline-block;width:9px;color:var(--marrom);font-size:9px;}
td{padding:8px 9px;text-align:right;border-bottom:1px solid #EFE6D6;font-variant-numeric:tabular-nums;vertical-align:middle;white-space:nowrap;}
tr.row{cursor:pointer;transition:background .1s;}
tr.row:hover{background:var(--areia-esc);}
.rank{color:var(--text-mut);font-weight:700;width:30px;}
.tk{font-weight:700;color:var(--verde);font-size:13px;}
.nm{color:var(--text-sec);font-size:10px;}
.setor-cel{font-size:10px;color:var(--text-sec);font-weight:600;max-width:130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.filt-build .fsetor{max-width:170px;}
.pos{color:var(--verde-med);font-weight:600;}
.neg{color:var(--verm);font-weight:600;}
.mut{color:var(--text-mut);}
.blk{display:inline-flex;align-items:center;gap:5px;justify-content:flex-end;}
.blk .track{display:block;height:7px;border-radius:4px;overflow:hidden;background:var(--areia-esc);}
.blk .fill{display:block;height:100%;border-radius:4px;}
.blk b{font-weight:700;min-width:22px;text-align:right;font-size:12px;}
.badge{display:inline-flex;align-items:center;gap:7px;justify-content:flex-end;}
.badge .track{display:block;width:46px;height:8px;background:var(--areia-esc);border-radius:4px;overflow:hidden;}
.badge .fill{display:block;height:100%;border-radius:4px;}
.badge b{font-weight:700;font-size:14px;min-width:26px;text-align:right;}
.badge .cl{font-size:9px;font-weight:700;padding:2px 6px;border-radius:8px;white-space:nowrap;}
.cl-forte {background:#D4EDDA;color:#155724;}
.cl-pos   {background:#D1ECF1;color:#0C5460;}
.cl-neutro{background:#FFF3CD;color:#856404;}
.cl-fraco {background:#FDEBD0;color:#7D4E00;}
.cl-neg   {background:#F8D7DA;color:#721C24;}
.det{display:none;background:#F7F2E8;}
.det.open{display:table-row;}
.det td{padding:0;border-bottom:1px solid var(--border);}
.det-inner{padding:14px 18px;display:grid;grid-template-columns:1fr 1fr;gap:20px;}
@media(max-width:900px){.det-inner{grid-template-columns:1fr;}}
.bloco-det h4{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;color:var(--text-sec);}
.sub-rows{display:flex;flex-direction:column;gap:5px;}
.sub-row{display:grid;grid-template-columns:150px 1fr 44px;align-items:center;gap:6px;}
.sub-lbl{font-size:11px;color:var(--text-sec);}
.sub-bar{height:6px;background:var(--areia-esc);border-radius:3px;overflow:hidden;}
.sub-fill{height:100%;border-radius:3px;}
.sub-pts{font-size:11px;font-weight:700;text-align:right;}
.sub-hint{font-size:10px;color:var(--text-mut);margin-top:1px;padding-left:2px;grid-column:2/-1;}
.inv-msg{font-size:11px;color:var(--verm);font-style:italic;}
.spark{margin-top:10px;}
.legend{font-size:11px;color:var(--text-sec);padding:10px 18px 16px;border-top:1px solid var(--border);line-height:1.6;}
footer{text-align:center;color:var(--text-mut);font-size:11px;margin-top:30px;}
/* ── Tabs ──────────────────────────────────────────────────────────────────── */
.tab-bar{display:flex;background:var(--bg-card);border-bottom:2px solid var(--border);padding:0 18px;}
.tab-btn{font-family:inherit;font-size:13px;font-weight:600;padding:11px 16px;border:none;background:none;cursor:pointer;color:var(--text-sec);border-bottom:3px solid transparent;margin-bottom:-2px;transition:color .12s,border-color .12s;}
.tab-btn.active{color:var(--verde);border-bottom-color:var(--verde);}
.tab-btn:hover:not(.active){color:var(--text-pri);}
.tab-pane{display:none;}
.tab-pane.active{display:block;}
/* ── Valuation — busca + visão única ───────────────────────────────────────── */
.val-search-wrap{padding:18px 18px 10px;background:var(--bg-card);}
.val-search-box{position:relative;}
.val-input{width:100%;font-family:inherit;font-size:15px;padding:11px 16px;border:1.5px solid var(--border);border-radius:9px;background:#fff;color:var(--text-pri);outline:none;transition:border-color .15s;}
.val-input:focus{border-color:var(--verde);}
.val-drop{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1px solid var(--border);border-radius:8px;max-height:280px;overflow-y:auto;z-index:200;display:none;box-shadow:0 6px 18px rgba(0,0,0,.12);}
.val-drop.show{display:block;}
.val-drop-item{padding:9px 16px;cursor:pointer;font-size:13px;border-bottom:1px solid #F0EAE0;display:flex;align-items:baseline;gap:10px;}
.val-drop-item:last-child{border-bottom:none;}
.val-drop-item:hover,.val-drop-item.sel{background:var(--areia-esc);}
.val-drop-item .dtk{font-weight:700;color:var(--verde);font-size:14px;min-width:52px;}
.val-drop-item .dnm{color:var(--text-sec);font-size:12px;}
.val-drop-item .dup{margin-left:auto;font-size:11px;font-weight:700;}
.val-count{font-size:11px;color:var(--text-mut);margin-top:7px;}
.val-empty{padding:70px 18px;text-align:center;}
.val-empty-icon{font-size:48px;margin-bottom:14px;opacity:.5;}
.val-empty-txt{font-size:17px;font-weight:600;color:var(--text-sec);}
.val-empty-sub{font-size:12px;color:var(--text-mut);margin-top:8px;line-height:1.6;}
.val-view{padding:0 18px 24px;}
.val-vh{padding:18px 0 16px;border-bottom:2px solid var(--border);margin-bottom:20px;display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:10px;}
.val-vh h3{font-size:24px;font-weight:800;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
.val-vh-meta{font-size:12px;color:var(--text-mut);margin-top:5px;}
.val-badge{font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;white-space:nowrap;}
.val-at{background:#D4EDDA;color:#155724;}
.val-ok{background:#D1ECF1;color:#0C5460;}
.val-ne{background:#FFF3CD;color:#856404;}
.val-ca{background:#F8D7DA;color:#721C24;}
.val-layout{display:grid;grid-template-columns:1.45fr 1fr;gap:18px;}
.val-block{background:var(--areia-cl);border:1px solid var(--border);border-radius:9px;overflow:hidden;}
.val-bt{padding:9px 16px;background:var(--verde);color:var(--areia);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;}
.val-bc{padding:16px;}
.val-anc-row{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px;padding-top:14px;border-top:1px solid var(--border);}
.val-anc-item .val-anc-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-mut);font-weight:700;}
.val-anc-item .val-anc-val{font-size:16px;font-weight:700;margin-top:3px;}
.val-proj-table{width:100%;border-collapse:collapse;font-size:12px;margin-top:14px;}
.val-proj-table th{font-size:9px;text-transform:uppercase;letter-spacing:.4px;color:var(--text-mut);font-weight:700;padding:0 8px 7px 0;text-align:right;border-bottom:1px solid var(--border);}
.val-proj-table th:first-child{text-align:left;}
.val-proj-table td{padding:6px 8px 6px 0;text-align:right;border-bottom:1px solid #EFE6D6;font-variant-numeric:tabular-nums;}
.val-proj-table td:first-child{text-align:left;font-weight:700;color:var(--verde);}
.val-proj-table tr:last-child td{border-bottom:none;}
.val-prem-bar{font-size:11px;color:var(--text-sec);padding-top:12px;margin-top:12px;border-top:1px solid var(--border);line-height:1.7;}
.val-note{font-size:11px;color:var(--text-sec);padding:12px 18px 16px;border-top:1px solid var(--border);line-height:1.6;font-style:italic;background:#F7F2E8;}
.val-tk{font-size:20px;font-weight:800;color:var(--verde);letter-spacing:-.5px;}
.val-nm{font-size:13px;color:var(--text-sec);font-weight:500;}
.v-anc-lbl{font-size:10px;color:var(--text-mut);margin-bottom:3px;}
.v-anc-val{font-size:15px;font-weight:700;}
.v-prem-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;padding-top:4px;}
.v-prem-row{display:flex;align-items:center;gap:6px;font-size:12px;}
.v-prem-row label{color:var(--text-sec);font-size:11px;min-width:72px;white-space:nowrap;}
.v-unit{font-size:10px;color:var(--text-mut);white-space:nowrap;}
.v-inp{width:62px;font-family:inherit;font-size:12px;font-weight:600;padding:4px 7px;border:1.5px solid var(--border);border-radius:6px;background:#fff;color:var(--verde);outline:none;text-align:right;transition:border-color .12s;}
.v-inp:focus{border-color:var(--verde);}
.w-out-box{margin-top:12px;padding-top:11px;border-top:1px solid var(--border);}
.w-line{font-size:11px;color:var(--text-sec);line-height:1.6;}
.w-final{font-size:20px;font-weight:800;color:var(--verde);margin:5px 0 1px;}
.w-final span{color:var(--marrom);}
.val-proj-table td{padding:5px 6px 5px 0;text-align:right;border-bottom:1px solid #EFE6D6;font-size:12px;}
.val-proj-table td:first-child{text-align:left;font-weight:700;color:var(--verde);}
/* — Aba Valuation v2: layout IB (largura cheia · anos em colunas · WACC vertical) — */
.dcf-wrap{overflow-x:auto;margin-top:4px;}
.dcf-tbl{width:100%;border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;}
.dcf-tbl th{font-size:9px;text-transform:uppercase;letter-spacing:.3px;color:var(--text-mut);font-weight:700;padding:4px 9px 7px;text-align:right;border-bottom:1.5px solid var(--border);white-space:nowrap;}
.dcf-tbl th:first-child{text-align:left;}
.dcf-tbl th.hist{color:var(--marrom);}
.dcf-tbl th.proj{color:var(--verde);}
.dcf-tbl td{padding:4px 9px;text-align:right;border-bottom:1px solid #EFE6D6;white-space:nowrap;}
.dcf-tbl td.lbl{text-align:left;color:var(--text-sec);font-weight:600;}
.dcf-tbl tr.grp td{background:var(--areia-cl);font-weight:700;color:var(--verde);font-size:9px;text-transform:uppercase;letter-spacing:.4px;}
.dcf-tbl tr.tot td{border-top:1.5px solid var(--border);font-weight:800;color:var(--verde);}
.dcf-tbl td.hcol{background:rgba(120,90,60,.05);}
.dcf-tbl td.muted{color:var(--text-mut);}
.dcf-tbl .v-inp{width:52px;padding:2px 5px;font-size:11px;}
.val-bottom{display:grid;grid-template-columns:0.85fr 1fr 1.05fr;gap:16px;margin-top:16px;}
.kv-tbl{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums;}
.kv-tbl td{padding:5px 4px;border-bottom:1px solid #EFE6D6;}
.kv-tbl td.r{text-align:right;font-weight:600;}
.kv-tbl tr.calc td{color:var(--marrom);}
.kv-tbl tr.sub td{color:var(--text-mut);}
.kv-tbl tr.fin td{border-top:1.5px solid var(--border);border-bottom:none;font-weight:800;color:var(--verde);font-size:14px;padding-top:8px;}
.kv-tbl .v-inp{width:58px;}
.drv-row{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:11px;padding:3px 0;}
.drv-row label{color:var(--text-sec);white-space:nowrap;}
.drv-row .u{color:var(--text-mut);font-size:9px;}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _br(v: Optional[float], dec: int = 1) -> str:
    """Formata número no padrão brasileiro (vírgula decimal)."""
    if v is None:
        return "—"
    s = f"{v:,.{dec}f}"               # 1,234.5
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _cor_bloco(pct: float) -> str:
    if pct >= 0.75: return "var(--verde)"
    if pct >= 0.50: return "var(--verde-cl)"
    if pct >= 0.30: return "var(--marrom)"
    return "var(--verm)"


def _classificacao(score: int) -> tuple:
    if score >= 80: return "Forte",    "cl-forte"
    if score >= 65: return "Positivo", "cl-pos"
    if score >= 50: return "Neutro",   "cl-neutro"
    if score >= 35: return "Fraco",    "cl-fraco"
    return "Negativo", "cl-neg"


def _mini_barra(pts: int, maximo: int, largura: int = 44) -> str:
    pct = max(0.0, min(pts / maximo, 1.0)) if maximo else 0.0
    cor = _cor_bloco(pct)
    return (f'<span class="blk">'
            f'<span class="track" style="width:{largura}px">'
            f'<span class="fill" style="width:{pct*100:.0f}%;background:{cor}"></span></span>'
            f'<b style="color:{cor}">{pts}</b></span>')


def _badge_total(score: Optional[int]) -> str:
    if score is None:
        return '<span class="mut">—</span>'
    lbl, cls = _classificacao(score)
    cor = _cor_bloco(score / 100)
    return (f'<span class="badge">'
            f'<span class="cl {cls}">{lbl}</span>'
            f'<span class="track"><span class="fill" style="width:{score}%;background:{cor}"></span></span>'
            f'<b style="color:{cor}">{score}</b></span>')


def _sparkline(close: List[float], w: int = 200, h: int = 40) -> str:
    pts = [c for c in close if c is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    n = len(pts)
    coords = [(i / (n - 1) * w, h - (p - lo) / rng * (h - 4) - 2)
              for i, p in enumerate(pts)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    up = pts[-1] >= pts[0]
    cor = "var(--verde-med)" if up else "var(--verm)"
    area = (f"M{coords[0][0]:.1f},{h} L" +
            " L".join(f"{x:.1f},{y:.1f}" for x, y in coords) +
            f" L{coords[-1][0]:.1f},{h} Z")
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<path d="{area}" fill="{cor}" opacity="0.10"/>'
            f'<path d="{d}" fill="none" stroke="{cor}" stroke-width="1.6"/>'
            f'</svg>')


# ─── Células de indicador de mercado ──────────────────────────────────────────

def _cel_mktcap(v: Optional[float]) -> str:
    if v is None:
        return '<span class="mut">—</span>'
    if v >= 1e9:
        return f'{_br(v/1e9, 1)} bi'
    return f'{_br(v/1e6, 0)} mi'


def _cel_liq(v: Optional[float]) -> str:
    if v is None:
        return '<span class="mut">—</span>'
    if v >= 1e9:
        return f'{_br(v/1e9, 2)} bi'
    return f'{_br(v/1e6, 1)} mi'


def _cel_mult(v: Optional[float]) -> str:
    if v is None:
        return '<span class="mut">—</span>'
    cls = ' class="neg"' if v < 0 else ''
    return f'<span{cls}>{_br(v, 1)}x</span>'


def _cel_pct(v: Optional[float], sinal: bool = False) -> str:
    if v is None:
        return '<span class="mut">—</span>'
    cls = "pos" if v > 0 else ("neg" if v < 0 else "mut")
    pre = "+" if (sinal and v > 0) else ""
    return f'<span class="{cls}">{pre}{_br(v, 1)}%</span>'


def _data_num(v: Optional[float], escala: float = 1.0) -> str:
    """Valor numérico cru (decimal '.') para data-attr; '' quando ausente."""
    return "" if v is None else f"{v/escala:.4f}"


# ─── Sub-linha de detalhe ─────────────────────────────────────────────────────

def _sub(lbl: str, pts: int, maximo: int, hint: str = "") -> str:
    pct = max(0.0, min(pts / maximo, 1.0)) if maximo else 0.0
    cor = _cor_bloco(pct)
    h_row = f'<span class="sub-hint">{_esc(hint)}</span>' if hint else ""
    return (
        f'<div class="sub-row">'
        f'<span class="sub-lbl">{_esc(lbl)}</span>'
        f'<div class="sub-bar"><div class="sub-fill" style="width:{pct*100:.0f}%;background:{cor}"></div></div>'
        f'<span class="sub-pts" style="color:{cor}">{pts}/{maximo}</span>'
        f'</div>{h_row}'
    )


def _g(v) -> str:
    return f"{v:+.1f}%" if v is not None else "—"


def _det_op(e: Dict) -> str:
    """Detalhe do Bloco A — metodologia v4 (trajetória primeiro, aceleração depois)."""
    da1 = e.get("det_a1") or {}
    da2 = e.get("det_a2") or {}
    da3 = e.get("det_a3") or {}
    da4 = e.get("det_a4") or {}
    da5 = e.get("det_a5") or {}

    h1 = f"YoY g0={_g(da1.get('g0'))} · g1={_g(da1.get('g1'))} · g2={_g(da1.get('g2'))}"
    h2 = (f"{da2.get('n_positivos',0)}/{da2.get('n_total',0)} tri com +YoY "
          f"({da2.get('ratio',0):.0f}%)")
    h3 = f"g0={_g(da3.get('g0'))} · g1={_g(da3.get('g1'))} · g2={_g(da3.get('g2'))}"

    eb = da4.get("campo_eb", "ebitda")
    h4 = f"{eb} YoY {_g(da4.get('g0_eb_pct'))}"
    dmg = da4.get("delta_mg")
    if dmg is not None:
        h4 += f" · Δmargem {dmg:+.1f}pp"
    if da4.get("penalidade"):
        h4 += " ⚠ mg.bruta↑/EBITDA↓"

    dl = da5.get("dl_ebitda")
    h5 = (f"DL/EBITDA {dl:.1f}x" if dl is not None else "DL/EBITDA n/d")
    h5 += f" · tend {da5.get('pts_tendencia',0)}/2 · FCO {da5.get('pts_fco',0)}/4"

    return (
        '<div class="bloco-det"><h4>🏭 Bloco A — Operacional (trajetória → aceleração)</h4>'
        '<div class="sub-rows">'
        + _sub("A1 Nível Cresc.",   e.get("a1", 0), 15, h1)
        + _sub("A2 Consistência",   e.get("a2", 0), 15, h2)
        + _sub("A3 Aceleração",     e.get("a3", 0), 10, h3)
        + _sub("A4 Qualid. Lucro",  e.get("a4", 0), 10, h4)
        + _sub("A5 Solidez Financ.", e.get("a5", 0), 10, h5)
        + '</div></div>'
    )


def _det_tec(e: Dict) -> str:
    db1 = e.get("det_b1") or {}
    db2 = e.get("det_b2") or {}
    db3 = e.get("det_b3") or {}
    db4 = e.get("det_b4") or {}

    ratio = db1.get("ratio_52w")
    h1 = f"{ratio*100:.1f}% do máx 52s" if ratio else ""
    if db1.get("bonus_max_recente"):
        h1 += " +2 máx recente"
    mom = db2.get("mom_12_1")
    h2 = f"mom 12-1 = {mom:+.1f}%" if mom is not None else ""

    conds = [("P>MM200", "c1_px_gt_ma200"), ("MM50>MM150>MM200", "c2_ma50_ma150_ma200"),
             ("MM200 slope↑", "c3_ma200_slope_pos"), ("P>min+30%", "c4_px_gt_low52_30"),
             ("MM150>MM200", "c5_ma150_gt_ma200")]
    h3 = " ".join(("✓" if db3.get(k) else "✗") + lbl for lbl, k in conds)

    vr = db4.get("vol_ratio")
    h4 = f"vol 20d/60d = {vr:.2f}x" if vr else "vol n/d"
    if db4.get("neutro"):
        h4 = "sem dados de volume"

    spark = _sparkline(e.get("spark_close") or [])

    return (
        '<div class="bloco-det"><h4>📈 Bloco B — Técnico</h4>'
        '<div class="sub-rows">'
        + _sub("B1 Prox. Máx 52s", e.get("b1", 0), 20, h1)
        + _sub("B2 Momentum 12-1", e.get("b2", 0), 12, h2)
        + _sub("B3 Estrut. MMs",   e.get("b3", 0), 10, h3)
        + '</div>'
        + f'<div class="sub-hint" style="margin-top:6px;opacity:.7">Volume: {h4} '
          '<i>(informativo · retirado do score na v4)</i></div>'
        + (f'<div style="margin-top:8px">{spark}</div>' if spark else '')
        + '</div>'
    )


# ─── Linhas da tabela ─────────────────────────────────────────────────────────

def _cel_score(v) -> str:
    """Célula de score de estratégia (0-100): número colorido por força."""
    if v is None:
        return '<span class="mut">—</span>'
    c = "var(--verde-med)" if v >= 80 else "var(--marrom)" if v >= 60 else "var(--text-sec)"
    return f'<b style="color:{c}">{v:.0f}</b>'


def _linhas(itens: List[Dict], estrat: bool = False) -> str:
    # Ordena por score total desc (estado inicial; JS pode reordenar depois)
    ordenados = sorted(itens, key=lambda e: (e.get("score_total") is None,
                                              -(e.get("score_total") or 0)))
    cspan = 18 if estrat else 15
    rows = []
    rank = 0
    for e in ordenados:
        total = e.get("score_total")
        inv   = e.get("invalido")
        if total is None and not inv:
            continue
        rank += 1
        rid = f"r{_esc(e['ticker'])}"

        so = e.get("score_operacional") or 0
        st = e.get("score_tecnico") or 0
        mkt   = e.get("mkt_cap")
        ev    = e.get("ev_ebitda")
        pl    = e.get("pl")
        cqoq  = e.get("cagr3_norm")   # CAGR 3a normalizado (anti-M&A)
        liq   = e.get("liq_2m")
        roe   = e.get("roe")
        dlpl  = e.get("div_liq_pl")
        _tir  = e.get("val_tir")
        tir5a = _tir * 100 if _tir is not None else None   # fração → % p/ exibição
        plnorm = e.get("pl_norm")     # P/L sobre lucro normalizado (média 5a)
        trapf = e.get("trap_flags") or []

        # data-attrs (decimal '.') para sort/filtro client-side
        attrs = (
            f' data-det="{rid}"'
            f' data-ticker="{_esc(e["ticker"])}"'
            f' data-nome="{_esc((e.get("nome") or "")[:40])}"'
            f' data-total="{_data_num(total)}"'
            f' data-a="{_data_num(so)}"'
            f' data-b="{_data_num(st)}"'
            f' data-mktcap="{_data_num(mkt, 1e9)}"'
            f' data-evebitda="{_data_num(ev)}"'
            f' data-pl="{_data_num(pl)}"'
            f' data-plnorm="{_data_num(plnorm)}"'
            f' data-crescqoq="{_data_num(cqoq)}"'
            f' data-liq2m="{_data_num(liq, 1e6)}"'
            f' data-roe="{_data_num(roe)}"'
            f' data-divliqpl="{_data_num(dlpl)}"'
            f' data-tir5a="{_data_num(tir5a)}"'
            f' data-mayer="{_data_num(e.get("score_mayer"))}"'
            f' data-comp="{_data_num(e.get("score_compounder"))}"'
            f' data-boring="{_data_num(e.get("score_boring"))}"'
            f' data-setor="{_esc(e.get("segmento") or "")}"'
        )
        _setor = (e.get("segmento") or "").strip()
        trap_mark = (f'<span class="trap" title="{_esc(" · ".join(trapf))}">⚠</span>'
                     if trapf else "")

        if inv:
            cel_tot = f'<span class="inv-msg">{_esc(inv)}</span>'
            cel_A = cel_B = '<span class="mut">—</span>'
        else:
            cel_tot = _badge_total(total)
            cel_A   = _mini_barra(so, 60, 44)
            cel_B   = _mini_barra(st, 42, 40)

        rows.append(
            f'<tr class="row"{attrs} onclick="tog(this)">'
            f'<td class="rank">{rank}</td>'
            f'<td class="l"><span class="tk">{_esc(e["ticker"])}{trap_mark}</span><br>'
            f'<span class="nm">{_esc((e.get("nome") or "")[:26])}</span></td>'
            f'<td class="l setor-cel">{_esc(_setor) or "—"}</td>'
            f'<td>{cel_tot}</td>'
            f'<td>{cel_A}</td>'
            f'<td>{cel_B}</td>'
            f'<td>{_cel_mktcap(mkt)}</td>'
            f'<td>{_cel_mult(ev)}</td>'
            f'<td>{_cel_mult(pl)}</td>'
            f'<td>{_cel_mult(plnorm)}</td>'
            f'<td>{_cel_pct(cqoq, sinal=True)}</td>'
            f'<td>{_cel_liq(liq)}</td>'
            f'<td>{_cel_pct(roe)}</td>'
            f'<td>{_cel_mult(dlpl)}</td>'
            f'<td>{_cel_pct(tir5a, sinal=True)}</td>'
            + ((f'<td>{_cel_score(e.get("score_mayer"))}</td>'
                f'<td>{_cel_score(e.get("score_compounder"))}</td>'
                f'<td>{_cel_score(e.get("score_boring"))}</td>') if estrat else "")
            + f'</tr>'
            f'<tr class="det" id="{rid}"><td colspan="{cspan}"><div class="det-inner">'
            + (f'<div class="bloco-det"><span class="inv-msg">{_esc(inv)}</span></div><div></div>'
               if inv else _det_op(e) + _det_tec(e))
            + '</div></td></tr>'
        )

    if not rows:
        return (f'<tr><td colspan="{cspan}" style="padding:20px;text-align:center;'
                'color:var(--text-mut)">Sem dados.</td></tr>')
    return "".join(rows)


# ─── Cabeçalho ordenável ──────────────────────────────────────────────────────

def _th(label: str, key: str, sub: str = "", l: bool = False) -> str:
    cls = "sortable" + (" l" if l else "")
    sub_html = f'<br><span style="font-weight:400;font-size:9px">{sub}</span>' if sub else ""
    return (f'<th class="{cls}" data-key="{key}" onclick="sortBy(\'{key}\')">'
            f'{label}{sub_html} <span class="arr" id="arr-{key}"></span></th>')


# Opções do construtor de filtros: (chave data-attr, rótulo, unidade)
# A unidade casa com a escala do data-attr (ver _linhas): Mkt Cap em R$ bilhões,
# Liquidez em R$ milhões — assim "< 1 bi" é digitado como "1", sem zeros.
_INDICADORES = [
    ("total",    "Score Total",          ""),
    ("a",        "A · Operacional",      ""),
    ("b",        "B · Técnico",          ""),
    ("mktcap",   "Mkt Cap (R$ bi)",      "bi"),
    ("evebitda", "EV/EBITDA",            "x"),
    ("pl",       "P/L",                  "x"),
    ("plnorm",   "P/L norm. (média 5a)", "x"),
    ("crescqoq", "CAGR 3a norm. (%)", "%"),
    ("liq2m",    "Liquidez 2m (R$ mi)",  "mi"),
    ("roe",      "ROE (%)",              "%"),
    ("divliqpl", "Dív.Líq/PL",           "x"),
    ("tir5a",    "TIR 5a (%)",           "%"),
    ("mayer",    "Mayer 100B (0-100)",   ""),
    ("comp",     "Compounder+tend. (0-100)", ""),
    ("boring",   "Boring buy&hold (0-100)", ""),
]


# ─── Aba de Valuation — busca + visão única ───────────────────────────────────

def _val_tab_content(itens: List[Dict]) -> str:
    import json as _json

    com_val = [e for e in itens if e.get("val_tir") is not None]
    if not com_val:
        return ('<div style="padding:40px;text-align:center;color:var(--text-mut)">'
                'Nenhum modelo disponível.</div>')

    data: dict = {}
    for e in com_val:
        tk = e["ticker"]
        up = e.get("val_upside") or 0.0
        tir = e.get("val_tir") or 0.0
        data[tk] = {
            "nome":  (e.get("nome") or "")[:50],
            "preco": round(e.get("val_preco") or 0, 2),
            "pj":    round(e.get("val_preco_justo") or 0, 2),
            "upside": round(up * 100, 1),
            "upraw": round((e.get("val_upside_raw") or up) * 100, 1),
            "flag":  e.get("val_flag") or "",
            "roic":  round((e.get("val_roic") if e.get("val_roic") is not None else 0.12) * 100, 1),
            "anchor": round(e.get("val_anchor_mult") or 0, 1),
            "tir":   round(tir * 100, 1),
            "ev":    round(e.get("val_ev_ebit") or 0, 1),
            "saida": round(e.get("val_saida_mult") or 0, 1),
            "cagr":  round((e.get("val_cagr_hist") or 0) * 100, 1),
            "rec":   round(e.get("val_receita_ltm") or 0, 0),
            "ebt":   round(e.get("val_ebit_ltm") or 0, 0),
            "nd":    round(e.get("val_net_debt") or 0, 0),
            "mg":    round((e.get("val_margem") or 0) * 100, 1),
            "na":    round(e.get("val_n_acoes") or 0, 1),
            "desc":  round((e.get("val_desconto") or 0.15) * 100, 1),
            "by":    e.get("val_base_year") or 2026,
            "rev":   [round(v, 0) for v in (e.get("val_rev") or [])],
            "ebs":   [round(v, 0) for v in (e.get("val_ebit_ser") or [])],
            "cresc": [round(v * 100, 1) for v in (e.get("val_cresc") or [])],
            "mkt":   round((e.get("mkt_cap") or 0) / 1e9, 1),
            "score": e.get("score_total"),
            # ── Modelo FCFE (financeiras) ──
            "modelo": e.get("val_modelo") or "FCFF",
            "tipofin": e.get("tipo_financeira") or "",
            "roe":    round((e.get("val_roe") or 0) * 100, 1),
            "roeeff": round((e.get("val_roe_eff") or 0) * 100, 1),
            "re":     round((e.get("val_re") or e.get("val_wacc") or 0.15) * 100, 2),
            "ll":     round(e.get("val_ll_ltm") or 0, 0),
            "plv":    round(e.get("val_pl") or 0, 0),
            "pvp":    round(e.get("val_pvp_atual") or 0, 2),
            "pvpj":   round(e.get("val_pvp_justo") or 0, 2),
            "eqj":    round(e.get("val_equity_justo") or 0, 0),
            "llser":  [round(v, 0) for v in (e.get("val_ll_ser") or [])],
            "fcfeser":[round(v, 0) for v in (e.get("val_fcfe_ser") or [])],
            # ── Drivers do build-up FCFF (editáveis) ──
            "dapct":  round((e.get("val_da_pct") or 0) * 100, 1),
            "cxpct":  round((e.get("val_capex_pct") or 0) * 100, 1),
            "cgpct":  round((e.get("val_cogs_pct") or 0) * 100, 1),
            "dso":    round(e.get("val_dso") or 0, 0),
            "dio":    round(e.get("val_dio") or 0, 0),
            "dpo":    round(e.get("val_dpo") or 0, 0),
            "gperp":  round((e.get("val_g_perp") or 0.04) * 100, 1),
            "tv":     round(e.get("val_tv") or 0, 0),
            "saidaimpl": round(e.get("val_saida_impl") or 0, 1),
            # Histórico (até 3 anos) p/ o schedule horizontal estilo IB
            "hrev":   [round(v, 0) for v in (e.get("val_hist_rev") or [])],
            "hebit":  [round(v, 0) for v in (e.get("val_hist_ebit") or [])],
            "hda":    [round(v, 0) for v in (e.get("val_hist_da") or [])],
            "hcapex": [round(v, 0) for v in (e.get("val_hist_capex") or [])],
            # WACC (componentes editáveis)
            "wacc":   round((e.get("val_wacc") or 0.15) * 100, 2),
            "beta":   round(e.get("val_beta") or 1.0, 2),
            "betaraw": (round(e["val_beta_raw"], 2) if e.get("val_beta_raw") is not None else None),
            "rf":     round((e.get("val_wacc_rf") or 0.105) * 100, 1),
            "erp":    round((e.get("val_wacc_erp") or 0.075) * 100, 1),
            "rd":     round((e.get("val_wacc_rd") or 0.12) * 100, 1),
            "rdsrc":  e.get("val_wacc_rd_src") or "",
            "tax":    round((e.get("val_wacc_tax") or 0.34) * 100, 1),
            "we":     round((e.get("val_wacc_we") or 0) * 100, 1),
            "wd":     round((e.get("val_wacc_wd") or 0) * 100, 1),
            "divbt":  round(e.get("val_div_bruta") or 0, 0),
            "mkteq":  round(e.get("val_mkt_eq") or 0, 0),
        }

    tickers_sorted = sorted(data.keys(), key=lambda t: -(data[t]["upside"]))
    data_js    = _json.dumps(data, ensure_ascii=False)
    tickers_js = _json.dumps(tickers_sorted)

    return (
        f'<script>var valData={data_js};var valTickers={tickers_js};</script>'
        '<div class="val-search-wrap">'
        '<div class="val-search-box">'
        '<input id="val-q" class="val-input" type="text"'
        ' placeholder="Buscar empresa pelo ticker ou nome…"'
        ' oninput="vSearch()" onkeydown="vKey(event)" autocomplete="off">'
        '<div id="val-drop" class="val-drop"></div>'
        '</div>'
        f'<div class="val-count">{len(com_val)} empresas com modelo disponível'
        ' &nbsp;·&nbsp; ordenadas por maior upside</div>'
        '</div>'
        '<div id="val-empty" class="val-empty">'
        '<div class="val-empty-icon">&#128202;</div>'
        '<div class="val-empty-txt">Busque uma empresa acima para ver o modelo</div>'
        '<div class="val-empty-sub">'
        'DCF de firma (FCFF) · 5 anos + perpetuidade · desconto = WACC · premissas automáticas<br>'
        'Clique em qualquer empresa na busca para ver o detalhamento completo'
        '</div>'
        '</div>'
        '<div id="val-view" class="val-view" style="display:none"></div>'
        '<div class="val-note">'
        '<b>Modelo:</b> DCF de firma tradicional — build-up FCFF = NOPAT + D&amp;A − Capex − ΔCapital de Giro, '
        'descontado ao WACC, + perpetuidade de Gordon (g/ROIC normalizado) no fim; Equity = EV − Dívida Líquida. '
        '<b>Upside = 0 ⟺ TIR = WACC.</b><br>'
        '<b>Premissas automáticas:</b> crescimento = CAGR histórico decrescendo 15%/ano (piso 5%) · margem EBIT = atual · '
        'D&amp;A/Capex/CMV em % da receita e giro por prazos (DSO/DIO/DPO), tudo do LTM · g perpétuo 5% · '
        'desconto = WACC (β bottom-up setorial, banda 8–18%) · 5 anos. Ações = Mkt Cap ÷ Cotação · CVM LTM. '
        '&nbsp;·&nbsp; <b>Não é recomendação de investimento.</b> '
        'Excel editável: <code>python valuation.py --ticker TICKER</code>.'
        '</div>'
    )




# ─── Entry point ──────────────────────────────────────────────────────────────

def gerar_relatorio(itens: List[Dict], output_path: Path, pais: str = "BR") -> Path:
    """Gera o HTML do screener de momentum (scores + fundamentais de mercado).

    pais='BR' (CVM/R$, Ibovespa) ou 'US' (SEC/US$, S&P 500). Controla moeda, rótulos
    e o seletor de país no topo (toggle BR ⇄ EUA)."""
    is_us = pais.upper() == "US"
    cur = "US$" if is_us else "R$"
    moeda_nome = "S&P 500" if is_us else "Ibovespa"
    n_total = sum(1 for e in itens if e.get("score_total") is not None)
    n_inv   = sum(1 for e in itens if e.get("invalido"))
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    opt_ind = "".join(
        f'<option value="{k}" data-suf="{suf}">{lbl}</option>'
        for k, lbl, suf in _INDICADORES
    ).replace("R$", cur)
    # Opções de SEGMENTO (granular, normalizado) para o filtro categórico
    setores = sorted({(e.get("segmento") or "").strip() for e in itens if (e.get("segmento") or "").strip()})
    opt_setor = '<option value="">Segmento: todos</option>' + "".join(
        f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in setores
    )
    # Chips de EXCLUSÃO de setor (clique p/ remover o setor da lista)
    chips_excl = "".join(
        f'<span class="xsec" data-s="{_esc(s)}" onclick="toggleExcl(this)">{_esc(s)}</span>'
        for s in setores
    )
    # Botões de ordenação por estratégia (US): Mayer/100B, Compounder+tend., Boring.
    # A máquina de sort aceita qualquer data-attr; aqui só disparam sortBy().
    sort_estrategia = (
        '<div class="excl-build"><span class="excl-lbl">Ordenar estratégia:</span>'
        '<span class="xsec" onclick="sortBy(\'mayer\')">🚀 Mayer 100B</span>'
        '<span class="xsec" onclick="sortBy(\'comp\')">📈 Compounder+tendência</span>'
        '<span class="xsec" onclick="sortBy(\'boring\')">🛋 Boring buy&amp;hold</span>'
        '</div>'
    ) if is_us else ''

    cabecalho = (
        '<th class="sortable" data-key="ticker" onclick="sortBy(\'ticker\')" '
        'style="text-align:left">#</th>'
        + _th("Ação", "ticker", l=True)
        + _th("Segmento", "setor", l=True)
        + _th("Total", "total", "/100")
        + _th("A", "a", "/60")
        + _th("B", "b", "/42")
        + _th("Mkt Cap", "mktcap")
        + _th("EV/EBITDA", "evebitda", "anual. tri")
        + _th("P/L", "pl", "anual. tri")
        + _th("P/L norm", "plnorm", "média 5a")
        + _th("CAGR 3a", "crescqoq", "norm. anti-M&amp;A")
        + _th("Liq. 2m", "liq2m", f"{cur}/dia")
        + _th("ROE", "roe")
        + _th("DL/PL", "divliqpl")
        + _th("TIR 5a", "tir5a", "DCF firma")
        + ((_th("Mayer", "mayer", "100B") + _th("Comp.", "comp", "+tend.")
            + _th("Boring", "boring", "buy&amp;hold")) if is_us else "")
    )

    n_val = sum(1 for e in itens if e.get("val_tir") is not None)

    # JS — sort + filtros (sem f-string para evitar conflito de chaves)
    js = """
function showTab(id){
  document.querySelectorAll('.tab-pane').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  document.getElementById(id).classList.add('active');
  document.querySelector('[data-tab="'+id+'"]').classList.add('active');
}
function tog(r){var d=document.getElementById(r.dataset.det);if(d)d.classList.toggle('open');}
var sortKey='total', sortDir=-1, filters=[];
function tb(){return document.getElementById('tb');}
function allRows(){return Array.prototype.slice.call(tb().querySelectorAll('tr.row'));}
function detOf(r){return document.getElementById(r.dataset.det);}
function num(r,k){var v=r.dataset[k];if(v===''||v===undefined||v===null)return NaN;return parseFloat(v);}
function pass(r){
  for(var i=0;i<filters.length;i++){
    var f=filters[i],v=num(r,f.key);
    if(isNaN(v))return false;
    if(f.op==='>'&&!(v>f.val))return false;
    if(f.op==='>='&&!(v>=f.val))return false;
    if(f.op==='<'&&!(v<f.val))return false;
    if(f.op==='<='&&!(v<=f.val))return false;
    if(f.op==='='&&!(Math.abs(v-f.val)<1e-6))return false;
  }
  var fs=document.getElementById('fsetor');
  if(fs&&fs.value&&(r.dataset.setor||'')!==fs.value)return false;
  if(exclSet[r.dataset.setor||''])return false;        // setor excluído via chip
  var q=document.getElementById('q').value.trim().toUpperCase();
  if(q){var t=((r.dataset.ticker||'')+' '+(r.dataset.nome||'')).toUpperCase();if(t.indexOf(q)<0)return false;}
  return true;
}
var exclSet={};
function toggleExcl(el){var s=el.dataset.s;
  if(exclSet[s]){delete exclSet[s];el.classList.remove('off');}
  else{exclSet[s]=1;el.classList.add('off');}
  render();}
function limparExcl(){exclSet={};
  document.querySelectorAll('.xsec.off').forEach(function(e){e.classList.remove('off');});
  render();}
function render(){
  var rs=allRows();
  if(sortKey){
    rs.sort(function(a,b){
      if(sortKey==='ticker'||sortKey==='setor'){var as=a.dataset[sortKey]||'',bs=b.dataset[sortKey]||'';return as<bs?-sortDir:as>bs?sortDir:0;}
      var av=num(a,sortKey),bv=num(b,sortKey);
      if(isNaN(av)&&isNaN(bv))return 0;
      if(isNaN(av))return 1; if(isNaN(bv))return -1;
      return (av-bv)*sortDir;
    });
  }
  var body=tb(),rank=0;
  rs.forEach(function(r){
    var d=detOf(r),show=pass(r);
    r.style.display=show?'':'none';
    if(d&&!show)d.classList.remove('open');
    if(show){rank++;var rk=r.querySelector('.rank');if(rk)rk.textContent=rank;}
    body.appendChild(r); if(d)body.appendChild(d);
  });
  var c=document.getElementById('cnt'); if(c)c.textContent=rank;
  document.querySelectorAll('th .arr').forEach(function(a){a.textContent='';});
  var arr=document.getElementById('arr-'+sortKey); if(arr)arr.textContent=sortDir<0?'\\u25BC':'\\u25B2';
}
function sortBy(k){ if(sortKey===k){sortDir=-sortDir;} else {sortKey=k; sortDir=(k==='ticker'||k==='setor')?1:-1;} render(); }
function addFilter(){
  var sel=document.getElementById('fk'),op=document.getElementById('fo').value,
      raw=document.getElementById('fv').value;
  if(raw===''||raw===null)return;
  var val=parseFloat(raw.replace(',','.')); if(isNaN(val))return;
  var key=sel.value,lbl=sel.options[sel.selectedIndex].text,
      suf=sel.options[sel.selectedIndex].getAttribute('data-suf')||'';
  filters.push({key:key,op:op,val:val,lbl:lbl,suf:suf});
  document.getElementById('fv').value='';
  renderChips(); render();
}
function rmFilter(i){filters.splice(i,1);renderChips();render();}
function renderChips(){
  var box=document.getElementById('chips');
  box.innerHTML=filters.map(function(f,i){
    var u=f.suf?(' '+f.suf):'';
    return '<span class="chip"><b>'+f.lbl+'</b> '+f.op+' '+f.val+u+
      '<span class="x" onclick="rmFilter('+i+')">\\u00D7</span></span>';
  }).join('');
}
function updUnit(){
  var sel=document.getElementById('fk');
  var u=sel.options[sel.selectedIndex].getAttribute('data-suf')||'';
  var map={bi:CUR+' bi',mi:CUR+' mi','x':'x','%':'%'};
  var fv=document.getElementById('fv');
  document.getElementById('funit').textContent=map[u]||'pts';
  fv.placeholder=(u==='bi'||u==='mi')?('ex.: 1 = 1 '+u):'valor';
}
/* ── Valuation — busca e visão única ── */
var vDropSel=-1;
function vSearch(){
  var q=(document.getElementById('val-q').value||'').toLowerCase().trim();
  var drop=document.getElementById('val-drop');
  if(!q||typeof valTickers==='undefined'){drop.classList.remove('show');drop.innerHTML='';return;}
  var hits=valTickers.filter(function(t){
    var d=valData[t];
    return t.toLowerCase().indexOf(q)>=0||(d.nome||'').toLowerCase().indexOf(q)>=0;
  });
  if(!hits.length){drop.classList.remove('show');drop.innerHTML='';return;}
  vDropSel=-1;
  drop.innerHTML=hits.slice(0,20).map(function(t,i){
    var d=valData[t];
    var up=d.upside;
    var uc=up>=15?'var(--verde-med)':up>=-10?'var(--marrom)':'var(--verm)';
    var us=(up>=0?'+':'')+up.toFixed(1)+'%';
    return '<div class="val-drop-item" data-tk="'+t+'" data-i="'+i+'">'
      +'<span class="dtk">'+t+'</span>'
      +'<span class="dnm">'+d.nome+'</span>'
      +'<span class="dup" style="color:'+uc+'">'+us+'</span>'
      +'</div>';
  }).join('');
  drop.classList.add('show');
}
function vKey(e){
  var drop=document.getElementById('val-drop');
  var items=drop.querySelectorAll('.val-drop-item');
  if(e.key==='ArrowDown'){e.preventDefault();vDropSel=Math.min(vDropSel+1,items.length-1);}
  else if(e.key==='ArrowUp'){e.preventDefault();vDropSel=Math.max(vDropSel-1,0);}
  else if(e.key==='Enter'&&vDropSel>=0){vSelect(items[vDropSel].dataset.tk);return;}
  else if(e.key==='Escape'){drop.classList.remove('show');return;}
  items.forEach(function(it,i){it.classList.toggle('sel',i===vDropSel);});
  if(vDropSel>=0)items[vDropSel].scrollIntoView({block:'nearest'});
}
function vSelect(tk){
  document.getElementById('val-drop').classList.remove('show');
  document.getElementById('val-q').value=tk;
  vRender(tk);
}
function _fmi(v){
  if(v===null||v===undefined)return '—';
  var a=Math.abs(v);
  if(a>=10000)return CUR+' '+(v/1000).toFixed(1)+' bi';
  if(a>=1000)return CUR+' '+Math.round(v)+' mi';
  return CUR+' '+v.toFixed(1)+' mi';
}
function _uCol(u){return u>15?'var(--verde-med)':u>-10?'var(--marrom)':'var(--verm)';}
function _tCol(t){return t>12?'var(--verde-med)':t>8?'var(--marrom)':'var(--verm)';}
function _vBadge(up){
  if(up>40)return '<span class="val-badge val-at">Muito Atrativo</span>';
  if(up>15)return '<span class="val-badge val-ok">Atrativo</span>';
  if(up>-10)return '<span class="val-badge val-ne">Neutro</span>';
  return '<span class="val-badge val-ca">Caro</span>';
}
function _vChart(rev,ebs,by){
  if(!rev||!rev.length)return '';
  var W=480,H=180,PL=8,PR=8,PT=24,PB=28;
  var maxR=Math.max.apply(null,rev)||1;
  var n=rev.length;
  var bw=(W-PL-PR)/n-4;
  var years=[];for(var i=0;i<n;i++)years.push(by+i);
  var bars='',line='',dots='',lbls='';
  for(var i=0;i<n;i++){
    var x=PL+i*((W-PL-PR)/n)+2;
    var rh=(rev[i]/maxR)*(H-PT-PB);
    var ry=PT+(H-PT-PB)-rh;
    bars+='<rect x="'+x+'" y="'+ry+'" width="'+bw+'" height="'+rh+'" fill="var(--verde)" opacity="0.28"/>';
    var eh=(ebs[i]/maxR)*(H-PT-PB);
    var ey=PT+(H-PT-PB)-eh;
    var cx=x+bw/2;
    if(i===0)line='M'+cx+','+ey;else line+=' L'+cx+','+ey;
    dots+='<circle cx="'+cx+'" cy="'+ey+'" r="3.5" fill="var(--marrom)"/>';
    lbls+='<text x="'+cx+'" y="'+(H-4)+'" text-anchor="middle" font-size="9" fill="var(--text-mut)">'+years[i]+'</text>';
  }
  return '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;max-width:'+W+'px;height:auto;display:block;margin-top:10px">'
    +'<path d="'+line+'" stroke="var(--marrom)" stroke-width="2" fill="none"/>'
    +bars+dots+lbls
    +'<text x="'+PL+'" y="14" font-size="9" fill="var(--verde)" font-weight="700">Receita</text>'
    +'<text x="'+(PL+52)+'" y="14" font-size="9" fill="var(--marrom)" font-weight="700">— EBIT</text>'
    +'</svg>';
}
function _vPbar(preco,pj){
  if(!preco||!pj)return '';
  var lo=Math.min(preco,pj)*0.85,hi=Math.max(preco,pj)*1.15;
  var rng=hi-lo;
  var xp=((preco-lo)/rng)*100,xj=((pj-lo)/rng)*100;
  var fill=pj>preco?'var(--verde)':'var(--verm)';
  var left=Math.min(xp,xj),right=Math.max(xp,xj);
  return '<div style="position:relative;height:28px;margin:14px 0 4px;background:var(--areia-cl);border-radius:6px;overflow:hidden;">'
    +'<div style="position:absolute;left:'+left+'%;width:'+(right-left)+'%;top:0;height:100%;background:'+fill+';opacity:0.22"></div>'
    +'<div style="position:absolute;left:'+xp+'%;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:var(--text-pri);border:2px solid #fff"></div>'
    +'<div style="position:absolute;left:'+xj+'%;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:'+fill+';border:2px solid #fff"></div>'
    +'</div>'
    +'<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-mut);padding:0 2px">'
    +'<span>atual '+CUR+' '+preco.toFixed(2)+'</span><span>justo '+CUR+' '+pj.toFixed(2)+'</span>'
    +'</div>';
}
var vCurr=null;
function _irr(flows){
  function npv(r){var s=0;for(var i=0;i<flows.length;i++)s+=flows[i]/Math.pow(1+r,i);return s;}
  function dnpv(r){var s=0;for(var i=1;i<flows.length;i++)s-=i*flows[i]/Math.pow(1+r,i+1);return s;}
  var r=0.12;
  for(var k=0;k<200;k++){
    var f=npv(r),df=dnpv(r);
    if(Math.abs(df)<1e-15)break;
    var rn=r-f/df;
    if(rn<=-0.9999)rn=(r-0.9999)/2;
    if(Math.abs(rn-r)<1e-12){r=rn;break;}
    r=rn;
  }
  return r;
}
function _fn(v,dec){if(v===null||v===undefined||isNaN(v))return '–';dec=(dec===undefined?0:dec);return v.toLocaleString('pt-BR',{minimumFractionDigits:dec,maximumFractionDigits:dec});}
function _fp(v){if(v===null||v===undefined||isNaN(v))return '–';return v.toFixed(1)+'%';}
function vModel(){
  /* lê os inputs e roda o DCF inteiro; devolve séries + resultados */
  var d=vCurr,n=d.cresc.length;
  function rd(id,def){var el=document.getElementById(id);var v=el?parseFloat(el.value):NaN;return isNaN(v)?def:v;}
  var mg=rd('v-mg',d.mg)/100,desc=rd('v-desc',d.desc)/100,tax=rd('w-tax',d.tax)/100;
  var roic=Math.max(0.05,Math.min(0.50,(d.roic||12)/100));
  var dapct=rd('v-dapct',d.dapct)/100,cxpct=rd('v-cxpct',d.cxpct)/100,cgpct=rd('v-cgpct',d.cgpct)/100;
  var dso=rd('v-dso',d.dso),dio=rd('v-dio',d.dio),dpo=rd('v-dpo',d.dpo);
  var gperp=Math.min(rd('v-gperp',d.gperp)/100,desc-0.005);
  var cresc=[];for(var i=1;i<=n;i++)cresc.push(rd('vg-'+i,d.cresc[i-1])/100);
  var rev=[d.rec];for(var i=0;i<n;i++)rev.push(rev[rev.length-1]*(1+cresc[i]));
  var ebit=[],nopat=[],da=[],capex=[],cogs=[],wc=[];
  for(var i=0;i<=n;i++){ebit.push(rev[i]*mg);nopat.push(ebit[i]*(1-tax));da.push(rev[i]*dapct);capex.push(rev[i]*cxpct);cogs.push(rev[i]*cgpct);wc.push(rev[i]*dso/365+cogs[i]*dio/365-cogs[i]*dpo/365);}
  var fcff=[0],dwc=[0],pv=[0];
  for(var i=1;i<=n;i++){var dw=wc[i]-wc[i-1];dwc.push(dw);var f=nopat[i]+da[i]-capex[i]-dw;fcff.push(f);pv.push(f/Math.pow(1+desc,i));}
  var rrp=Math.max(0,Math.min(0.90,gperp/roic));
  var tv=desc>gperp?(nopat[n]*(1+gperp)*(1-rrp))/(desc-gperp):0;
  var pv_tv=tv/Math.pow(1+desc,n),pv_sum=0;for(var i=1;i<=n;i++)pv_sum+=pv[i];
  var ev=pv_sum+pv_tv,eq=ev-d.nd,pj=eq/d.na,up=pj/d.preco*100-100,ev_at=d.preco*d.na+d.nd;
  var flows=[-ev_at];for(var i=1;i<n;i++)flows.push(fcff[i]);flows.push(fcff[n]+tv);
  return {n:n,mg:mg,desc:desc,gperp:gperp,cresc:cresc,rev:rev,ebit:ebit,nopat:nopat,da:da,capex:capex,
          dwc:dwc,fcff:fcff,pv:pv,tv:tv,pv_tv:pv_tv,pv_sum:pv_sum,ev:ev,eq:eq,pj:pj,up:up,
          tir:_irr(flows)*100,saida:ebit[n]?tv/ebit[n]:0};
}
function vRecompute(){
  var d=vCurr;if(!d)return;var m=vModel(),n=m.n;
  function S(id,txt){var el=document.getElementById(id);if(el)el.innerHTML=txt;}
  for(var i=0;i<=n;i++){
    S('s-rev-'+i,_fn(m.rev[i]));
    S('s-mg-'+i,(m.mg*100).toFixed(1)+'%');
    S('s-ebit-'+i,_fn(m.ebit[i]));
    S('s-nopat-'+i,_fn(m.nopat[i]));
    S('s-da-'+i,_fn(m.da[i]));
    S('s-cx-'+i,'('+_fn(m.capex[i])+')');
    S('s-wc-'+i,i>0?'('+_fn(m.dwc[i])+')':'–');
    S('s-fcff-'+i,i>0?_fn(m.fcff[i]):'–');
    S('s-pv-'+i,i>0?_fn(m.pv[i]):'–');
    if(i>0){var sc=document.getElementById('vg-'+i);if(sc){var g=m.cresc[i-1]*100;sc.style.color=g>15?'var(--verde-med)':(g>5?'var(--marrom)':'var(--verm)');}}
  }
  S('e-pvf',_fn(m.pv_sum));S('e-pvtv',_fn(m.pv_tv));S('e-ev',_fn(m.ev));
  S('e-nd','('+_fn(d.nd)+')');S('e-eq',_fn(m.eq));S('e-na',_fn(d.na,1));
  S('e-pj',CUR+' '+m.pj.toFixed(2));S('e-pa',CUR+' '+d.preco.toFixed(2));
  S('e-up','<b style="color:'+_uCol(m.up)+'">'+(m.up>=0?'+':'')+m.up.toFixed(1)+'%</b>');
  S('e-tir','<b style="color:'+_tCol(m.tir)+'">'+m.tir.toFixed(1)+'%</b>');
  S('e-tvpct',(m.tv/m.ev*100||0).toFixed(0)+'%');S('e-saida',m.saida.toFixed(1)+'x');S('e-gp',(m.gperp*100).toFixed(1)+'%');
  var bd=document.getElementById('v-badge');if(bd)bd.innerHTML=_vBadge(m.up);
}
function vWacc(){
  var d=vCurr;if(!d)return;
  function rd(id,def){var el=document.getElementById(id);var v=el?parseFloat(el.value):NaN;return isNaN(v)?def:v;}
  var beta=rd('w-beta',d.beta);
  var rf=rd('w-rf',d.rf)/100;
  var erp=rd('w-erp',d.erp)/100;
  var rdc=rd('w-rd',d.rd)/100;
  var tax=rd('w-tax',d.tax)/100;
  var e=d.mkteq,dbt=d.divbt,v=e+dbt;
  var we=v>0?e/v:1,wd=v>0?dbt/v:0;
  var re=rf+beta*erp, rdt=rdc*(1-tax);
  var wacc=Math.max(0.08,Math.min(0.18,we*re+wd*rdt));   /* banda do WACC */
  var set=function(id,txt){var el=document.getElementById(id);if(el)el.textContent=txt;};
  set('w-re',(re*100).toFixed(1)+'%');
  set('w-rdt',(rdt*100).toFixed(1)+'%');
  set('w-we',(we*100).toFixed(0)+'%');
  set('w-wd',(wd*100).toFixed(0)+'%');
  set('w-out',(wacc*100).toFixed(1)+'%');
  var dEl=document.getElementById('v-desc');if(dEl)dEl.value=(wacc*100).toFixed(1);
  vRecompute();
}
function vRenderFCFE(tk){
  /* Aba de valuation para FINANCEIRAS — DCF de equity (FCFE).
     Estático (sem inputs editáveis): mostra o build-up do lucro, FCFE, custo de
     equity (Re), P/VP e preço justo. TIR = retorno do acionista (= Re no preço justo). */
  var d=valData[tk];if(!d)return;vCurr=d;
  document.getElementById('val-empty').style.display='none';
  var vv=document.getElementById('val-view');vv.style.display='block';
  var ll=d.llser||[],fc=d.fcfeser||[],n=d.cresc.length,by=d.by;
  var tcols=2+n;
  var th='<th>'+CUR+' milhões</th><th>'+by+' LTM</th>';
  for(var i=1;i<=n;i++)th+='<th class="proj">'+(by+i)+'E</th>';
  function rowF(lbl,arr,cls,fmt){
    fmt=fmt||function(x){return _fn(x);};
    var r='<tr'+(cls?' class="'+cls+'"':'')+'><td class="lbl">'+lbl+'</td>';
    for(var i=0;i<arr.length;i++)r+='<td>'+((arr[i]!==undefined&&arr[i]!==null)?fmt(arr[i]):'–')+'</td>';
    return r+'</tr>';
  }
  var grow='<tr><td class="lbl">Cresc. lucro % a/a</td><td class="muted">–</td>';
  for(var i=0;i<n;i++)grow+='<td class="muted">'+_fp(d.cresc[i])+'</td>';
  grow+='</tr>';
  /* retenção b = g/ROE por ano (informativo) */
  var bret=['–'];for(var i=0;i<n;i++){var b=(d.roeeff>0)?(d.cresc[i]/d.roeeff*100):0;bret.push(b.toFixed(0)+'%');}
  var brow='<tr><td class="lbl">Retenção g/ROE</td>';for(var i=0;i<bret.length;i++)brow+='<td class="muted">'+bret[i]+'</td>';brow+='</tr>';
  var tbl='<div class="dcf-wrap"><table class="dcf-tbl"><thead><tr>'+th+'</tr></thead><tbody>'
    +'<tr class="grp"><td colspan="'+tcols+'">Lucro &amp; Reinvestimento</td></tr>'
    +rowF('Lucro líquido',ll)
    +grow+brow
    +'<tr class="grp"><td colspan="'+tcols+'">Fluxo de Caixa ao Acionista (FCFE)</td></tr>'
    +rowF('(=) FCFE = LL·(1−g/ROE)',fc,'tot')
    +'</tbody></table></div>';
  /* Custo de equity (Re) — vertical */
  var betaTag=(d.betaraw!==null&&d.betaraw!==undefined)?'β raw '+d.betaraw.toFixed(2):'ajust.';
  var reTbl='<table class="kv-tbl">'
    +'<tr><td>Beta β <span style="color:var(--text-mut);font-size:9px">'+betaTag+'</span></td><td class="r">'+d.beta.toFixed(2)+'</td></tr>'
    +'<tr><td>Rf (livre de risco)</td><td class="r">'+d.rf.toFixed(1)+'%</td></tr>'
    +'<tr><td>ERP (prêmio de mercado)</td><td class="r">'+d.erp.toFixed(1)+'%</td></tr>'
    +'<tr class="fin"><td>= Custo de equity Re (CAPM)</td><td class="r">'+d.re.toFixed(1)+'%</td></tr>'
    +'<tr class="sub"><td>ROE (efetivo no modelo)</td><td class="r">'+d.roe.toFixed(1)+'% ('+d.roeeff.toFixed(1)+'%)</td></tr>'
    +'<tr class="sub"><td>g perpétuo (Gordon)</td><td class="r">'+d.gperp.toFixed(1)+'%</td></tr>'
    +'</table>';
  var pvpFmt=function(x){return x?x.toFixed(2)+'x':'–';};
  var evb='<table class="kv-tbl">'
    +'<tr><td>Lucro líq. LTM</td><td class="r">'+_fn(d.ll)+'</td></tr>'
    +'<tr><td>Patrimônio líq. (book)</td><td class="r">'+_fn(d.plv)+'</td></tr>'
    +'<tr class="calc"><td>(=) Equity justo</td><td class="r">'+_fn(d.eqj)+'</td></tr>'
    +'<tr><td>(÷) Ações (mi)</td><td class="r">'+_fn(d.na)+'</td></tr>'
    +'<tr class="fin"><td>Preço justo</td><td class="r">'+CUR+' '+d.pj.toFixed(2)+'</td></tr>'
    +'<tr class="sub"><td>Preço atual</td><td class="r">'+CUR+' '+d.preco.toFixed(2)+'</td></tr>'
    +'<tr class="fin"><td>Upside</td><td class="r">'+_fp(d.upside)+'</td></tr>'
    +'<tr class="fin"><td>TIR (acionista)</td><td class="r">'+_fp(d.tir)+'</td></tr>'
    +'<tr class="sub"><td>P/VP atual · justo</td><td class="r">'+pvpFmt(d.pvp)+' · '+pvpFmt(d.pvpj)+'</td></tr>'
    +'</table>';
  var flagHtml=(d.flag?'<div style="font-size:10px;color:var(--verm);margin-top:10px">&#9888; '+d.flag+'</div>':'')
    +'<div style="font-size:10px;color:var(--text-mut);margin-top:6px">Modelo de equity ('+(d.tipofin||'financeira')+') · desconto ao custo de equity Re, sem WACC — para banco a dívida é matéria-prima. Preço justo P/VP = (ROE−g)/(Re−g).</div>';
  vv.innerHTML=
    '<div class="val-vh"><span class="val-tk" style="font-size:22px">'+tk+'</span>'
    +'<span class="val-nm" style="font-size:14px;margin-left:12px">'+d.nome+'</span>'
    +'<span id="v-badge">'+_vBadge(d.upside)+'</span></div>'
    +'<div class="val-block"><div class="val-bt">Modelo DCF — Fluxo de Caixa ao Acionista (FCFE) &nbsp;<span style="font-weight:400;opacity:.7;font-size:9px">— financeiras: lucro retém g/ROE para crescer a base de capital</span></div>'
    +'<div class="val-bc">'+tbl+'</div></div>'
    +'<div class="val-bottom">'
    +'<div class="val-block"><div class="val-bt">Custo de Capital — Custo de Equity (Re)</div><div class="val-bc">'+reTbl+'</div></div>'
    +'<div class="val-block"><div class="val-bt">Ponte de Valor &amp; Resultado</div><div class="val-bc">'+evb+flagHtml+'</div></div>'
    +'</div>';
  document.getElementById('val-drop').classList.remove('show');
}
function vRender(tk){
  var d=valData[tk];if(!d)return;
  if(d.modelo==='FCFE')return vRenderFCFE(tk);
  vCurr=d;
  document.getElementById('val-empty').style.display='none';
  var vv=document.getElementById('val-view');vv.style.display='block';
  var n=d.cresc.length,by=d.by,hn=(d.hrev||[]).length;
  var htax=(d.tax||34)/100;
  var hnopat=(d.hebit||[]).map(function(x){return x*(1-htax);});
  var hfcff=[];for(var i=0;i<hn;i++)hfcff.push(hnopat[i]+(d.hda[i]||0)-(d.hcapex[i]||0));
  var tcols=2+hn+n;
  /* cabeçalho: anos nas colunas (histórico A + LTM + projeção E) */
  var th='<th>'+CUR+' milhões</th>';
  for(var i=0;i<hn;i++)th+='<th class="hist">'+(by-hn+i)+'A</th>';
  th+='<th>'+by+' LTM</th>';
  for(var i=1;i<=n;i++)th+='<th class="proj">'+(by+i)+'E</th>';
  /* linha: hist estático + LTM/proj por id (preenchidos por vRecompute) */
  function row(lbl,key,harr,cls,fmt){
    fmt=fmt||function(x){return _fn(x);};
    var r='<tr'+(cls?' class="'+cls+'"':'')+'><td class="lbl">'+lbl+'</td>';
    for(var i=0;i<hn;i++)r+='<td class="hcol">'+((harr&&harr[i]!==undefined&&harr[i]!==null)?fmt(harr[i]):'–')+'</td>';
    for(var i=0;i<=n;i++)r+='<td id="s-'+key+'-'+i+'"></td>';
    return r+'</tr>';
  }
  var grow='<tr><td class="lbl">Cresc. % a/a</td>';
  for(var i=0;i<hn;i++){var g=(i>0&&d.hrev[i-1])?((d.hrev[i]/d.hrev[i-1]-1)*100):null;grow+='<td class="hcol muted">'+(g===null?'–':_fp(g))+'</td>';}
  grow+='<td class="muted">–</td>';
  for(var i=1;i<=n;i++)grow+='<td><input id="vg-'+i+'" class="v-inp" type="number" step="0.1" value="'+d.cresc[i-1].toFixed(1)+'" oninput="vRecompute()"></td>';
  grow+='</tr>';
  var hmarg=[];for(var i=0;i<hn;i++)hmarg.push(d.hrev[i]?d.hebit[i]/d.hrev[i]*100:null);
  var capfmt=function(x){return '('+_fn(x)+')';};
  var tbl='<div class="dcf-wrap"><table class="dcf-tbl"><thead><tr>'+th+'</tr></thead><tbody>'
    +'<tr class="grp"><td colspan="'+tcols+'">Receita &amp; Margem</td></tr>'
    +row('Receita','rev',d.hrev)
    +grow
    +row('Margem EBIT %','mg',hmarg,null,function(x){return x.toFixed(1)+'%';})
    +'<tr class="grp"><td colspan="'+tcols+'">Fluxo de Caixa Livre da Firma (FCFF)</td></tr>'
    +row('EBIT','ebit',d.hebit)
    +row('NOPAT = EBIT·(1−t)','nopat',hnopat)
    +row('(+) D&amp;A','da',d.hda)
    +row('(−) Capex','cx',d.hcapex,null,capfmt)
    +row('(−) Δ Capital de Giro','wc',null)
    +row('(=) FCFF','fcff',hfcff,'tot')
    +row('FCFF descontado @ WACC','pv',null)
    +'</tbody></table></div>';
  /* WACC vertical (padrão de mercado) */
  var betaTag=(d.betaraw!==null&&d.betaraw!==undefined)?'β raw '+d.betaraw.toFixed(2):'ajust.';
  var waccTbl='<table class="kv-tbl">'
    +'<tr><td>Beta β <span style="color:var(--text-mut);font-size:9px">'+betaTag+'</span></td><td class="r"><input id="w-beta" class="v-inp" type="number" step="0.05" value="'+d.beta.toFixed(2)+'" oninput="vWacc()"></td></tr>'
    +'<tr><td>Rf (livre de risco)</td><td class="r"><input id="w-rf" class="v-inp" type="number" step="0.25" value="'+d.rf.toFixed(1)+'" oninput="vWacc()"> %</td></tr>'
    +'<tr><td>ERP (prêmio de mercado)</td><td class="r"><input id="w-erp" class="v-inp" type="number" step="0.25" value="'+d.erp.toFixed(1)+'" oninput="vWacc()"> %</td></tr>'
    +'<tr class="calc"><td>= Custo do equity (CAPM)</td><td class="r" id="w-re">–</td></tr>'
    +'<tr><td>Custo da dívida (pré-tax) <span style="color:var(--text-mut);font-size:9px">'+(d.rdsrc?d.rdsrc:'')+'</span></td><td class="r"><input id="w-rd" class="v-inp" type="number" step="0.5" value="'+d.rd.toFixed(1)+'" oninput="vWacc()"> %</td></tr>'
    +'<tr><td>Imposto (t)</td><td class="r"><input id="w-tax" class="v-inp" type="number" step="1" value="'+d.tax.toFixed(0)+'" oninput="vWacc()"> %</td></tr>'
    +'<tr class="calc"><td>= Custo da dívida pós-tax</td><td class="r" id="w-rdt">–</td></tr>'
    +'<tr><td>Pesos Equity / Dívida</td><td class="r"><span id="w-we">–</span> / <span id="w-wd">–</span></td></tr>'
    +'<tr class="fin"><td>WACC</td><td class="r"><span id="w-out">–</span></td></tr>'
    +'</table>';
  function drv(lbl,id,val,step,unit){return '<div class="drv-row"><label>'+lbl+'</label><span><input id="'+id+'" class="v-inp" type="number" step="'+step+'" value="'+val+'" oninput="vRecompute()"> <span class="u">'+unit+'</span></span></div>';}
  var drivers=drv('Margem EBIT','v-mg',d.mg.toFixed(1),'0.1','%')
    +drv('D&amp;A','v-dapct',d.dapct.toFixed(1),'0.1','% rec')
    +drv('Capex','v-cxpct',d.cxpct.toFixed(1),'0.1','% rec')
    +drv('CMV','v-cgpct',d.cgpct.toFixed(1),'1','% rec')
    +drv('Receber','v-dso',d.dso.toFixed(0),'1','dias (DSO)')
    +drv('Estoque','v-dio',d.dio.toFixed(0),'1','dias (DIO)')
    +drv('Fornecedores','v-dpo',d.dpo.toFixed(0),'1','dias (DPO)')
    +drv('g perpétuo','v-gperp',d.gperp.toFixed(1),'0.25','% Gordon')
    +drv('Desconto (WACC)','v-desc',d.desc.toFixed(1),'0.5','% a.a.');
  var evb='<table class="kv-tbl">'
    +'<tr><td>Σ VP dos FCFF ('+n+'a)</td><td class="r" id="e-pvf">–</td></tr>'
    +'<tr><td>(+) VP da Perpetuidade</td><td class="r" id="e-pvtv">–</td></tr>'
    +'<tr class="calc"><td>(=) EV justo</td><td class="r" id="e-ev">–</td></tr>'
    +'<tr><td>(−) Dívida líquida</td><td class="r" id="e-nd">–</td></tr>'
    +'<tr class="calc"><td>(=) Equity justo</td><td class="r" id="e-eq">–</td></tr>'
    +'<tr><td>(÷) Ações (mi)</td><td class="r" id="e-na">–</td></tr>'
    +'<tr class="fin"><td>Preço justo</td><td class="r" id="e-pj">–</td></tr>'
    +'<tr class="sub"><td>Preço atual</td><td class="r" id="e-pa">–</td></tr>'
    +'<tr class="fin"><td>Upside</td><td class="r" id="e-up">–</td></tr>'
    +'<tr class="fin"><td>TIR (firma)</td><td class="r" id="e-tir">–</td></tr>'
    +'<tr class="sub"><td>Perpet. % EV · EV/EBIT saída · g</td><td class="r"><span id="e-tvpct">–</span> · <span id="e-saida">–</span> · <span id="e-gp">–</span></td></tr>'
    +'</table>';
  var flagHtml=(d.flag?'<div style="font-size:10px;color:var(--verm);margin-top:10px">&#9888; '+d.flag+'</div>':'')
    +(d.cagr?'<div style="font-size:10px;color:var(--text-mut);margin-top:6px">CAGR hist. receita: <b>'+d.cagr.toFixed(1)+'%</b> · ROIC <b>'+d.roic.toFixed(1)+'%</b></div>':'');
  vv.innerHTML=
    '<div class="val-vh"><span class="val-tk" style="font-size:22px">'+tk+'</span>'
    +'<span class="val-nm" style="font-size:14px;margin-left:12px">'+d.nome+'</span>'
    +'<span id="v-badge">'+_vBadge(d.upside)+'</span></div>'
    +'<div class="val-block"><div class="val-bt">Modelo DCF — Fluxo de Caixa Livre da Firma &nbsp;<span style="font-weight:400;opacity:.7;font-size:9px">— histórico (A) + projeção (E) · edite o cresc. % de cada ano e os drivers abaixo</span></div>'
    +'<div class="val-bc">'+tbl+'</div></div>'
    +'<div class="val-bottom">'
    +'<div class="val-block"><div class="val-bt">Custo de Capital — WACC</div><div class="val-bc">'+waccTbl+'</div></div>'
    +'<div class="val-block"><div class="val-bt">Premissas — drivers do FCFF</div><div class="val-bc">'+drivers+'</div></div>'
    +'<div class="val-block"><div class="val-bt">Ponte de Valor &amp; Resultado</div><div class="val-bc">'+evb+flagHtml+'</div></div>'
    +'</div>';
  document.getElementById('val-drop').classList.remove('show');
  vWacc();
}
document.addEventListener('click',function(e){
  var item=e.target.closest('.val-drop-item');
  if(item&&item.dataset.tk){vSelect(item.dataset.tk);return;}
  if(!e.target.closest('.val-search-box')){
    var drop=document.getElementById('val-drop');
    if(drop)drop.classList.remove('show');
  }
});
window.onload=function(){updUnit();render();};
"""

    # ── Seletor de país (canto superior direito) ──────────────────────────────
    def _pill(label, href, active):
        bg = "var(--marrom)" if active else "transparent"
        col = "#fff" if active else "rgba(243,229,208,.8)"
        return (f'<a href="{href}" style="padding:5px 13px;border-radius:14px;'
                f'font-size:12px;font-weight:600;text-decoration:none;background:{bg};'
                f'color:{col};border:1px solid var(--marrom);">{label}</a>')
    seletor = (
        '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">'
        + _pill("🇧🇷 Ações", "momentum_acoes.html", not is_us)
        + _pill("🇺🇸 EUA", "momentum_us.html", is_us)
        + _pill("🏢 FIIs", "momentum_fii.html", False)
        + f'<span style="font-size:11px;color:rgba(243,229,208,.6);margin-left:8px">'
          f'<b id="cnt">{n_total}</b> ações · {moeda_nome} · {ts}</span>'
        + '</div>'
    )
    htmldoc = (
        f'<!doctype html><html lang="pt-BR"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Screener {"EUA" if is_us else "Brasil"} · Zelen Invest</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        f'<style>{_CSS}'
        '.excl-build{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:9px;}'
        '.excl-lbl{font-size:11px;color:var(--text-mut);font-weight:700;margin-right:3px;}'
        '.xsec{font-size:10px;padding:3px 9px;border-radius:11px;border:1px solid var(--marrom);'
        'cursor:pointer;color:var(--marrom);user-select:none;background:transparent;white-space:nowrap;}'
        '.xsec.off{background:var(--verm);color:#fff;border-color:var(--verm);text-decoration:line-through;}'
        '.xsec-clear{font-size:10px;color:var(--text-mut);cursor:pointer;text-decoration:underline;margin-left:6px;}'
        '.trap{margin-left:5px;cursor:help;font-size:11px;filter:saturate(1.4);}'
        # Painel de altura fixa: rola na vertical DENTRO da caixa (cabeçalho sticky)
        # e a barra HORIZONTAL fica fixa no rodapé visível — não lá embaixo após 1300 linhas.
        '.tbl-scroll{overflow:auto;max-height:76vh;-webkit-overflow-scrolling:touch;'
        'scrollbar-width:auto;scrollbar-color:var(--marrom) #E7DcC6;}'
        '.tbl-scroll table{width:auto;min-width:100%;}'
        '.tbl-scroll th,.tbl-scroll td{white-space:nowrap;}'
        '.tbl-scroll::-webkit-scrollbar{height:14px;width:14px;}'
        '.tbl-scroll::-webkit-scrollbar-track{background:#E7DcC6;border-radius:7px;}'
        '.tbl-scroll::-webkit-scrollbar-thumb{background:var(--marrom);border-radius:7px;border:3px solid #E7DcC6;}'
        '.tbl-scroll::-webkit-scrollbar-thumb:hover{background:var(--verde-esc,#284B23);}'
        '.tbl-scroll::-webkit-scrollbar-corner{background:#E7DcC6;}'
        # Congela # e Ação (ticker) na horizontal — ficam fixos enquanto rola lateralmente
        '.tbl-scroll th:nth-child(1),.tbl-scroll td:nth-child(1){position:sticky;left:0;z-index:4;'
        'background:var(--bg-card);box-sizing:border-box;width:42px;min-width:42px;max-width:42px;}'
        '.tbl-scroll tr.row td:nth-child(2),.tbl-scroll thead th:nth-child(2){position:sticky;left:42px;'
        'z-index:4;background:var(--bg-card);box-shadow:6px 0 6px -4px rgba(0,0,0,.12);}'
        '.tbl-scroll thead th:nth-child(1),.tbl-scroll thead th:nth-child(2){z-index:7;}'
        '.tbl-scroll tr.row:hover td:nth-child(1),.tbl-scroll tr.row:hover td:nth-child(2){background:#F7F2E8;}'
        '</style></head><body>'
        f'<script>var CUR="{cur}";</script>'
        '<header class="top"><div class="wrap">'
        f'<h1>Momentum {"🇺🇸 EUA" if is_us else "🇧🇷 Brasil"} <span>· Zelen Invest</span></h1>'
        + seletor +
        '</div></header>'
        '<div class="wrap"><div class="panel">'
        '<div class="panel-head"><h2>Screener de Momentum <span style="font-weight:400;opacity:.8">· Zelen Invest</span></h2>'
        '<span class="tag">CLIQUE NO CABEÇALHO P/ ORDENAR</span></div>'
        # Tab bar
        '<div class="tab-bar">'
        '<button class="tab-btn active" data-tab="t-screener" onclick="showTab(\'t-screener\')">Screener</button>'
        f'<button class="tab-btn" data-tab="t-valuation" onclick="showTab(\'t-valuation\')">Valuation'
        f'<span style="margin-left:6px;font-size:10px;background:var(--marrom);color:#fff;padding:1px 6px;border-radius:8px;">{n_val}</span>'
        f'</button>'
        '</div>'
        # ── Tab Screener ──────────────────────────────────────────────────────
        '<div class="tab-pane active" id="t-screener">'
        '<div class="screener"><div class="filt-build">'
        f'<select id="fk" onchange="updUnit()">{opt_ind}</select>'
        '<select id="fo"><option>&gt;</option><option>&gt;=</option>'
        '<option>&lt;</option><option>&lt;=</option><option>=</option></select>'
        '<input id="fv" type="number" step="any" placeholder="valor">'
        '<span id="funit" class="funit"></span>'
        '<button class="btn" onclick="addFilter()">+ Filtro</button>'
        f'<select id="fsetor" class="fsetor" onchange="render()">{opt_setor}</select>'
        '<input id="q" class="q" placeholder="buscar ticker ou nome…" oninput="render()">'
        '</div>'
        f'<div class="excl-build"><span class="excl-lbl">Excluir setores:</span>{chips_excl}'
        '<span class="xsec-clear" onclick="limparExcl()">limpar</span></div>'
        + sort_estrategia +
        '<div id="chips" class="chips"></div></div>'
        '<div class="tbl-scroll">'
        f'<table><thead><tr>{cabecalho}</tr></thead><tbody id="tb">'
        f'{_linhas(itens, is_us)}'
        '</tbody></table></div>'
        '<div class="legend">'
        '<b>Scores</b>: Total 0-100 = A (operacional /60) + B (técnico /42). '
        'A = trajetória de crescimento (A1 nível · A2 consistência) → aceleração (A3) → qualidade do lucro (A4) → solidez (A5). '
        'B = força técnica (proximidade máx 52s, momentum 12-1, estrutura de médias).&nbsp;|&nbsp;'
        '<b>Valuation</b>: Mkt Cap = P/VP × PL (Fundamentus). EV/EBITDA e P/L <i>anualizados</i> do último tri '
        '(× 4; fallback LTM Fundamentus). CAGR 3a = CAGR de receita de 3 anos normalizado '
        '(exclui anos com salto de goodwill = M&amp;A; crescimento orgânico passa intacto). '
        'Liq. 2m = vol. financeiro médio diário. Clique na linha p/ expandir detalhe.'
        '</div>'
        '</div>'
        # ── Tab Valuation ─────────────────────────────────────────────────────
        '<div class="tab-pane" id="t-valuation">'
        + _val_tab_content(itens) +
        '</div>'
        '</div>'  # .panel
        f'<footer>Screener de Momentum · Zelen Invest · {ts} · Não é recomendação de investimento.</footer>'
        '</div>'
        f'<script>{js}</script>'
        '</body></html>'
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(htmldoc, encoding="utf-8")
    return output_path
