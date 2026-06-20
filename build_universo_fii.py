"""
Monta o universo de FIIs de TIJOLO líquidos → empresas_fii.csv.

Não há mapa público CNPJ↔ticker de FII, então parto de uma lista curada de tickers
tijolo líquidos (por segmento) + um fragmento de nome, e resolvo o CNPJ na base do
informe CVM (fii_client), confirmando a classificação tijolo.

    python build_universo_fii.py
"""

from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

from fii_client import carregar_fiis, classificar_tijolo

# (ticker, fragmento de nome p/ casar na base CVM, segmento)
_CURADOS = [
    # Logística
    ("HGLG11", "PATRIA LOG", "Logística"), ("BTLG11", "BTGP LOGISTICA", "Logística"),
    ("XPLG11", "XP LOG", "Logística"), ("VILG11", "VINCI LOGISTICA", "Logística"),
    ("BRCO11", "BRESCO", "Logística"), ("LVBI11", "VBI LOG", "Logística"),
    ("GGRC11", "GGR COVEPI", "Logística"), ("PATL11", "PATRIA LOGISTICA", "Logística"),
    ("HSLG11", "HSI LOGISTICA", "Logística"),
    # Lajes corporativas
    ("KNRI11", "KINEA RENDA IMOBILIARIA", "Lajes"), ("PVBI11", "VBI PRIME", "Lajes"),
    ("HGRE11", "CSHG REAL ESTATE", "Lajes"), ("RCRB11", "RIO BRAVO RENDA CORP", "Lajes"),
    ("JSRE11", "JS REAL ESTATE", "Lajes"), ("BRCR11", "BC FUND", "Lajes"),
    ("RBRP11", "RBR PROPERTIES", "Lajes"), ("ONEF11", "THE ONE", "Lajes"),
    ("HGPO11", "CSHG PRIME OFFICES", "Lajes"), ("VINO11", "VINCI OFFICES", "Lajes"),
    # Shopping
    ("XPML11", "XP MALLS", "Shopping"), ("VISC11", "VINCI SHOPPING", "Shopping"),
    ("HGBS11", "HEDGE BRASIL SHOPPING", "Shopping"), ("MALL11", "MALLS BRASIL", "Shopping"),
    ("HSML11", "HSI MALLS", "Shopping"), ("JRDM11", "SHOPPING JARDIM SUL", "Shopping"),
    ("ABCP11", "GRAND PLAZA", "Shopping"),
    # Renda urbana / varejo / híbrido-tijolo
    ("HGRU11", "CSHG RENDA URBANA", "Renda Urbana"), ("TRXF11", "TRX REAL ESTATE", "Renda Urbana"),
    ("RBVA11", "RIO BRAVO RENDA VAREJO", "Renda Urbana"), ("RBRF11", "RBR ALPHA", "FOF"),
    # Logística/industrial adicionais
    ("SDIL11", "SDI LOGISTICA", "Logística"), ("ALZR11", "ALIANZA", "Híbrido"),
    ("FIIP11", "RB CAPITAL RENDA I", "Renda"),
]


def _norm(x: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (x or "").upper())
                   if not unicodedata.combining(c))


def main() -> int:
    print("Carregando base CVM-FII (2024-2025)…")
    base = carregar_fiis([2024, 2025])
    idx = [(k, v, _norm(v.nome)) for k, v in base.items()]
    linhas = []
    nao_achou = []
    for ticker, frag, seg in _CURADOS:
        fragn = _norm(frag)
        cands = [(k, v) for k, v, nm in idx if fragn in nm]
        # prefere o tijolo de maior PL (descarta sub-classes/espelhos)
        cands = [(k, v) for k, v in cands if classificar_tijolo(v)[0] in ("tijolo", "hibrido")]
        cands.sort(key=lambda kv: -((kv[1].ultimo() or {}).get("pl") or 0))
        if not cands:
            nao_achou.append(ticker); continue
        cnpj, fii = cands[0]
        classe, frac = classificar_tijolo(fii)
        linhas.append({"ticker": ticker, "cnpj": cnpj, "nome": fii.nome[:48],
                       "segmento": seg, "classe": classe, "frac_imoveis": round(frac, 2)})
    out = Path("empresas_fii.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "cnpj", "nome", "segmento", "classe", "frac_imoveis"])
        w.writeheader(); w.writerows(linhas)
    print(f"\nResolvidos: {len(linhas)}/{len(_CURADOS)}  ·  não achou: {nao_achou}")
    for l in linhas:
        print(f"  {l['ticker']:8} {l['cnpj']} {l['classe']:8} {l['segmento']:13} {l['nome']}")
    print(f"\nArquivo: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
