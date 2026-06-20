"""
Monta o universo S&P 1500 (= S&P 500 + S&P 400 MidCap + S&P 600 SmallCap) a partir
das listas de constituintes da Wikipedia → empresas_us.csv.

Equivalente americano do build_empresa_lista (Brasil/CVM). Saída: ticker (formato
Yahoo, ex. BRK-B), nome, setor GICS, índice (500/400/600).

    python build_universo_us.py
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests

_UA = {"User-Agent": "Mozilla/5.0 (Zelen Invest research)"}
_FONTES = [
    ("500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
    ("400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"),
    ("600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"),
]


def _col(df: pd.DataFrame, *cands: str):
    for c in cands:
        for col in df.columns:
            if str(col).strip().lower() == c.lower():
                return col
    return None


def _tabela_constituintes(html: str) -> pd.DataFrame:
    """Acha a 1ª tabela que tem coluna Symbol/Ticker."""
    for df in pd.read_html(io.StringIO(html)):
        if _col(df, "Symbol", "Ticker symbol", "Ticker"):
            return df
    raise RuntimeError("tabela de constituintes não encontrada")


def coletar() -> List[Dict[str, str]]:
    linhas: Dict[str, Dict[str, str]] = {}
    for indice, url in _FONTES:
        html = requests.get(url, headers=_UA, timeout=60).text
        df = _tabela_constituintes(html)
        c_sym = _col(df, "Symbol", "Ticker symbol", "Ticker")
        c_nome = _col(df, "Security", "Company")
        c_set = _col(df, "GICS Sector", "GICS sector")
        for _, r in df.iterrows():
            sym = str(r[c_sym]).strip().upper().replace(".", "-")  # BRK.B → BRK-B (Yahoo)
            if not sym or sym == "NAN":
                continue
            if sym not in linhas:                                  # 1º índice que listou vence
                linhas[sym] = {
                    "ticker": sym,
                    "nome": str(r[c_nome]).strip() if c_nome else "",
                    "setor_gics": str(r[c_set]).strip() if c_set else "",
                    "indice": indice,
                }
        print(f"  S&P {indice}: {len(df)} linhas")
    return list(linhas.values())


def main() -> int:
    print("Coletando constituintes do S&P 1500 (Wikipedia)…")
    lista = coletar()
    out = Path("empresas_us.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "nome", "setor_gics", "indice"])
        w.writeheader()
        w.writerows(sorted(lista, key=lambda x: x["ticker"]))
    from collections import Counter
    dist = Counter(x["indice"] for x in lista)
    print(f"\nTotal único: {len(lista)}  ·  500:{dist['500']} 400:{dist['400']} 600:{dist['600']}")
    print(f"Arquivo: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
