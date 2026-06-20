"""
Histórico de ações em circulação (point-in-time) via FRE da CVM.

O Formulário de Referência (FRE) traz a composição de capital de cada companhia.
O arquivo `fre_cia_aberta_capital_social_YYYY.csv` tem a quantidade total de ações
(Capital Integralizado) por data de referência; o cabeçalho `fre_cia_aberta_YYYY.csv`
traz a DT_RECEB (divulgação) — usada para o corte point-in-time, sem look-ahead.

Permite reconstruir market cap histórico = ações_em(T) × preço_em(T), habilitando
EV/EBITDA e P/L as-of-T no backtest.

Caveat: o FRE é anual (granularidade ~1 atualização/ano), então diluições e
desdobramentos intra-ano só entram na atualização seguinte. Para nomes com split,
o pareamento ações×preço pode ficar impreciso entre o evento e o próximo FRE.
"""

from __future__ import annotations

import csv
import io
import ssl
import sys
import urllib.error
import urllib.request
import zipfile
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_FRE_URL = ("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/"
            "fre_cia_aberta_{year}.zip")

# Preferência de tipo de capital (mais "realizado" primeiro)
_TIPO_PRIO = ["Capital Integralizado", "Capital Subscrito", "Capital Emitido"]


def _log(msg: str, end: str = "\n") -> None:
    print(msg, end=end, file=sys.stderr, flush=True)


def _norm_cnpj(v: str) -> str:
    return "".join(c for c in (v or "") if c.isdigit())


def _download(url: str, target: Path, force: bool = False) -> Optional[Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return target
    _log(f"  ↓ {target.name}…")
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
            target.write_bytes(resp.read())
        return target
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _log(f"  ✗ 404 {target.name}")
            return None
        raise


def _iter_csv(zf: zipfile.ZipFile, member: str):
    if member not in zf.namelist():
        return
    raw = zf.read(member).decode("latin-1")
    yield from csv.DictReader(io.StringIO(raw), delimiter=";")


class SharesLookup:
    """Ações em circulação point-in-time, por CNPJ."""

    def __init__(self, por_cnpj: Dict[str, List[Tuple[str, float]]]):
        # por_cnpj: {cnpj: [(disclosure_date, shares), ...] ordenado por data}
        self._d: Dict[str, Tuple[List[str], List[float]]] = {}
        for cnpj, pares in por_cnpj.items():
            pares = sorted(pares)
            self._d[cnpj] = ([p[0] for p in pares], [p[1] for p in pares])

    def asof(self, cnpj: str, dt: str) -> Optional[float]:
        cnpj = _norm_cnpj(cnpj)
        rec = self._d.get(cnpj)
        if not rec:
            return None
        datas, vals = rec
        i = bisect_right(datas, dt) - 1
        return vals[i] if i >= 0 else None

    def __len__(self) -> int:
        return len(self._d)


def load_shares(
    anos: List[int],
    cache_dir: Path = Path("cache_cvm"),
    force_download: bool = False,
) -> SharesLookup:
    """
    Carrega o histórico de ações em circulação para os anos FRE indicados.

    Returns SharesLookup com asof(cnpj, data) → quantidade total de ações divulgada
    até aquela data.
    """
    cache_dir = Path(cache_dir)
    por_cnpj: Dict[str, List[Tuple[str, float]]] = {}

    for year in anos:
        zp = _download(_FRE_URL.format(year=year),
                       cache_dir / "fre" / f"fre_cia_aberta_{year}.zip",
                       force=force_download)
        if not zp:
            continue
        try:
            with zipfile.ZipFile(zp) as zf:
                # 1) DT_RECEB por (CNPJ, DT_REFER, VERSAO) do cabeçalho
                receb: Dict[Tuple[str, str, str], str] = {}
                for r in _iter_csv(zf, f"fre_cia_aberta_{year}.csv"):
                    k = (_norm_cnpj(r.get("CNPJ_CIA", "")),
                         (r.get("DT_REFER", "") or "")[:10],
                         r.get("VERSAO", ""))
                    dtr = (r.get("DT_RECEB", "") or "")[:10]
                    if dtr:
                        receb[k] = dtr

                # 2) melhor tipo de capital por (CNPJ, DT_REFER, VERSAO)
                #    guarda {chave: (prioridade, shares)}
                best: Dict[Tuple[str, str, str], Tuple[int, float]] = {}
                for r in _iter_csv(zf, f"fre_cia_aberta_capital_social_{year}.csv"):
                    tipo = (r.get("Tipo_Capital", "") or "").strip()
                    if tipo not in _TIPO_PRIO:
                        continue
                    qtd = (r.get("Quantidade_Total_Acoes", "") or "").strip()
                    try:
                        shares = float(qtd)
                    except ValueError:
                        continue
                    if shares <= 0:
                        continue
                    cnpj = _norm_cnpj(r.get("CNPJ_Companhia", ""))
                    k = (cnpj, (r.get("Data_Referencia", "") or "")[:10],
                         r.get("Versao", ""))
                    prio = _TIPO_PRIO.index(tipo)
                    cur = best.get(k)
                    if cur is None or prio < cur[0]:
                        best[k] = (prio, shares)

                # 3) acumula pares (disclosure_date, shares); fallback DT_REFER+150d
                for (cnpj, dt_refer, versao), (_, shares) in best.items():
                    disc = receb.get((cnpj, dt_refer, versao))
                    if not disc:
                        try:
                            from datetime import timedelta as _td
                            disc = (date.fromisoformat(dt_refer) + _td(days=150)).isoformat()
                        except ValueError:
                            disc = dt_refer
                    por_cnpj.setdefault(cnpj, []).append((disc, shares))
        except Exception as exc:                  # noqa: BLE001
            _log(f"  ⚠ erro FRE {year}: {exc}")

    _log(f"FRE: {len(por_cnpj)} companhias com histórico de ações")
    return SharesLookup(por_cnpj)


# ─── CLI de teste ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    anos = list(range(2020, date.today().year + 1))
    sl = load_shares(anos, force_download="--no-cache" in sys.argv)
    # AMBP3 = CNPJ 12.648.266/0001-24
    cnpj = "12648266000124"
    print(f"\nAmbipar (ações em circulação por data):")
    rec = sl._d.get(cnpj)
    if rec:
        for d, v in zip(*rec):
            print(f"  divulgado {d}: {v/1e6:.1f} mi ações")
    for T in ("2022-06-30", "2024-06-30", "2025-06-30", "2026-06-01"):
        print(f"  asof {T}: {(sl.asof(cnpj, T) or 0)/1e6:.1f} mi")
