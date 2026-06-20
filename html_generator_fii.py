"""
Gerador de HTML do screener de FIIs de tijolo (qualidade + renda).

Reusa o CSS e o padrão visual do html_generator_acoes, mas com colunas próprias
de FII (Q qualidade · P/VP · DY · vol · upside DDM · segmento) e o seletor de 3
abas (🇧🇷 Ações · 🇺🇸 EUA · 🏢 FIIs).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

from html_generator_acoes import _CSS, _esc


def _n(v, dec=2, suf=""):
    return f"{v:,.{dec}f}{suf}".replace(",", "X").replace(".", ",").replace("X", ".") if v is not None else "—"


def _pct(v, dec=1, sinal=False):
    if v is None:
        return "—"
    s = f"{v*100:+.{dec}f}%" if sinal else f"{v*100:.{dec}f}%"
    return s.replace(".", ",")


def _qcol(q):
    c = "var(--verde-med)" if q >= 80 else "var(--marrom)" if q >= 60 else "var(--verm)"
    return f'<b style="color:{c}">{q:.0f}</b>'


def _upcol(u):
    if u is None:
        return "—"
    c = "var(--verde-med)" if u > 0.10 else "var(--marrom)" if u > -0.05 else "var(--verm)"
    return f'<b style="color:{c}">{_pct(u, 1, True)}</b>'


def gerar_relatorio_fii(itens: List[Dict], output_path: Path) -> Path:
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    com = [e for e in itens if e.get("score_qualidade") is not None]
    com.sort(key=lambda e: -(e.get("score_qualidade") or 0))

    def _pill(label, href, active):
        bg = "var(--marrom)" if active else "transparent"
        col = "#fff" if active else "rgba(243,229,208,.8)"
        return (f'<a href="{href}" style="padding:5px 13px;border-radius:14px;font-size:12px;'
                f'font-weight:600;text-decoration:none;background:{bg};color:{col};'
                f'border:1px solid var(--marrom);">{label}</a>')
    seletor = ('<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">'
               + _pill("🇧🇷 Ações", "momentum_acoes.html", False)
               + _pill("🇺🇸 EUA", "momentum_us.html", False)
               + _pill("🏢 FIIs", "momentum_fii.html", True)
               + f'<span style="font-size:11px;color:rgba(243,229,208,.6);margin-left:8px">'
                 f'{len(com)} FIIs tijolo · {ts}</span></div>')

    segs = sorted({(e.get("segmento") or "") for e in com if e.get("segmento")})
    opt_seg = '<option value="">Segmento: todos</option>' + "".join(
        f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in segs)

    cols = [("Q", "q", "qualidade"), ("Segmento", "seg", ""), ("P/VP", "pvp", ""),
            ("DY", "dy", "12m"), ("Vol an.", "vol", ""), ("Não-dil.", "dil", "VP/cota a/a"),
            ("Upside", "up", "DDM"), ("Preço", "preco", ""), ("Justo", "justo", "DDM"),
            ("Liq/dia", "liq", "")]
    th = '<th class="l">#</th><th class="l">FII</th>' + "".join(
        f'<th class="sortable" onclick="sortBy(\'{k}\')">{lbl}'
        f'{"<br><span style=font-size:9px;opacity:.6>"+sub+"</span>" if sub else ""}</th>'
        for lbl, k, sub in cols)

    rows = []
    for i, e in enumerate(com, 1):
        dil_lbl = ("✓ acretivo" if e.get("acretivo") else "⚠ diluiu")
        dil_col = "var(--verde-med)" if e.get("acretivo") else "var(--verm)"
        attrs = (f'data-q="{e.get("score_qualidade") or 0}" data-seg="{_esc(e.get("segmento") or "")}" '
                 f'data-pvp="{e.get("pvp") or 0}" data-dy="{(e.get("dy") or 0)*100:.2f}" '
                 f'data-vol="{(e.get("vol_anual") or 0)*100:.2f}" data-dil="{(e.get("vp_cota_cagr") or 0)*100:.2f}" '
                 f'data-up="{(e.get("upside") or 0)*100:.2f}" data-preco="{e.get("preco") or 0}" '
                 f'data-justo="{e.get("preco_justo") or 0}" data-liq="{(e.get("liq_2m") or 0)/1e6:.3f}"')
        rows.append(
            f'<tr class="row" {attrs}>'
            f'<td class="rank">{i}</td>'
            f'<td class="l"><span class="tk">{_esc(e["ticker"])}</span><br>'
            f'<span class="nm">{_esc((e.get("nome") or "")[:30])}</span></td>'
            f'<td>{_qcol(e.get("score_qualidade") or 0)}</td>'
            f'<td class="l">{_esc(e.get("segmento") or "—")}</td>'
            f'<td>{_n(e.get("pvp"), 2)}</td>'
            f'<td>{_pct(e.get("dy"))}</td>'
            f'<td>{_pct(e.get("vol_anual"))}</td>'
            f'<td style="color:{dil_col};font-size:11px">{dil_lbl}<br>{_pct(e.get("vp_cota_cagr"),1,True)}</td>'
            f'<td>{_upcol(e.get("upside"))}</td>'
            f'<td>R$ {_n(e.get("preco"), 2)}</td>'
            f'<td>R$ {_n(e.get("preco_justo"), 2)}</td>'
            f'<td>{_n((e.get("liq_2m") or 0)/1e6, 1)} mi</td>'
            f'</tr>')

    js = """
function sortBy(k){var tb=document.getElementById('fii-tb');var rs=[].slice.call(tb.querySelectorAll('tr.row'));
var asc=tb.getAttribute('data-sk')===k?!(tb.getAttribute('data-asc')==='1'):false;
rs.sort(function(a,b){var x=parseFloat(a.getAttribute('data-'+k))||0,y=parseFloat(b.getAttribute('data-'+k))||0;return asc?x-y:y-x;});
rs.forEach(function(r,i){r.cells[0].textContent=i+1;tb.appendChild(r);});
tb.setAttribute('data-sk',k);tb.setAttribute('data-asc',asc?'1':'0');}
function filtSeg(){var v=document.getElementById('fseg').value;
[].forEach.call(document.querySelectorAll('#fii-tb tr.row'),function(r){
r.style.display=(!v||r.getAttribute('data-seg')===v)?'':'none';});}
"""
    html = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Screener FIIs · Zelen Invest</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        f'<style>{_CSS}</style></head><body>'
        '<header class="top"><div class="wrap">'
        '<h1>Momentum 🏢 FIIs <span>· Zelen Invest</span></h1>'
        + seletor +
        '</div></header>'
        '<div class="wrap"><div class="panel">'
        '<div class="panel-head"><h2>FIIs de Tijolo · Qualidade &amp; Renda <span style="font-weight:400;opacity:.8">· Zelen Invest</span></h2>'
        '<span class="tag">CLIQUE NO CABEÇALHO P/ ORDENAR</span></div>'
        '<div class="screener" style="padding:10px 0"><div class="filt-build">'
        f'<select id="fseg" onchange="filtSeg()">{opt_seg}</select>'
        '<span style="font-size:11px;color:var(--text-mut);margin-left:8px">'
        'Q = qualidade (não-diluição · consistência · baixa vol · porte) · Valuation por renda (DDM), não P/VP</span>'
        '</div></div>'
        '<div class="tbl-wrap"><table class="screener-tbl"><thead><tr>'
        + th + '</tr></thead><tbody id="fii-tb">' + "".join(rows) + '</tbody></table></div>'
        '</div></div>'
        f'<script>{js}</script></body></html>'
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
