"""
Cliente CVM-FII — informe mensal dos Fundos Imobiliários (dados.cvm.gov.br).

FII não é empresa: não tem DRE/balanço corporativo. O que importa vem do INFORME
MENSAL da CVM (3 arquivos por ano):
  · complemento  → Patrimônio Líquido, Cotas Emitidas, Valor Patrimonial da Cota
  · ativo_passivo→ composição do ativo (imóveis vs CRI vs cotas) + Rendimentos a Distribuir
  · geral        → nome, ISIN, tipo

Daqui saem as métricas do dashboard de FII: P/VP, Dividend Yield, classificação
tijolo vs papel, e a série de distribuições.

    from fii_client import carregar_fiis, dados_fii
    base = carregar_fiis(anos=[2024, 2025])     # {cnpj: FiiData}
"""

from __future__ import annotations

import csv
import io
import ssl
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CTX = ssl.create_default_context()
_UA = {"User-Agent": "Zelen Invest research"}
_CACHE = Path("_cache_fii")
_URL = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{ano}.zip"


def _num(v: str) -> Optional[float]:
    v = (v or "").strip().replace(",", ".")
    if not v or v.upper() in ("NA", "N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass
class FiiData:
    cnpj: str
    nome: str = ""
    isin: str = ""
    serie: List[dict] = field(default_factory=list)   # mensal, ordenado por data

    def ticker_isin(self) -> Optional[str]:
        """Ticker derivado do ISIN (BR**XXXX**CTF00n → XXXX11). None se sem ISIN."""
        z = (self.isin or "").strip().upper()
        return (z[2:6] + "11") if len(z) >= 6 and z.startswith("BR") else None

    def ultimo(self) -> Optional[dict]:
        return self.serie[-1] if self.serie else None

    def distribuicoes_12m(self) -> List[float]:
        """Rendimentos por cota dos últimos 12 meses (R$/cota)."""
        out = []
        for m in self.serie[-12:]:
            rd, cotas = m.get("rend_distribuir"), m.get("cotas")
            if rd is not None and cotas:
                out.append(rd / cotas)
        return out


def _baixar_zip(ano: int, force: bool = False) -> Optional[bytes]:
    _CACHE.mkdir(parents=True, exist_ok=True)
    cache = _CACHE / f"inf_mensal_fii_{ano}.zip"
    if cache.exists() and not force:
        return cache.read_bytes()
    try:
        req = urllib.request.Request(_URL.format(ano=ano), headers=_UA)
        with urllib.request.urlopen(req, context=_CTX, timeout=180) as r:
            data = r.read()
        cache.write_bytes(data)
        return data
    except Exception:
        return None


def _ler_csv(zf: zipfile.ZipFile, nome: str) -> List[dict]:
    if nome not in zf.namelist():
        return []
    with zf.open(nome) as fh:
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter=";"))


def carregar_fiis(anos: List[int], force: bool = False) -> Dict[str, FiiData]:
    """Carrega o informe mensal dos anos dados → {cnpj: FiiData} com série mensal."""
    por_cnpj: Dict[str, FiiData] = {}
    # acumula por (cnpj, data) juntando os 3 arquivos
    acc: Dict[Tuple[str, str], dict] = {}
    nomes: Dict[str, str] = {}
    isins: Dict[str, str] = {}
    for ano in sorted(anos):
        raw = _baixar_zip(ano, force)
        if not raw:
            continue
        zf = zipfile.ZipFile(io.BytesIO(raw))
        comp = _ler_csv(zf, f"inf_mensal_fii_complemento_{ano}.csv")
        ativ = _ler_csv(zf, f"inf_mensal_fii_ativo_passivo_{ano}.csv")
        geral = _ler_csv(zf, f"inf_mensal_fii_geral_{ano}.csv")
        for r in geral:
            cnpj = (r.get("CNPJ_Fundo_Classe") or r.get("CNPJ_Fundo") or "").strip()
            if cnpj:
                nomes[cnpj] = r.get("Nome_Fundo_Classe") or r.get("Nome_Fundo") or nomes.get(cnpj, "")
                iz = (r.get("Codigo_ISIN") or "").strip()
                if iz:
                    isins[cnpj] = iz
        for r in comp:
            cnpj = (r.get("CNPJ_Fundo_Classe") or r.get("CNPJ_Fundo") or "").strip()
            dt = (r.get("Data_Referencia") or "").strip()
            if not cnpj or not dt:
                continue
            d = acc.setdefault((cnpj, dt), {"cnpj": cnpj, "data": dt})
            d["pl"] = _num(r.get("Patrimonio_Liquido"))
            d["cotas"] = _num(r.get("Cotas_Emitidas"))
            d["vp_cota"] = _num(r.get("Valor_Patrimonial_Cotas"))
            d["valor_ativo"] = _num(r.get("Valor_Ativo"))
        for r in ativ:
            cnpj = (r.get("CNPJ_Fundo_Classe") or r.get("CNPJ_Fundo") or "").strip()
            dt = (r.get("Data_Referencia") or "").strip()
            if not cnpj or not dt:
                continue
            d = acc.setdefault((cnpj, dt), {"cnpj": cnpj, "data": dt})
            imoveis = sum(filter(None, [
                _num(r.get("Imoveis_Renda_Acabados")), _num(r.get("Imoveis_Renda_Construcao")),
                _num(r.get("Imoveis_Venda_Acabados")), _num(r.get("Imoveis_Venda_Construcao")),
                _num(r.get("Outros_Direitos_Reais")), _num(r.get("Direitos_Bens_Imoveis")),
            ])) or 0.0
            papel = sum(filter(None, [
                # CRIs e títulos de renda fixa — o lado "papel" (estava faltando!):
                _num(r.get("Titulos_Privados")),      # CRIs (principal ativo de fundo de papel)
                _num(r.get("Titulos_Publicos")), _num(r.get("Fundos_Renda_Fixa")),
                _num(r.get("Certificados_Deposito_Valores_Mobiliarios")),
                _num(r.get("Outras_Cotas_FI")), _num(r.get("Cotas_Sociedades_Atividades_FII")),
                _num(r.get("Outros_Valores_Mobliarios")),
            ])) or 0.0
            d["imoveis"] = imoveis
            d["papel"] = papel
            d["rend_distribuir"] = _num(r.get("Rendimentos_Distribuir"))

    # monta as séries ordenadas
    series: Dict[str, List[dict]] = {}
    for (cnpj, dt), d in acc.items():
        series.setdefault(cnpj, []).append(d)
    for cnpj, lst in series.items():
        lst.sort(key=lambda x: x["data"])
        por_cnpj[cnpj] = FiiData(cnpj=cnpj, nome=nomes.get(cnpj, ""),
                                 isin=isins.get(cnpj, ""), serie=lst)
    return por_cnpj


def classificar_tijolo(fii: FiiData) -> Tuple[str, float]:
    """(classe, imóveis/PL). Usa o PATRIMÔNIO LÍQUIDO como denominador (reportado de
    forma confiável no complemento), não imóveis/(imóveis+papel) — porque os campos
    de ativo vêm incompletos para muitos fundos de papel (ex.: MXRF mostra R$18mi de
    ativos num fundo de R$4bi). Imóveis sendo a maior parte do PL = tijolo de verdade."""
    u = fii.ultimo() or {}
    imoveis = u.get("imoveis") or 0.0
    pl = u.get("pl") or 0.0
    if pl <= 0:
        return "indef", 0.0
    frac = imoveis / pl
    if frac >= 0.55:
        return "tijolo", frac
    if frac <= 0.20:
        return "papel", frac
    return "hibrido", frac


def _norm_cnpj(c: str) -> str:
    return "".join(ch for ch in (c or "") if ch.isdigit())


def serie_preco_div(ticker: str, range_: str = "3y") -> Optional[dict]:
    """Yahoo: série de fechamento + dividendos (proventos) de um FII (TICKER.SA).
    Fonte confiável da distribuição real (o campo da CVM é só o saldo a distribuir)."""
    import json
    sym = ticker if ticker.endswith(".SA") or ticker.startswith("^") else f"{ticker}.SA"
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?range={range_}&interval=1d&events=div")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
            j = json.load(r)
        res = j["chart"]["result"][0]
        meta = res.get("meta", {})
        ts = res.get("timestamp", []) or []
        q = res.get("indicators", {}).get("quote", [{}])[0]
        closes = q.get("close") or []
        vols = q.get("volume") or []
        divs = [(int(d["date"]), float(d["amount"]))
                for d in res.get("events", {}).get("dividends", {}).values()]
        return {"preco": meta.get("regularMarketPrice"),
                "timestamps": ts, "close": closes, "volume": vols, "dividendos": sorted(divs)}
    except Exception:
        return None


if __name__ == "__main__":
    # Prova: tijolo conhecidos (CNPJ) — HGLG11, KNRI11, XPML11, VISC11, BTLG11
    alvos = {
        "11.728.688/0001-47": "HGLG11", "12.005.956/0001-65": "KNRI11",
        "28.757.546/0001-00": "XPML11", "17.554.274/0001-25": "VISC11",
        "11.839.593/0001-09": "BTLG11",
    }
    base = carregar_fiis([2024, 2025])
    print(f"FIIs no informe: {len(base)}\n")
    by_norm = {_norm_cnpj(k): v for k, v in base.items()}
    for cnpj, tk in alvos.items():
        f = by_norm.get(_norm_cnpj(cnpj))
        if not f:
            print(f"{tk}: não encontrado"); continue
        u = f.ultimo() or {}
        classe, frac = classificar_tijolo(f)
        dist = f.distribuicoes_12m()
        print(f"{tk:8} {f.nome[:34]:36} {u.get('data')}")
        print(f"   PL R$ {(u.get('pl') or 0)/1e6:,.0f} mi · cotas {(u.get('cotas') or 0)/1e6:,.1f} mi · "
              f"VP/cota R$ {u.get('vp_cota')} · {classe} ({frac*100:.0f}% imóveis)")
        print(f"   dist 12m R$/cota: {[round(x,2) for x in dist]} (soma {sum(dist):.2f})")
