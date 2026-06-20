"""
Indicadores de companhias FINANCEIRAS (bancos, seguradoras, B3/corretoras).

O `indicadores_empresas.py` assume o DRE padrão (3.01 receita, 3.05 EBIT, CMV,
capital de giro) — que não existe para instituição financeira. Aqui extraímos o
que o modelo de equity (FCFE) precisa, direto do template financeiro da CVM:

  · Lucro líquido atribuível aos controladores   (DRE 3.09.01 / busca por descrição)
  · Patrimônio líquido atribuível à controladora (BPP 2.08 / busca por descrição)
  · Receita de intermediação / prêmios           (DRE 3.01)
  · Ativo total                                   (BPA 1)
  → ROE = LL/PL, alavancagem = Ativo/PL, margem líquida = LL/Receita.

Convenção idêntica ao pipeline: valores retornados em REAIS (escala já aplicada);
quem consome (main_acoes / FCFE) divide por 1e6 para R$ mi.

Retorna um dict com as MESMAS chaves que `calcular_indicadores` usa no pipeline,
para rotear sem atrito (setor, ind, campos_brutos, historico_brutos…), além de
`tipo_financeira` e `modelo_valuation="FCFE"`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from cvm_client import EmpresaData, DemosPivot


# ─── Classificação do sub-tipo financeiro ─────────────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()


def classificar_tipo(setor_cvm: str) -> str:
    """banco | seguradora | bolsa | corretora | financeira (default)."""
    s = _norm(setor_cvm)
    if "BANCO" in s:
        return "banco"
    if "SEGUR" in s or "PREVID" in s or "CAPITALIZ" in s:
        return "seguradora"
    if "BOLSA" in s:
        return "bolsa"
    if "CORRETOR" in s or "DISTRIBUIDORA DE TITULOS" in s:
        return "corretora"
    if any(k in s for k in ("ARRENDAMENTO", "INTERMEDIACAO", "CREDITO", "SECURITIZ", "FINANC")):
        return "financeira"
    return "financeira"


# ─── Extração robusta (por descrição, com fallback por código) ────────────────

def _lucro_liquido(dre: DemosPivot, dt: str, sc: float) -> Optional[float]:
    """Lucro líquido atribuível aos sócios da CONTROLADORA (exclui minoritários)."""
    # 1) linha explícita "atribuído a sócios da empresa controladora"
    for cd, desc in sorted(dre.descricoes.items(), key=lambda x: x[0]):
        d = _norm(desc)
        if "ATRIBU" in d and "CONTROLAD" in d and "NAO CONTROLAD" not in d and cd.startswith("3."):
            v = dre.pivot.get(cd, {}).get(dt)
            if v is not None:
                return v * sc
    # 2) lucro/prejuízo consolidado do período (inclui minoritários, mas é o total)
    v = dre.first_match("3", r"lucro.*preju.*(consolidado|per.odo)|resultado l.quido.*(consolidado|per.odo)", dt)
    if v is not None:
        return v * sc
    # 3) fallback por código (banco=3.09, padrão=3.11)
    for cd in ("3.09.01", "3.09", "3.11.01", "3.11", "3.13"):
        v = dre.pivot.get(cd, {}).get(dt)
        if v is not None:
            return v * sc
    return None


def _patrimonio_liquido(bpp: DemosPivot, dt: str, sc: float) -> Optional[float]:
    """Patrimônio líquido atribuível à controladora (book equity)."""
    # 1) "patrimônio líquido atribuído à controladora"
    v = bpp.first_match("2", r"patrim.*l.quido.*(atribu|control)", dt)
    if v is not None:
        return v * sc
    # 2) patrimônio líquido (total, consolidado)
    v = bpp.first_match("2", r"patrim.*l.quido", dt)
    if v is not None:
        return v * sc
    # 3) fallback por código (banco=2.08, padrão=2.03)
    for cd in ("2.08", "2.03"):
        v = bpp.pivot.get(cd, {}).get(dt)
        if v is not None:
            return v * sc
    return None


def _campos_financeiros(emp: EmpresaData, dt: str, fonte: str = "dfp") -> Dict[str, Optional[float]]:
    demos = emp.dfp if fonte == "dfp" else emp.itr
    dre = demos.get("DRE", DemosPivot())
    bpa = demos.get("BPA", DemosPivot())
    bpp = demos.get("BPP", DemosPivot())
    sc = dre.escala or bpp.escala or bpa.escala or 1000

    lucro_liq = _lucro_liquido(dre, dt, sc)
    pl = _patrimonio_liquido(bpp, dt, sc)
    receita = dre.pivot.get("3.01", {}).get(dt)            # intermediação / prêmios
    receita = receita * sc if receita is not None else None
    lair = dre.pivot.get("3.05", {}).get(dt)
    lair = lair * sc if lair is not None else None
    ativo = bpa.first_match("1", r"ativo total", dt)
    if ativo is None:
        ativo = bpa.pivot.get("1", {}).get(dt)
    ativo = ativo * sc if ativo is not None else None
    return {"lucro_liq": lucro_liq, "pl": pl, "receita": receita,
            "lair": lair, "ativo_total": ativo}


# ─── Função principal ─────────────────────────────────────────────────────────

def calcular_indicadores_financeira(emp: EmpresaData) -> Dict:
    """Indicadores de financeira no formato consumido pelo pipeline (main_acoes)."""
    resultado: Dict = {
        "ticker": emp.ticker, "nome": emp.nome, "cd_cvm": emp.cd_cvm, "cnpj": emp.cnpj,
        "categ_reg": emp.categ_reg, "setor": emp.setor_gics, "ajuste": emp.ajuste_setor,
        "setor_cvm": getattr(emp, "setor_cvm", ""), "ticker_b3": getattr(emp, "ticker_b3", ""),
        "tp_merc": getattr(emp, "tp_merc", ""), "sit_cvm": getattr(emp, "sit_cvm", "ATIVO"),
        "erro": emp.erro,
        "modelo_valuation": "FCFE",
        "tipo_financeira": classificar_tipo(getattr(emp, "setor_cvm", "") or emp.ajuste_setor),
        "ind": {}, "campos_brutos": {}, "historico_brutos": {}, "historico": {},
    }
    if emp.erro or not emp.anos_dfp:
        return resultado

    dt = emp.ano(0)
    resultado["data_ref"] = dt
    cb = _campos_financeiros(emp, dt, fonte="dfp")
    resultado["campos_brutos"] = {k: v for k, v in cb.items() if v is not None}

    ll, pl = cb.get("lucro_liq"), cb.get("pl")
    receita, ativo = cb.get("receita"), cb.get("ativo_total")
    roe = (ll / pl) if (ll is not None and pl and pl > 0) else None
    resultado["ind"] = {
        "roe": roe * 100 if roe is not None else None,           # em %
        "roic": roe * 100 if roe is not None else None,          # alias p/ reaproveitar pipeline
        "alavancagem": (ativo / pl) if (ativo and pl and pl > 0) else None,
        "margem_liq": (ll / receita * 100) if (ll is not None and receita and receita > 0) else None,
    }

    # Histórico anual (mais antigo → mais recente) p/ CAGR e score A bancário
    hist_ll: List[Optional[float]] = []
    hist_rec: List[Optional[float]] = []
    hist_pl: List[Optional[float]] = []
    for ano in emp.anos_dfp:
        c = _campos_financeiros(emp, ano, fonte="dfp")
        hist_ll.append(c.get("lucro_liq"))
        hist_rec.append(c.get("receita"))
        hist_pl.append(c.get("pl"))
    resultado["historico_brutos"] = {"lucro_liq": hist_ll, "receita": hist_rec, "pl": hist_pl}
    return resultado
