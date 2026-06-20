"""
Relatório HTML do backtest de Momentum (Zelen Invest).

Renderiza: cards de métricas (estratégia vs Ibovespa), curva de capital em SVG
e tabela mês a mês com retorno e composição do Top-N.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List

_CSS = """
:root{
  --verde:#284B23;--verde-esc:#1A3018;--verde-med:#3D6B36;--verde-cl:#5A8C52;
  --marrom:#B3703C;--areia:#F3E5D0;--areia-cl:#FAF4EB;--areia-esc:#E8D4B8;
  --bg-base:#F3E5D0;--bg-card:#FAF4EB;
  --text-pri:#1A2E17;--text-sec:#4A5E47;--text-mut:#8A9E87;
  --border:#D4C4A8;--verm:#8B2020;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:"Inter","Segoe UI",sans-serif;background:var(--bg-base);color:var(--text-pri);font-size:13px;padding:0 0 60px;}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px;}
header.top{background:var(--verde-esc);color:var(--areia);padding:22px 0;margin-bottom:20px;border-bottom:4px solid var(--marrom);}
header.top .wrap{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px;}
header.top h1{font-size:22px;font-weight:700;}
header.top h1 span{color:var(--marrom);}
header.top .sub{font-size:12px;color:rgba(243,229,208,.7);font-weight:500;}
.panel{background:var(--bg-card);border:1px solid var(--border);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:20px;overflow:hidden;}
.panel-head{padding:13px 18px;background:var(--verde);color:var(--areia);display:flex;align-items:center;justify-content:space-between;}
.panel-head h2{font-size:14px;font-weight:700;}
.panel-head .tag{font-size:10px;font-weight:600;background:rgba(243,229,208,.18);padding:3px 9px;border-radius:10px;}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);}
@media(max-width:820px){.cards{grid-template-columns:repeat(2,1fr);}}
.card{background:var(--bg-card);padding:14px 16px;}
.card .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-mut);font-weight:700;margin-bottom:7px;}
.card .big{font-size:22px;font-weight:700;line-height:1;}
.card .cmp{font-size:11px;color:var(--text-sec);margin-top:5px;}
.pos{color:var(--verde-med);} .neg{color:var(--verm);}
.chart-box{padding:18px;}
.legend{display:flex;gap:18px;font-size:12px;font-weight:600;margin-bottom:6px;padding-left:4px;}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px;}
table{width:100%;border-collapse:collapse;}
th{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:var(--text-mut);text-align:right;padding:9px 10px;border-bottom:2px solid var(--border);font-weight:700;}
th.l,td.l{text-align:left;}
td{padding:8px 10px;text-align:right;border-bottom:1px solid #EFE6D6;font-variant-numeric:tabular-nums;}
td.hold{font-size:11px;color:var(--verde);font-weight:600;}
.foot{font-size:11px;color:var(--text-sec);padding:12px 18px;border-top:1px solid var(--border);line-height:1.6;}
footer{text-align:center;color:var(--text-mut);font-size:11px;margin-top:24px;}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _pct(v: float, dec: int = 1, sinal: bool = True) -> str:
    s = f"{v*100:+.{dec}f}%" if sinal else f"{v*100:.{dec}f}%"
    return s.replace(".", ",")


def _cls(v: float) -> str:
    return "pos" if v > 0 else ("neg" if v < 0 else "")


# ─── Curva de capital (SVG) ───────────────────────────────────────────────────

def _line_chart(eq_s: List[float], eq_i: List[float], labels: List[str],
                w: int = 1080, h: int = 340) -> str:
    ml, mr, mt, mb = 52, 16, 14, 30
    pw, ph = w - ml - mr, h - mt - mb
    n = len(eq_s)
    if n < 2:
        return ""
    lo = min(min(eq_s), min(eq_i))
    hi = max(max(eq_s), max(eq_i))
    rng = (hi - lo) or 1.0
    # padding vertical
    lo -= rng * 0.06; hi += rng * 0.06; rng = hi - lo

    def X(i): return ml + pw * i / (n - 1)
    def Yv(v): return mt + ph * (1 - (v - lo) / rng)

    def path(eq):
        return "M" + " L".join(f"{X(i):.1f},{Yv(v):.1f}" for i, v in enumerate(eq))

    # gridlines horizontais (múltiplos de capital)
    grid = []
    import math
    steps = 5
    for g in range(steps + 1):
        val = lo + rng * g / steps
        y = Yv(val)
        grid.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{w-mr}" y2="{y:.1f}" '
                    f'stroke="#E8D4B8" stroke-width="1"/>')
        grid.append(f'<text x="{ml-7}" y="{y+3:.1f}" text-anchor="end" '
                    f'font-size="10" fill="#8A9E87">{val:.2f}x</text>')

    # rótulos de data no eixo x (~6)
    xlab = []
    n_lab = min(6, n)
    for j in range(n_lab):
        i = round(j * (n - 1) / (n_lab - 1))
        xlab.append(f'<text x="{X(i):.1f}" y="{h-9}" text-anchor="middle" '
                    f'font-size="10" fill="#8A9E87">{_esc(labels[i][:7])}</text>')

    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">'
        + "".join(grid)
        + f'<path d="{path(eq_i)}" fill="none" stroke="var(--marrom)" stroke-width="1.8"/>'
        + f'<path d="{path(eq_s)}" fill="none" stroke="var(--verde-med)" stroke-width="2.2"/>'
        + "".join(xlab)
        + '</svg>'
    )


# ─── Cards ────────────────────────────────────────────────────────────────────

def _card(lbl: str, val: str, cmp: str = "", cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    cmp_h = f'<div class="cmp">{cmp}</div>' if cmp else ""
    return (f'<div class="card"><div class="lbl">{_esc(lbl)}</div>'
            f'<div class="big"{c}>{val}</div>{cmp_h}</div>')


# ─── Entry point ──────────────────────────────────────────────────────────────

def gerar_relatorio_backtest(res: Dict, output_path: Path) -> Path:
    ms, mi = res["metr_s"], res["metr_i"]
    hist = res["historico"]
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    labels = ([hist[0]["data"]] + [h["data_fim"] for h in hist]) if hist else []

    cards = (
        _card("Retorno Total", _pct(ms["total"], 0), f'IBOV {_pct(mi["total"],0)}', _cls(ms["total"]))
        + _card("CAGR", _pct(ms["cagr"]), f'IBOV {_pct(mi["cagr"])}', _cls(ms["cagr"]))
        + _card("Alpha (a.a.)", _pct(res["alpha"]), f'β {res["beta"]:.2f}', _cls(res["alpha"]))
        + _card("Sharpe", f'{ms["sharpe"]:.2f}'.replace(".", ","), f'IBOV {mi["sharpe"]:.2f}'.replace(".", ","))
        + _card("Vol. Anual", _pct(ms["vol_anual"], 1, sinal=False), f'IBOV {_pct(mi["vol_anual"],1,sinal=False)}')
        + _card("Max Drawdown", _pct(ms["max_dd"]), f'IBOV {_pct(mi["max_dd"])}', "neg")
        + _card("Meses &gt; IBOV", _pct(res["hit"], 0, sinal=False), f'{ms["n_meses"]} meses')
        + _card("Turnover/mês", _pct(res["turnover"], 0, sinal=False), f'Top {res["top_n"]}')
    )

    # Tabela mês a mês
    linhas = []
    for h in reversed(hist):
        holds = " ".join(t for t, _, _, _ in h["detalhe"])
        linhas.append(
            f'<tr><td class="l">{_esc(h["data"])}</td>'
            f'<td class="{_cls(h["port_ret"])}">{_pct(h["port_ret"])}</td>'
            f'<td class="{_cls(h["ibov_ret"])}">{_pct(h["ibov_ret"])}</td>'
            f'<td class="{_cls(h["port_ret"]-h["ibov_ret"])}">{_pct(h["port_ret"]-h["ibov_ret"])}</td>'
            f'<td class="hold l">{_esc(holds)}</td></tr>'
        )

    htmldoc = (
        '<!doctype html><html lang="pt-BR"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Backtest de Momentum · Zelen Invest</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        f'<style>{_CSS}</style></head><body>'
        '<header class="top"><div class="wrap">'
        '<h1>Backtest de Momentum <span>· Zelen Invest</span></h1>'
        f'<div class="sub">Point-in-time · Top {res["top_n"]} mensal · '
        f'sinal {_esc(res.get("sinal_label", "A+B"))} · '
        f'{res["n_universo"]} ações · {ms.get("n_meses",0)} meses · {ts}</div>'
        '</div></header><div class="wrap">'
        # Métricas
        '<div class="panel"><div class="panel-head">'
        '<h2>Desempenho vs Ibovespa</h2><span class="tag">EQUAL-WEIGHT</span></div>'
        f'<div class="cards">{cards}</div></div>'
        # Curva
        '<div class="panel"><div class="panel-head"><h2>Curva de Capital (base 1,00x)</h2>'
        f'<span class="tag">{_esc(labels[0][:7]) if labels else ""} → {_esc(labels[-1][:7]) if labels else ""}</span></div>'
        '<div class="chart-box">'
        '<div class="legend">'
        '<span><i style="background:var(--verde-med)"></i>Estratégia</span>'
        '<span><i style="background:var(--marrom)"></i>Ibovespa</span></div>'
        f'{_line_chart(res["eq_s"], res["eq_i"], labels)}'
        '</div></div>'
        # Tabela
        '<div class="panel"><div class="panel-head"><h2>Rebalanceamentos mês a mês</h2>'
        '<span class="tag">MAIS RECENTE PRIMEIRO</span></div>'
        '<table><thead><tr>'
        '<th class="l">Mês</th><th>Carteira</th><th>IBOV</th><th>Excesso</th>'
        '<th class="l">Top (maior→menor score)</th>'
        '</tr></thead><tbody>'
        f'{"".join(linhas)}'
        '</tbody></table>'
        '<div class="foot">'
        'Backtest <b>point-in-time</b>: em cada mês o score A (operacional) usa apenas balanços '
        'já divulgados (DT_RECEB ≤ data) e o score B (técnico) apenas preços até a data. '
        'Carteira = Top-N por score combinado, peso igual, rebalanceada mensalmente, retorno via '
        'fechamento ajustado (total return). <b>Caveats</b>: survivorship bias (universo atual), '
        'Sharpe com rf=0, sem custos de transação.'
        '</div></div>'
        f'<footer>Backtest de Momentum · Zelen Invest · {ts} · Não é recomendação de investimento.</footer>'
        '</div></body></html>'
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(htmldoc, encoding="utf-8")
    return output_path
