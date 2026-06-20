"""
Pipeline de FIIs de tijolo — qualidade + renda.

empresas_fii.csv (build_universo_fii) → CVM informe (P/VP, não-diluição) +
Yahoo (preço, DY, vol) → score de qualidade + valuation DDM → momentum_fii.html.

    python main_fii.py                # universo inteiro
    python main_fii.py --liq-min 3e5  # filtro de liquidez (R$/dia)
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

R = "\033[0m"; B = "\033[1m"; G = "\033[32m"; C = "\033[36m"


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Momentum FIIs tijolo — Zelen")
    ap.add_argument("--liq-min", type=float, default=50_000.0, help="liquidez mínima R$/dia")
    ap.add_argument("--out", type=Path, default=Path("relatorios/momentum_fii.html"))
    args = ap.parse_args()
    t0 = time.time()

    from fii_client import carregar_fiis, _norm_cnpj
    from indicadores_fii import avaliar_fii
    from html_generator_fii import gerar_relatorio_fii

    with open("empresas_fii.csv", encoding="utf-8", newline="") as f:
        universo = list(csv.DictReader(f))
    print(f"{C}{B}[CVM]{R}  Carregando informe mensal (2021-2025)…", file=sys.stderr)
    base = carregar_fiis([2021, 2022, 2023, 2024, 2025])
    by = {_norm_cnpj(k): v for k, v in base.items()}

    print(f"{C}{B}[FII]{R}  Avaliando {len(universo)} FIIs (P/VP · DY · não-diluição · DDM)…", file=sys.stderr)
    itens = []
    for i, row in enumerate(universo, 1):
        tk = row["ticker"].strip().upper()
        print(f"\r  {i}/{len(universo)}  {tk:<8}   ", end="", file=sys.stderr, flush=True)
        fii = by.get(_norm_cnpj(row["cnpj"]))
        if not fii:
            continue
        r = avaliar_fii(fii, tk)
        if r.get("erro") or r.get("score_qualidade") is None:
            continue
        r["segmento"] = row.get("segmento") or r.get("classe")
        itens.append(r)
    print("", file=sys.stderr)

    n_antes = len(itens)
    itens = [e for e in itens if (e.get("liq_2m") or 0) >= args.liq_min]
    print(f"{G}OK{R} — {n_antes} avaliados · {n_antes-len(itens)} removidos por liquidez "
          f"< R$ {args.liq_min/1e3:.0f}k/dia · {len(itens)} no dashboard")

    gerar_relatorio_fii(itens, args.out)
    print(f"\n{G}{B}✓ FIIs em {time.time()-t0:.1f}s{R} · {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
