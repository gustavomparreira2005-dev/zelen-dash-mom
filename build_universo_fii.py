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


# Override CNPJ→ticker para os grandes que NÃO trazem ISIN no informe (Res. 175).
_CURADO_CNPJ = {
    "11.728.688/0001-47": "HGLG11", "11.839.593/0001-09": "BTLG11",
    "12.005.956/0001-65": "KNRI11", "28.757.546/0001-00": "XPML11",
    "17.554.274/0001-25": "VISC11", "26.502.794/0001-85": "XPLG11",
    "24.853.044/0001-22": "VILG11", "08.431.747/0001-06": "HGBS11",
}


def _seg_por_nome(nome: str) -> str:
    n = _norm(nome)
    if any(k in n for k in ("LOG", "GALPAO", "INDUSTRIAL")):
        return "Logística"
    if any(k in n for k in ("SHOPPING", "MALL", "VAREJO")):
        return "Shopping/Varejo"
    if any(k in n for k in ("CORPORAT", "OFFICE", "LAJE", "ESCRITORIO")):
        return "Lajes"
    if any(k in n for k in ("RENDA URBANA", "URBAN")):
        return "Renda Urbana"
    if any(k in n for k in ("HOSPITAL", "SAUDE", "EDUC", "HOTEL", "AGENC", "BANCO")):
        return "Renda/Especial"
    return "Tijolo (outros)"


def main() -> int:
    print("Carregando base CVM-FII (2021-2025)…")
    base = carregar_fiis([2021, 2022, 2023, 2024, 2025])
    norm_cnpj = lambda c: "".join(ch for ch in c if ch.isdigit())
    cur = {norm_cnpj(k): v for k, v in _CURADO_CNPJ.items()}

    linhas, sem_ticker = [], 0
    for cnpj, fii in base.items():
        classe, frac = classificar_tijolo(fii)
        if classe not in ("tijolo", "hibrido"):
            continue
        u = fii.ultimo() or {}
        if (u.get("pl") or 0) < 50e6:           # corta micro-fundos (< R$50mi PL)
            continue
        ticker = cur.get(norm_cnpj(cnpj)) or fii.ticker_isin()
        if not ticker:
            sem_ticker += 1
            continue
        linhas.append({"ticker": ticker, "cnpj": cnpj, "nome": fii.nome[:48],
                       "segmento": _seg_por_nome(fii.nome), "classe": classe,
                       "frac_imoveis": round(frac, 2)})
    # dedup por ticker (mantém o de maior PL)
    by_tk = {}
    for l in linhas:
        cur_l = by_tk.get(l["ticker"])
        pl = (base[l["cnpj"]].ultimo() or {}).get("pl") or 0
        if not cur_l or pl > cur_l[1]:
            by_tk[l["ticker"]] = (l, pl)
    linhas = sorted((v[0] for v in by_tk.values()), key=lambda x: x["ticker"])

    out = Path("empresas_fii.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "cnpj", "nome", "segmento", "classe", "frac_imoveis"])
        w.writeheader(); w.writerows(linhas)
    from collections import Counter
    seg = Counter(l["segmento"] for l in linhas)
    print(f"\nTijolo/híbrido com ticker (PL≥50mi): {len(linhas)}  ·  sem ticker: {sem_ticker}")
    for s, n in seg.most_common():
        print(f"  {n:3}  {s}")
    print(f"\nArquivo: {out.resolve()} (filtro final de liquidez é no main_fii)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
