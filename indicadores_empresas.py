# ============================================================================
# AVISO: CÓPIA do projeto de crédito ("1 Renda Fixa/Dash - Credito Privado").
# A fonte de verdade deste módulo é aquele projeto. Melhorias na metodologia
# de crédito NÃO se propagam automaticamente para cá — re-sincronize à mão.
# ============================================================================

"""
Cálculo dos indicadores financeiros para companhias não-financeiras.

Fluxo:
1. Extrai campos brutos dos DemosPivot (BPA, BPP, DRE, DFC_MI)
2. Calcula os 18 indicadores dos 5 pilares
3. Retorna dict com valores atuais + séries históricas (3 anos) para sparklines
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from cvm_client import EmpresaData, DemosPivot

_NEUTRO = 3.0   # score neutro para campo indisponível

# Chaves de fluxo (P&L / DFC — acumulam ao longo do ano → tratamento LTM)
# e de balanço (posição point-in-time). Usadas na montagem LTM trimestre a trimestre.
_FLOW_KEYS = ("receita", "custo_vendas", "lucro_bruto", "ebit", "lucro_liq", "desp_fin",
              "ircs", "ebt", "fco", "fci", "fcf", "var_caixa", "da", "capex",
              "fcf_captacao", "fcf_pagamentos")
_BS_KEYS   = ("ativo_total", "ativo_circ", "caixa", "contas_receber", "estoques",
              "passivo_circ", "fornecedores", "pl", "divida_cp", "divida_lp",
              "intangivel", "goodwill")


# ─── Extração de campos brutos ─────────────────────────────────────────────────

def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return num / den if den and den != 0 else default


def _divida_financeira(demo: DemosPivot, parent: str, divida_pat: str,
                       arrend_pat: str, dt: str) -> float:
    """
    Dívida financeira sob `parent` (2.01 ou 2.02), EXCLUINDO arrendamento (IFRS 16).

    Por que não é só `find_under(debt) - find_under(lease)`:
    no template CVM o passivo de arrendamento operacional ("Arrendamentos a pagar")
    fica em "Outras Obrigações" (2.01.05 / 2.02.02), FORA do galho de dívida
    "Empréstimos e Financiamentos" (2.01.04 / 2.02.01). Subtrair todo arrendamento
    do agregado de dívida zerava a dívida real de varejistas/drogarias com lease
    pesado (ex: RADL3) e subestimava a dívida de empresas onde a separação já
    funcionava (ex: WEGE3, cujo lease vive em Outras Obrigações).

    Solução: somar as contas de dívida (dedup por pai, igual a find_under) e
    subtrair APENAS o arrendamento que esteja DENTRO dessas contas de dívida
    (ex: a sub-conta "Financiamento por Arrendamento" 2.01.04.03 quando ela tem
    saldo), nunca o lease que vive em outro galho do balanço.
    """
    rxd = re.compile(divida_pat, re.IGNORECASE)
    rxa = re.compile(arrend_pat, re.IGNORECASE)

    # 1) Contas de dívida: começam com parent e casam com divida_pat (dedup por pai)
    cands = [
        cd for cd, desc in demo.descricoes.items()
        if (cd == parent or cd.startswith(parent + ".")) and rxd.search(desc)
    ]
    cands.sort(key=lambda c: c.count("."))
    debt_accounts: List[str] = []
    total = 0.0
    for cd in cands:
        if any(cd.startswith(p + ".") for p in debt_accounts):
            continue  # já contado via pai
        v = demo.pivot.get(cd, {}).get(dt)
        if v is not None:
            total += v
            debt_accounts.append(cd)

    # 2) Arrendamento DENTRO das contas de dívida (dedup por pai) → subtrair
    lease_cands = [
        cd for cd, desc in demo.descricoes.items()
        if rxa.search(desc)
        and any(cd == p or cd.startswith(p + ".") for p in debt_accounts)
    ]
    lease_cands.sort(key=lambda c: c.count("."))
    counted_lease: List[str] = []
    lease_in_debt = 0.0
    for cd in lease_cands:
        if any(cd.startswith(p + ".") for p in counted_lease):
            continue
        v = demo.pivot.get(cd, {}).get(dt)
        if v is not None:
            lease_in_debt += v
            counted_lease.append(cd)

    return max(0.0, abs(total) - abs(lease_in_debt))


def _extrair_campos(emp: EmpresaData, dt: str, fonte: str = "dfp") -> Dict[str, Optional[float]]:
    """
    Extrai valores brutos dos demonstrativos para uma data de referência.
    fonte: "dfp" (anual) ou "itr" (trimestral)
    Retorna dict {campo: valor_em_reais} — já aplicada a escala (MIL→R$).
    """
    demos = emp.dfp if fonte == "dfp" else emp.itr
    dre  = demos.get("DRE",    DemosPivot())
    bpa  = demos.get("BPA",    DemosPivot())
    bpp  = demos.get("BPP",    DemosPivot())
    dfc  = demos.get("DFC_MI", DemosPivot())
    dva  = demos.get("DVA",    DemosPivot())

    # Escala (geralmente 1000 = MIL)
    sc = dre.escala or bpa.escala or bpp.escala or dfc.escala or dva.escala or 1000

    def g(demo: DemosPivot, cd: str) -> Optional[float]:
        v = demo.pivot.get(cd, {}).get(dt)
        return v * sc if v is not None else None

    def gu(demo: DemosPivot, parent: str, pat: str) -> float:
        return demo.find_under(parent, pat, dt) * sc

    # ── DRE ───────────────────────────────────────────────────────────────────
    receita      = g(dre, "3.01")
    custo_vendas = g(dre, "3.02")          # CMV/CPV (negativo)
    lucro_bruto  = g(dre, "3.03")          # Resultado Bruto
    ebit         = g(dre, "3.05")
    # Lucro líquido: prefere 3.09 (operações continuadas) sobre 3.11 (total c/ descontinuadas)
    # para evitar contaminação por ganho/perda de venda de subsidiárias
    lucro_liq    = g(dre, "3.09") or g(dre, "3.11")

    # ── EBIT NORMALIZADO (sem impairments e eventos não-recorrentes) ───────────
    # 3.04.03 "Perdas pela Não Recuperabilidade de Ativos" (impairment de goodwill,
    # ativos fixos) é não-cash e tipicamente não-recorrente. Vale teve R$ 25 bi
    # em 2025 — distorce EBIT, EBITDA, Cob.Juros e ROIC quando não excluído.
    # Threshold: só adiciona de volta quando o impairment é material (> 3% receita).
    impairment = g(dre, "3.04.03") or 0.0   # negativo na DRE
    if ebit is not None and receita and impairment and abs(impairment) > 0.03 * receita:
        # Adiciona impairment (negativo) de volta — torna EBIT positivo maior
        # Ex.: EBIT 32 - impairment(-25) → EBIT normalizado 32 - (-25) = 57
        ebit = ebit - impairment
    # Despesas Financeiras — cadeia de fallback (usando `is None`, não `or`,
    # pois 0.0 é falsy e quebraria o fallback para empresas que capitalizam juros):
    # 1) 3.06.02.01 = juros puros (evita contaminação cambial — ex: Petrobras)
    # 2) 3.06.02    = despesas financeiras brutas (a maioria das empresas)
    # 3) 3.06 − 3.06.01 = resultado fin. líquido deduzido de receitas financeiras
    #    (fallback para utilities que capitalizam juros e deixam 3.06.02 = 0, ex: SBSP3)
    _d_juros_puros = g(dre, "3.06.02.01")  # tag explícita de juros (sem lease)
    _used_aggregated = False
    if _d_juros_puros is None or _d_juros_puros == 0.0:
        _d = g(dre, "3.06.02")               # despesa financeira bruta (PODE incluir lease/JCP/cambial)
        _used_aggregated = True
    else:
        _d = _d_juros_puros

    if _d is None or _d == 0.0:
        _rf = g(dre, "3.06")
        if _rf is not None:
            _d = _rf - (g(dre, "3.06.01") or 0.0)
            _used_aggregated = True
    desp_fin = _d

    # ── LIMPEZA da Despesa Financeira (apenas quando agregada) ─────────────────
    # 3.06.02 (despesa financeira bruta) contamina Cob.Juros com 3 não-juros:
    #   (a) JCP — Juros sobre Capital Próprio = dividendos disfarçados (Vale, Petrobras)
    #   (b) Variação Cambial — não é juros, é resultado de exposição FX (Suzano, Vale)
    #   (c) Lease interest (IFRS 16) — já tratado acima via arrend_total × 10%
    # Subtraímos JCP e Cambial via busca por padrão nas sub-contas 3.06.02.x
    if _used_aggregated and desp_fin is not None:
        # JCP: padrão "Juros sobre Capital Próprio" ou "JCP"
        jcp = gu(dre, "3.06.02", r"juros?\s+sobre\s+capital\s+pr[óo]prio|jcp\b")
        # Variação cambial / FX losses
        cambial = gu(dre, "3.06.02", r"varia[çc][ãa]o\s+cambial|perdas?\s+cambiais|c[aâ]mbio")
        # Ambos são valores negativos (despesas). Subtrai-los de desp_fin (também negativo)
        # = soma de magnitudes positivas → desp_fin fica menos negativo
        desp_fin = desp_fin - (jcp or 0.0) - (cambial or 0.0)
        # Garante que desp_fin não vire receita (mínimo zero)
        desp_fin = min(desp_fin, 0.0)
    ircs         = g(dre, "3.08")             # Imposto de Renda e CS (negativo)
    ebt          = g(dre, "3.07")             # Resultado antes dos tributos

    # ── DFC_MI ────────────────────────────────────────────────────────────────
    fco          = g(dfc, "6.01")             # Caixa Líquido Operacional
    fci          = g(dfc, "6.02")             # Caixa Líquido de Investimento
    fcf          = g(dfc, "6.03")             # Caixa Líquido de Financiamento
    var_caixa    = g(dfc, "6.05")             # Aumento/Redução de Caixa total

    # Captação vs Pagamentos: filhos diretos de 6.03 separados por sinal
    # Positivos = inflows (emissão de dívida, captações)
    # Negativos = outflows (amortizações, dividendos)
    _fcf_pos_raw, _fcf_neg_raw = dfc.get_pos_neg_children("6.03", dt)
    fcf_captacao   = _fcf_pos_raw * sc if _fcf_pos_raw else None
    fcf_pagamentos = _fcf_neg_raw * sc if _fcf_neg_raw else None

    # D&A: prioridade 1 — sub-contas de 6.01 no DFC método indireto (add-back explícito)
    da = gu(dfc, "6.01", r"deprecia|amortiza|exaust")
    # D&A pode estar em valores negativos (ajuste de saída) ou positivos (add-back)
    # No DFC método indireto, D&A é sempre add-back (positivo)
    da = abs(da) if da else 0.0

    # D&A: prioridade 2 — DVA linha 7.04.01 "Depreciação, Amortização e Exaustão"
    # Fallback necessário para empresas que adotam DFC Método Direto (sem add-back):
    # ALLPARK e outros não têm sub-contas em 6.01 → da ficaria zero sem este fallback.
    # A DVA é obrigatória pela CVM e sempre contém o D&A total (PP&E + ROU + intangível).
    if da == 0.0:
        da_dva = g(dva, "7.04.01")
        if da_dva:
            da = abs(da_dva)

    # Capex: sub-contas de 6.02 — padrão ÚNICO evita dupla contagem quando uma
    # conta menciona "imobilizado e intangível" na mesma descrição (ex: VIVT3, RADL3).
    # "intangíve" (sem "l/is") captura singular ("intangível") E plural ("intangíveis").
    # "ativo[s]? de contrato" captura ativos de concessão de utilities (ex: SBSP3).
    _PAT_CAPEX = (
        r"imobilizado|ativo fixo|propriedade"
        r"|intangíve|intangivel|software"
        r"|ativo[s]? de contrato"
    )
    capex = abs(gu(dfc, "6.02", _PAT_CAPEX))

    # Capex fallback: DFC Método Direto não tem sub-contas em 6.02.
    # Quando capex=0 por ausência de sub-contas, usa |FCI| como proxy conservador.
    # Cap em 40% da receita para não inflar com aquisições M&A extraordinárias.
    # Aplica apenas quando da > 0 (confirma empresa com ativos depreciáveis → capex real).
    _fci_raw = g(dfc, "6.02")
    if capex == 0.0 and da > 0.0 and _fci_raw and _fci_raw < 0:
        _max_capex = (receita or 0.0) * 0.40
        capex = min(abs(_fci_raw), _max_capex) if _max_capex > 0 else abs(_fci_raw)

    # ── BPA ───────────────────────────────────────────────────────────────────
    ativo_total = g(bpa, "1")
    ativo_circ  = g(bpa, "1.01")

    # Caixa para análise de crédito: SOMA de Caixa+Equivalentes (1.01.01) com
    # Aplicações Financeiras curto-prazo (1.01.02). Ambas são liquidez imediata
    # disponível para honrar dívida, e várias empresas (Nissei, varejistas, etc.)
    # alocam o grosso da liquidez em CDBs/títulos públicos curtos, deixando 1.01.01
    # com saldo de operação trivial.
    caixa_eq = g(bpa, "1.01.01") or 0.0
    if not caixa_eq:
        v = bpa.first_match("1.01", r"caixa|disponib", dt)
        caixa_eq = v * sc if v is not None else 0.0

    aplic_fin = g(bpa, "1.01.02") or 0.0
    if not aplic_fin:
        # fallback: busca por sub-contas de 1.01 com "aplicações financeiras"
        aplic_fin = gu(bpa, "1.01", r"aplica\w*\s*financ|t.tulos\s+e\s+valores")

    caixa_total = caixa_eq + aplic_fin
    caixa = caixa_total if caixa_total > 0 else None

    # ── Capital de giro operacional (ciclo de conversão de caixa) ──────────────
    # Contas a Receber (1.01.03), Estoques (1.01.04), Fornecedores (2.01.02).
    # Usados para dias de recebíveis/estoque/pagáveis — detectores de qualidade
    # de lucro (channel stuffing, obsolescência, "esticar fornecedores" = Americanas).
    contas_receber = g(bpa, "1.01.03") or 0.0
    if not contas_receber:
        contas_receber = gu(bpa, "1.01", r"contas?\s+a\s+receber|clientes")
    estoques = g(bpa, "1.01.04") or 0.0
    if not estoques:
        estoques = gu(bpa, "1.01", r"estoque")

    # Intangível total (1.02.04 no padrão CVM PJ) — inclui ÁGIO/goodwill + software,
    # marcas, contratos de cliente. Usado para ajustar ROIC de empresas com M&A
    # pesado (TOTVS, Magalu, Hapvida, Stone, etc.)
    # Fallback: se 1.02.04 vazio, busca por descrição em sub-contas de 1.02
    intangivel = g(bpa, "1.02.04") or 0.0
    if not intangivel:
        intangivel = gu(bpa, "1.02", r"intang|ágio|agio|goodwill")

    # Goodwill separado (1.02.04.02 padrão CVM; fallback por descrição).
    # Usado para ajuste de ROIC: só o goodwill (prêmio pago em M&A) infla artificialmente
    # o capital investido sem gerar retorno operacional. Direitos de concessão, marcas
    # e contratos de clientes NÃO são excluídos — são ativos operacionais reais.
    goodwill = g(bpa, "1.02.04.02") or 0.0
    if not goodwill:
        goodwill = gu(bpa, "1.02.04", r"^ágio\b|^agio\b|goodwill")

    # ── BPP ───────────────────────────────────────────────────────────────────
    passivo_circ = g(bpp, "2.01")
    # Fornecedores (2.01.02) — para dias de pagáveis no ciclo de caixa
    fornecedores = g(bpp, "2.01.02") or 0.0
    if not fornecedores:
        fornecedores = gu(bpp, "2.01", r"fornecedor")
    # PL: tenta 2.03, depois 2.07, depois busca por "patrimônio"
    pl = g(bpp, "2.03") or g(bpp, "2.07")
    if pl is None:
        v = bpp.first_match("2", r"patrim.nio l.quido consolidado|patrimônio líquido$", dt)
        pl = v * sc if v is not None else None

    # Dívida CP/LP: sub-contas 2.01/2.02 com empréstimo/financiamento/debênture.
    # IMPORTANTE: "arrendamento" (IFRS 16 lease liability) NÃO é incluído na dívida
    # financeira. Lease IS um compromisso real, mas inclui-lo distorce ratios de
    # leverage vs peers — agências usam "Dívida Financeira" excl. leases.
    # Drogarias, varejistas, hotéis, fast-food, transporte ficavam mal classificados.
    #
    # NOTA TÉCNICA: a sub-conta "Financiamento por Arrendamento" (2.0x.04.03)
    # vive DENTRO do agregado de dívida e PODE estar embutida no valor do pai —
    # essa parte é subtraída. Já o passivo de arrendamento OPERACIONAL
    # ("Arrendamentos a pagar") fica em "Outras Obrigações" (2.01.05 / 2.02.02),
    # FORA do galho de dívida: NÃO deve ser subtraído da dívida (ele nunca foi
    # somado nela). Subtraí-lo zerava a dívida da RADL3 e subestimava a da WEGE3.
    # Por isso a subtração de lease é escopada ao galho de dívida — ver
    # _divida_financeira(). O arrend_total (lease completo) segue sendo usado só
    # para a estimativa de juros de arrendamento no DRE, abaixo.
    _divida_pat  = r"empréstimo|emprestimo|financiamento|debênture|debenture"
    _arrend_pat  = r"arrendamento|leasing"

    arrend_cp = abs(gu(bpp, "2.01", _arrend_pat))
    arrend_lp = abs(gu(bpp, "2.02", _arrend_pat))
    arrend_total = arrend_cp + arrend_lp

    divida_cp = _divida_financeira(bpp, "2.01", _divida_pat, _arrend_pat, dt) * sc
    divida_lp = _divida_financeira(bpp, "2.02", _divida_pat, _arrend_pat, dt) * sc

    # IFRS 16 — Estimativa de juros sobre arrendamento (lease interest).
    # Quando o DRE NÃO discrimina juros bancários (cai no agregado 3.06.02), a
    # despesa financeira inclui juros sobre arrendamento. Para credit analysis
    # purposes (cob.juros, DSCR), subtraímos a estimativa.
    # Taxa de 10% a.a. é a média típica do desconto IFRS 16 no BR (faixa 8-12%).
    # Quando há breakdown explícito (3.06.02.01), assumimos que já exclui leases.
    if _used_aggregated and arrend_total > 0 and desp_fin is not None:
        lease_interest_est = arrend_total * 0.10
        # desp_fin é negativo (custo); somar estimativa positiva o torna menos negativo
        desp_fin = desp_fin + lease_interest_est
        # Não deixar virar receita (mínimo zero)
        desp_fin = min(desp_fin, 0.0)

    return {
        "receita":      receita,
        "custo_vendas": custo_vendas,
        "lucro_bruto": lucro_bruto,
        "ebit":         ebit,
        "lucro_liq":    lucro_liq,
        "desp_fin":     desp_fin,
        "ircs":         ircs,
        "ebt":          ebt,
        "fco":              fco,
        "fci":              fci,
        "fcf":              fcf,
        "fcf_captacao":     fcf_captacao,
        "fcf_pagamentos":   fcf_pagamentos,
        "var_caixa":        var_caixa,
        "da":               da,
        "capex":            capex,
        "ativo_total":  ativo_total,
        "ativo_circ":   ativo_circ,
        "caixa":        caixa,
        "contas_receber": contas_receber,
        "estoques":       estoques,
        "intangivel":   intangivel,
        "goodwill":     goodwill,
        "passivo_circ": passivo_circ,
        "fornecedores":   fornecedores,
        "pl":           pl,
        "divida_cp":    divida_cp,
        "divida_lp":    divida_lp,
    }


def _calc_indicadores_de_campos(campos: Dict[str, Optional[float]], setor: str = "default") -> Dict[str, Optional[float]]:
    """Calcula os 18 indicadores a partir dos campos brutos."""
    c = campos
    estimados: List[str] = []

    def v(key: str) -> Optional[float]:
        return c.get(key)

    receita    = v("receita")    or 0.0
    ebit       = v("ebit")       or 0.0
    lucro_liq  = v("lucro_liq")  or 0.0
    desp_fin   = v("desp_fin")   or 0.0   # negativo na DRE
    fco        = v("fco")        or 0.0
    da         = v("da")         or 0.0
    capex      = v("capex")      or 0.0
    ativo_circ = v("ativo_circ") or 0.0
    caixa      = v("caixa")      or 0.0
    intangivel = v("intangivel") or 0.0
    goodwill   = v("goodwill")   or 0.0
    passivo_circ = v("passivo_circ") or 0.0
    pl         = v("pl")         or 0.0
    divida_cp  = v("divida_cp")  or 0.0
    divida_lp  = v("divida_lp")  or 0.0
    ativo_total = v("ativo_total") or 0.0

    ebitda_contabil = ebit + da          # EBITDA contábil puro (EBIT + D&A da DRE/DVA)
    ebitda     = ebitda_contabil
    divida_bruta = divida_cp + divida_lp
    divida_liq = divida_bruta - caixa

    # ── Concessionárias (IFRIC 12) — proxy de EBITDA para ratios de dívida ───
    # Quando intangível > 60% do ativo, a empresa tem receita+custo de construção
    # contabilizados via IFRIC 12 que distorcem EBIT da DRE.
    # Para ratios de capacidade de pagamento (dl_ebitda, cob_juros, dscr), usamos:
    #   EBITDA_proxy = FCO + |desp_fin|  (FCO pré-financeiro ≈ EBITDA operacional)
    # IMPORTANTE: o proxy NÃO é usado para margem_ebitda (display) — ficaria inflado
    # porque soma FCO (já pós-juros no BR GAAP) com desp_fin (duplica o add-back).
    # margem_ebitda usa sempre o EBITDA contábil puro.
    is_concessao = (ativo_total > 0 and intangivel / ativo_total > 0.60)
    usou_proxy_concessao = False
    ebitda_para_divida = ebitda_contabil   # EBITDA para dl_ebitda / cob_juros
    if is_concessao and fco > 0 and (ebit < 0 or fco > ebit * 1.5):
        # FCO + |desp_fin| ≈ EBITDA operacional pré-IFRIC 12 (juros recolocados)
        ebitda_proxy = fco + abs(desp_fin)
        # Override apenas se a substituição melhora a representação
        if ebitda_proxy > ebitda_contabil:
            ebitda_para_divida = ebitda_proxy
            # ebitda (global) permanece = ebitda_contabil para margens/display
            # Reconstrói EBIT proxy só para cob_juros (EBIT / |desp_fin|)
            ebit = ebitda_proxy - da
            usou_proxy_concessao = True

    # ── Concessão IFRIC 12: neutraliza lucro_bruto no display ─────────────────
    # CPV inclui amortização do ativo de concessão (enorme) + custo de construção
    # (pass-through = zero impacto). Resultado bruto é estruturalmente negativo
    # e não tem significado econômico real para essas empresas.
    if is_concessao:
        campos["lucro_bruto"] = None

    # Alíquota de IR: estimada pelo histórico ou 34% (padrão Brasil)
    # Em casos onde EBIT foi recomputado via proxy (concessão IFRIC 12), o EBT/IRCS
    # originais refletem situação distorcida — força tax_rate padrão 34%.
    ebt = v("ebt") or 0.0
    ircs = v("ircs") or 0.0
    if usou_proxy_concessao or ebt == 0:
        tax_rate = 0.34
    else:
        tax_rate = max(0.0, min(0.50, -ircs / ebt))  # nunca negativa nem > 50%
    nopat = ebit * (1 - tax_rate)

    capital_investido = pl + divida_bruta - caixa

    # Capital investido AJUSTADO para ROIC: goodwill de M&A infla o capital investido
    # sem gerar retorno operacional (prêmio pago por aquisições). Empresas como TOTVS,
    # Magalu, Hapvida, Stone, Locaweb absorvem grande goodwill no balanço.
    #
    # CORREÇÃO vs versão anterior: usamos GOODWILL (1.02.04.02), não intangível total.
    # Direitos de concessão, marcas e contratos de clientes NÃO são excluídos — são
    # ativos operacionais reais que geram o EBIT considerado. Usar intangível total
    # distorcia ALLPARK (parking rights = real operational), Norte Energia (concessão), etc.
    #
    # Threshold: goodwill > 15% do ativo (sinal de M&A relevante).
    # Removemos 80% do goodwill (premium M&A ≈ 70-90% do valor do ágio).
    # Floor em 40% do capital investido original para evitar over-correção.
    capital_investido_roic = capital_investido
    if ativo_total > 0 and goodwill > 0 and goodwill / ativo_total > 0.15:
        capital_investido_roic = max(
            capital_investido - goodwill * 0.80,
            capital_investido * 0.40,
        )

    ind: Dict[str, Optional[float]] = {}

    # ── Pilar 1 — Alavancagem ─────────────────────────────────────────────────
    # dl_ebitda usa ebitda_para_divida: proxy FCO-based para concessão IFRIC 12,
    # ebitda_contábil para todos os demais.
    ind["dl_ebitda"] = _safe_div(divida_liq, ebitda_para_divida) if ebitda_para_divida > 0 else None

    # BUG FIX: se PL < 0 e DL > 0, o quociente DL/PL é negativo — a regra (None, 0, 5)
    # interpretaria isso como "net cash" e daria score 5. É o oposto: empresa insolvente
    # por patrimônio com dívida positiva → máxima penalidade (99x dispara score 1 + RED+1)
    if pl and pl < 0 and divida_liq > 0:
        ind["dl_pl"] = 99.0
    else:
        ind["dl_pl"] = _safe_div(divida_liq, pl) if pl else None
    ind["cob_juros"] = _safe_div(ebit, abs(desp_fin)) if desp_fin else None
    # Cobertura (EBITDA − Capex) / Juros — métrica-rei de Gatto: o capex de
    # manutenção é tão obrigatório quanto os juros. < 1 = não gera caixa nem
    # para os juros após capex (sinal de distress). Mais conservadora que cob_juros.
    ind["cob_ebitda_capex"] = _safe_div(ebitda_para_divida - capex, abs(desp_fin)) if desp_fin else None
    ind["pct_divida_cp"] = (
        _safe_div(divida_cp, divida_bruta) * 100 if divida_bruta > 0 else 0.0
    )

    # ── Pilar 2 — Qualidade de Caixa ──────────────────────────────────────────
    ind["margem_ebitda"]  = _safe_div(ebitda, receita) * 100 if receita else None

    # Conv. Caixa = FCO / EBITDA. Confiabilidade requer:
    #   1) EBITDA materialmente significativo (margem > 5% receita) — senão o quociente
    #      explode (10% × FCO / 1% × receita = 1000% sem sentido).
    #   2) Capping em [-50%, 200%] para evitar outliers únicos distorcerem o score.
    # A média histórica de 3 anos será aplicada depois em extrair_indicadores().
    mg_ebitda_pct = (ebitda / receita * 100) if receita and ebitda else 0
    if ebitda > 0 and abs(mg_ebitda_pct) >= 5.0:
        _cv = _safe_div(fco, ebitda) * 100
        # Cap razoável: > 200% é WC release excepcional, < -50% é distress, ambos pontuais
        ind["conv_caixa"] = max(-50.0, min(200.0, _cv))
    else:
        ind["conv_caixa"] = None    # margem fina demais para ser confiável
    ind["roic"]           = _safe_div(nopat, capital_investido_roic) * 100 if capital_investido_roic > 0 else None
    ind["capex_receita"]  = _safe_div(capex, receita) * 100 if receita else None

    # Custo da Dívida / ROIC — indicador da planilha Zelen de crédito privado
    # Mede se a empresa ganha mais do que paga pela dívida (spread de retorno).
    # Calculado como: custo_médio_dívida_implícito / ROIC
    #   custo_médio = |DesDFinanceiras| / Dívida Bruta  (proxy; diferente de taxa contratual)
    #   ROIC já calculado acima como nopat/capital_investido
    # Interpretação: < 1 = empresa gera mais do que paga (bom); > 1 = destrói valor via dívida
    _roic_raw = _safe_div(nopat, capital_investido_roic) if capital_investido_roic > 0 else None
    if divida_bruta > 0 and desp_fin:
        _custo_div = abs(desp_fin) / divida_bruta
        if _roic_raw is not None and _roic_raw > 0:
            ind["custo_divida_roic"] = _custo_div / _roic_raw
        elif _roic_raw is not None and _roic_raw <= 0:
            # ROIC negativo + dívida = destruição máxima de valor
            ind["custo_divida_roic"] = 5.0
        else:
            ind["custo_divida_roic"] = None
    else:
        ind["custo_divida_roic"] = None  # sem dívida ou sem desp_fin — não aplicável

    # ── Ajuste real_estate: FCO-based SÓ para propriedade de renda (IAS 40) ──────
    # O setor "Imobiliario" mistura DOIS modelos de negócio opostos:
    #   (a) Propriedade de renda / IAS 40 (shoppings, galpões logísticos): reconhecem
    #       a variação do valor justo dos imóveis no resultado → EBITDA inflado sem
    #       gerar caixa (ex.: LOG CP 257% de margem, Multiplan, JHSF). Aqui o FCO/FFO
    #       é a métrica correta para servir dívida.
    #   (b) Incorporadoras / homebuilders (receita por PoC): o EBITDA é lucro operacional
    #       REAL. O FCO é naturalmente NEGATIVO no ciclo de obra (gasta caixa construindo
    #       estoque, recebe na entrega) — penalizá-las por FCO<0 é falso sinal de distress.
    #       Ex.: MDNE3 (DL/EBITDA real 0,16x) e CYRE3 (2,0x) levavam dl_ebitda=99 à toa.
    #
    # Discriminador (validado em jun/2026): margem EBITDA contábil. Propriedade de renda
    # IAS 40 fica > 45% (vão claro: incorporadoras 8-35%, renda 59%+). Só o grupo (a)
    # recebe o override FCO-based; (b) usa o EBITDA-based padrão (FCO<0 não penaliza),
    # exceto quando o EBITDA contábil é negativo (distress real → penalidade máxima).
    if setor == "real_estate":
        margem_contabil = (ebitda_contabil / receita) if receita else 0.0
        fair_value_property = margem_contabil > 0.45
        if fair_value_property:
            if fco and fco > 0:
                ind["margem_ebitda"]    = _safe_div(fco, receita) * 100 if receita else ind.get("margem_ebitda")
                ind["dl_ebitda"]        = _safe_div(divida_liq, fco)
                if desp_fin:
                    ind["cob_juros"]        = _safe_div(fco, abs(desp_fin))
                    ind["cob_ebitda_capex"] = _safe_div(fco - capex, abs(desp_fin))
            else:
                # renda sem FCO positivo: sem geração de caixa para servir a dívida
                ind["margem_ebitda"]    = _safe_div(fco, receita) * 100 if receita and fco is not None else 0.0
                ind["dl_ebitda"]        = 99.0
                ind["cob_juros"]        = 0.0
                ind["cob_ebitda_capex"] = -99.0
        elif ebitda_contabil <= 0:
            # incorporadora com EBITDA negativo = distress real → penalidade máxima
            ind["dl_ebitda"]        = 99.0
            ind["cob_ebitda_capex"] = -99.0
        # senão: incorporadora saudável → mantém indicadores EBITDA-based padrão

    # ── Pilar 3 — Liquidez ────────────────────────────────────────────────────
    # Refundado (Gatto): o índice de liquidez corrente é de-priorizado em favor
    # de geração de Free Cash Flow. FCF = FCO − Capex (proxy padrão FCFF).
    ind["liq_corrente"]   = _safe_div(ativo_circ, passivo_circ) if passivo_circ else None
    ind["caixa_div_cp"]   = _safe_div(caixa, divida_cp) if divida_cp > 0 else (5.0 if caixa > 0 else 0.0)
    # DSCR proxy: FCO / Despesas Financeiras (melhor proxy disponível)
    ind["dscr"]           = _safe_div(fco, abs(desp_fin)) if desp_fin else None
    # Margem de Free Cash Flow = (FCO − Capex) / Receita — geração de caixa livre
    fcf = fco - capex
    ind["fcf_margem"]     = _safe_div(fcf, receita) * 100 if receita else None

    # ── Qualidade de lucro — Ciclo de Conversão de Caixa ──────────────────────
    # Dias de estoque + recebíveis − pagáveis. Detector anti-Americanas:
    #   · pagáveis esticando (dias_pag ↑) = pressão de fornecedor / risco sacado
    #   · recebíveis inchando (dias_receb ↑) = channel stuffing / receita fraca
    #   · estoque inchando (dias_estoque ↑) = obsolescência / write-down futuro
    # Calculado point-in-time; tendência (YoY) é avaliada no rating_engine.
    custo_vendas = abs(v("custo_vendas") or 0.0)
    contas_receber = v("contas_receber") or 0.0
    estoques       = v("estoques") or 0.0
    fornecedores   = v("fornecedores") or 0.0
    ind["dias_receb"]   = _safe_div(contas_receber, receita) * 365 if receita else None
    ind["dias_estoque"] = _safe_div(estoques, custo_vendas) * 365 if custo_vendas else None
    ind["dias_pag"]     = _safe_div(fornecedores, custo_vendas) * 365 if custo_vendas else None
    if ind["dias_receb"] is not None or ind["dias_estoque"] is not None:
        ind["ciclo_caixa"] = ((ind.get("dias_estoque") or 0.0)
                              + (ind.get("dias_receb") or 0.0)
                              - (ind.get("dias_pag") or 0.0))
    else:
        ind["ciclo_caixa"] = None

    # ── Pilar 4 — Rentabilidade ───────────────────────────────────────────────
    ind["roe"]            = _safe_div(lucro_liq, pl) * 100 if pl else None
    ind["margem_liq"]     = _safe_div(lucro_liq, receita) * 100 if receita else None
    # crescimento_receita requer dois períodos — calculado em calcular_indicadores()
    ind["cresc_receita"]  = None   # preenchido externamente

    # ── Pilar 5 — Governança (valores diretos 1-5) ───────────────────────────
    # Placeholders; preenchidos em calcular_indicadores() (e nos pontos da série
    # LTM) a partir do cadastro CVM: controle acionário, situação e transparência.
    ind["gov_controle"] = None
    ind["gov_situacao"] = None
    ind["gov_transparencia"] = None

    return ind


# ─── Pilar 5 — Governança ─────────────────────────────────────────────────────
#
# Redesenhado para ser PREDITIVO de defaults, não decorativo.
# Os três fatores anteriores (listagem B3, controle MVP hardcoded, free float
# fixo em 3.0) aplainavam o pilar: 590/600 empresas recebiam score idêntico.
#
# Três novos indicadores — todos disponíveis para 100% das empresas via CVM:
#
# 1. gov_auditoria  — Qualidade e independência do auditor externo
#    Americanas, OAS, Oi: auditores de qualidade questionável ou troca frequente
#    Proxy credível de qualidade informacional disponível para todas as empresas.
#
# 2. gov_situacao   — Situação operacional/legal declarada à CVM
#    Recuperação Judicial é o sinal mais óbvio de default iminente.
#    Monitoramento especial = risco elevado.
#
# 3. gov_transparencia — Regime de divulgação obrigatória
#    Categoria A + listada em bolsa = maior exigência de disclosure.
#    Categoria B = só dívida, menos transparência, maior assimetria informacional.

import re as _re
import unicodedata as _uni

def _norm_str(s: str) -> str:
    s = _uni.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.upper()


def _score_controle(controle_acionario: str) -> float:
    """
    Score 1-5 baseado na estrutura de controle acionário declarada à CVM.

    Mede o risco de extração de valor pelos controladores em detrimento de credores
    ("tunneling risk") — principal fator governance-driven de default no Brasil.

    Evidência empírica:
      PRIVADO HOLDING: Americanas (família Batista, R$25B), Odebrecht, OAS, Agrogalaxy
      GOVERNAMENTAL:   Petrobras — default não ocorreu apesar de escândalos (suporte implícito)
      ESTRANGEIRO:     Subsidiárias de multinacionais — raramente entram em default sem
                       que a matriz os abandone explicitamente

    5 → ESTRANGEIRO HOLDING  suporte implícito da matriz + padrões internacionais de gov.
    4 → ESTRANGEIRO          controle estrangeiro direto: alinhamento com matriz
    3.5 → GOVERNAMENTAL      estado não deixa defaultar (risco político ≠ risco crédito)
    3 → PRIVADO              acionistas dispersos, pressão de mercado como disciplinador
    2.5 → COOPERATIVA        governança por membros, risco de má alocação de capital
    2 → PRIVADO HOLDING      holding privada concentrada: histórico dos maiores defaults BR
    1.5 → desconhecido       sem informação → conservador
    """
    n = _norm_str(controle_acionario)

    if "ESTRANGEIRO" in n and "HOLDING" in n:
        return 5.0
    if "ESTRANGEIRO" in n:
        return 4.0
    # GOVERNAMENTAL e ESTATAL são sinônimos no CVM — estado não deixa defaultar
    if "GOVERNAMENTAL" in n or "ESTATAL" in n:
        return 3.5
    if "COOPERATIVA" in n:
        return 2.5
    if "PRIVADO" in n and "HOLDING" not in n:
        return 3.0
    if "PRIVADO" in n:          # PRIVADO HOLDING — maior tunneling risk
        return 2.0

    return 1.5   # desconhecido ou não declarado


def _score_situacao(sit_emissor: str, sit_cvm: str) -> float:
    """
    Score 1-5 baseado na situação operacional/legal declarada à CVM.

    Recuperação Judicial = default técnico ou iminente → score 1 + RED flag
    Outros status negativos → score 2
    Fase operacional normal → score 4-5

    sit_emissor: "FASE OPERACIONAL", "EM RECUPERAÇÃO JUDICIAL",
                 "EM LIQUIDAÇÃO JUDICIAL", "MONITORAMENTO ESPECIAL", etc.
    sit_cvm:     "ATIVO", "CANCELADA", "SUSPENSO", etc.
    """
    se = _norm_str(sit_emissor)
    sc = _norm_str(sit_cvm)

    # Situações de colapso/insolvência declarada
    if any(k in se for k in ("RECUPERACAO JUDICIAL", "LIQUIDACAO JUDICIAL",
                              "LIQUIDACAO EXTRAJUDICIAL", "FALENCIA", "INTERVENCAO")):
        return 1.0   # RED flag disparado separadamente no rating engine

    # Cancelada ou suspensa pela CVM
    if "CANCELADA" in sc or "SUSPENSA" in sc:
        # Cancelamento voluntário com empresa ainda operacional (quitou obrigações
        # e se descadastrou) ≠ insolvência. Aplica haircut informacional mas não RJ.
        if "FASE OPERACIONAL" in se:
            return 2.5   # menos info, não insolvência
        return 1.0

    # Monitoramento especial = CVM identificou problema
    if "MONITORAMENTO" in se:
        return 2.0

    # Pré-operacional: sem histórico = risco desconhecido elevado
    if "PRE" in se and "OPERACIONAL" in se:
        return 2.0

    # Paralisada
    if "PARALISADA" in se:
        return 2.0

    # Fase operacional normal = bom sinal (empresa cumprindo obrigações)
    if "OPERACIONAL" in se:
        return 4.0

    # Sem informação = incerteza → neutro conservador
    return 3.0


def _score_transparencia(categ_reg: str, tp_merc: str) -> float:
    """
    Score 1-5 baseado no regime de divulgação obrigatória (CVM + B3).

    Categoria A + Bolsa  → 4.5  maior exigência: ITR trimestral, DFPI, assembleia etc.
    Categoria A + outros → 3.5  exigência CVM mas sem pressão de mercado acionário
    Categoria B          → 2.0  regime mais leve: só DFP anual, sem ITR
    Outros               → 1.5  regime mínimo ou desconhecido
    """
    cr = (categ_reg or "").strip()
    tm = (tp_merc or "").strip().upper()

    if cr == "Categoria A":
        if tm == "BOLSA":
            return 4.5
        if "BALC" in tm:
            return 4.0
        return 3.5   # Cat A sem mercado declarado

    if cr == "Categoria B":
        return 2.0

    return 1.5


def _serie_ltm_campos(emp: EmpresaData) -> List[Dict]:
    """
    Monta a série temporal de campos brutos (anuais + LTM trimestre a trimestre)
    para reconstruir a evolução do score da companhia.

    Para cada exercício anual (DFP) → ponto direto (já é 12 meses).
    Para cada trimestre (ITR, exceto dez) → ponto LTM:
        LTM(T) = FY(ano−1) + YTD(T) − YTD(mesmo trimestre do ano anterior)
      · Fluxo (P&L/DFC): soma rolante de 12 meses
      · Balanço:         posição point-in-time do próprio trimestre

    Só gera o ponto LTM quando os três insumos existem (FY base, YTD atual,
    YTD do ano anterior) — caso contrário pula o trimestre.

    Returns: lista ordenada por data de [{data_ref, label, ltm, campos, cresc}].
    """
    from datetime import date as _date

    anos_dfp = list(emp.anos_dfp or [])
    tris     = list(emp.trimestres_itr or [])
    dfp_set  = set(anos_dfp)
    tris_set = set(tris)
    pts: List[Dict] = []

    # ── Pontos anuais (DFP) ───────────────────────────────────────────────────
    for dt in anos_dfp:
        campos = _extrair_campos(emp, dt, fonte="dfp")
        cresc = None
        py = f"{int(dt[:4]) - 1}-12-31"
        if py in dfp_set:
            c_prev = _extrair_campos(emp, py, fonte="dfp")
            rp, rc = c_prev.get("receita"), campos.get("receita")
            if rp and rc is not None:
                cresc = (rc - rp) / abs(rp) * 100
        pts.append({"data_ref": dt, "ltm": False, "campos": campos, "cresc": cresc})

    # ── Pontos LTM trimestrais (ITR) ──────────────────────────────────────────
    for dt_q in tris:
        try:
            d = _date.fromisoformat(dt_q)
        except ValueError:
            continue
        if d.month == 12:
            continue   # dezembro = exercício anual, já coberto pelo DFP

        fy_base = f"{d.year - 1}-12-31"
        if fy_base not in dfp_set:
            continue
        try:
            dt_py = _date(d.year - 1, d.month, d.day).isoformat()
        except ValueError:
            dt_py = _date(d.year - 1, d.month, 28).isoformat()
        if dt_py not in tris_set:
            continue

        base = _extrair_campos(emp, fy_base, fonte="dfp")
        cur  = _extrair_campos(emp, dt_q,    fonte="itr")
        prev = _extrair_campos(emp, dt_py,   fonte="itr")

        campos = dict(base)
        ltm_ok = False
        for k in _FLOW_KEYS:
            if base.get(k) is not None and cur.get(k) is not None and prev.get(k) is not None:
                campos[k] = base[k] + cur[k] - prev[k]
                ltm_ok = True
        for k in _BS_KEYS:
            if cur.get(k) is not None:
                campos[k] = cur[k]

        if not ltm_ok and not any(cur.get(k) for k in _BS_KEYS):
            continue

        # Crescimento de receita YoY do acumulado (YTD atual vs YTD ano anterior)
        cresc = None
        rp, rc = prev.get("receita"), cur.get("receita")
        if rp and rc is not None:
            cresc = (rc - rp) / abs(rp) * 100

        pts.append({"data_ref": dt_q, "ltm": True, "campos": campos, "cresc": cresc})

    pts.sort(key=lambda p: p["data_ref"])
    return pts


def calcular_indicadores(emp: EmpresaData) -> Dict:
    """
    Calcula todos os indicadores para a empresa, usando o ano DFP mais recente
    como referência e construindo séries históricas para sparklines.

    Returns dict com:
        - Campos brutos do ano de referência
        - Indicadores calculados
        - Histórico {indicador: [val_t-2, val_t-1, val_t]} para sparklines
        - Flags e metadados
    """
    resultado: Dict = {
        "ticker":     emp.ticker,
        "nome":       emp.nome,
        "cd_cvm":     emp.cd_cvm,
        "cnpj":       emp.cnpj,
        "categ_reg":  emp.categ_reg,
        "setor":      emp.setor_gics,
        "ajuste":     emp.ajuste_setor,
        "ticker_b3":  getattr(emp, "ticker_b3", ""),
        "tp_merc":    getattr(emp, "tp_merc", ""),
        "sit_cvm":    getattr(emp, "sit_cvm", "ATIVO"),
        # Metadados para descrições ricas
        "denom_social":       getattr(emp, "denom_social", ""),
        "denom_comerc":       getattr(emp, "denom_comerc", ""),
        "setor_cvm":          getattr(emp, "setor_cvm", ""),
        "controle_acionario": getattr(emp, "controle_acionario", ""),
        "mun":                getattr(emp, "mun", ""),
        "uf":                 getattr(emp, "uf", ""),
        "dt_const":           getattr(emp, "dt_const", ""),
        "dt_reg":             getattr(emp, "dt_reg", ""),
        "auditor":            getattr(emp, "auditor", ""),
        "sit_emissor":        getattr(emp, "sit_emissor", ""),
        "erro":       emp.erro,
        "ind": {},
        "historico": {},
        "campos_brutos": {},
    }

    if emp.erro or not emp.anos_dfp:
        resultado["ind"] = _indicadores_neutros()
        return resultado

    dt_ref = emp.ano(0)   # ano mais recente (DFP)
    setor  = emp.ajuste_setor

    # Campos brutos do ano de referência (DFP anual — base para LTM)
    campos = _extrair_campos(emp, dt_ref, fonte="dfp")
    resultado["data_ref"] = dt_ref

    # ── LTM (Últimos 12 Meses) quando há ITR mais recente que o DFP ─────────
    #
    #  P&L / DFC (receita, EBITDA, FCO, capex…):
    #    LTM = DFP_anual + Trimestre_atual_ITR − Trimestre_mesmo_período_ano_ant.
    #    Ex.: LTM abr/25-mar/26 = DFP_2025 + 1T26 − 1T25
    #
    #  Balanço (caixa, dívida, PL…):
    #    Usa diretamente o ITR mais recente (posição point-in-time).
    #
    dt_itr = emp.ultimo_itr()            # ex: "2026-03-31"
    if dt_itr and dt_itr > dt_ref and emp.trimestres_itr:
        from datetime import date as _date
        try:
            _d = _date.fromisoformat(dt_itr)
            # Mesmo trimestre, ano anterior (trata 29/fev → 28/fev)
            try:
                dt_itr_py = _date(_d.year - 1, _d.month, _d.day).isoformat()
            except ValueError:
                dt_itr_py = _date(_d.year - 1, _d.month, 28).isoformat()
        except ValueError:
            dt_itr_py = None

        itr_cur  = _extrair_campos(emp, dt_itr,    fonte="itr")
        itr_prev = (_extrair_campos(emp, dt_itr_py, fonte="itr")
                    if dt_itr_py and dt_itr_py in emp.trimestres_itr
                    else {})

        # P&L e DFC: aplica LTM quando temos os dois trimestres
        ltm_aplicado = False
        for k in _FLOW_KEYS:
            v_dfp  = campos.get(k)
            v_cur  = itr_cur.get(k)
            v_prev = itr_prev.get(k)
            if v_dfp is not None and v_cur is not None and v_prev is not None:
                campos[k] = v_dfp + v_cur - v_prev
                ltm_aplicado = True

        # Balanço: posição atual do ITR (point-in-time)
        for k in _BS_KEYS:
            v = itr_cur.get(k)
            if v is not None:
                campos[k] = v

        if ltm_aplicado or any(itr_cur.get(k) for k in _BS_KEYS):
            resultado["data_ref"] = dt_itr   # exibe "2026-03-31" no relatório
            resultado["ltm"] = True          # flag para o HTML indicar LTM

    # Indicadores do ano de referência
    # IMPORTANTE: _calc_indicadores_de_campos pode modificar campos in-place
    # (ex: lucro_bruto = None para concessão IFRIC 12). Por isso campos_brutos
    # é construído APÓS a chamada para capturar essas neutralizações.
    ind = _calc_indicadores_de_campos(campos, setor)

    resultado["campos_brutos"] = {k: v for k, v in campos.items() if v is not None}

    # Crescimento de receita: compara com ano anterior
    dt_ant = emp.ano(1)
    if dt_ant:
        campos_ant = _extrair_campos(emp, dt_ant, fonte="dfp")
        rec_ant = campos_ant.get("receita") or 0.0
        rec_atu = campos.get("receita") or 0.0
        if rec_ant and rec_ant != 0:
            ind["cresc_receita"] = _safe_div(rec_atu - rec_ant, abs(rec_ant)) * 100

    # gov_situacao: situação legal/operacional — usado exclusivamente para hard ceiling
    # de CCC em RJ/falência (via _calc_score_efetivo no rating_engine). Não entra
    # mais no score de pilar (P5 removido). gov_controle e gov_transparencia removidos.
    ind["gov_situacao"] = _score_situacao(
                              getattr(emp, "sit_emissor", ""),
                              getattr(emp, "sit_cvm", "ATIVO"))

    resultado["setor_cvm_ativ"] = getattr(emp, "setor_cvm", "")

    resultado["ind"] = ind

    # ── Série LTM trimestre a trimestre (evolução do score) ─────────────────────
    # Cada ponto carrega os indicadores LTM daquele trimestre/exercício. O score
    # é calculado no rating_engine (fonte única de verdade) sobre estes 'ind'.
    # Governança é metadado estático (cadastro CVM) → replicada em todos os pontos.
    serie_ltm: List[Dict] = []
    for p in _serie_ltm_campos(emp):
        ind_p = _calc_indicadores_de_campos(p["campos"], setor)
        ind_p["cresc_receita"] = p["cresc"]
        ind_p["gov_situacao"]  = ind.get("gov_situacao")   # necessário para hard ceiling RJ
        _pl = p["campos"].get("pl")
        serie_ltm.append({
            "data_ref": p["data_ref"],
            "ltm": p["ltm"],
            "ind": ind_p,
            "pl_negativo": (_pl is not None and _pl < 0),
        })
    resultado["serie_ltm"] = serie_ltm

    # Série histórica (até 3 anos) para sparklines e RED flag de FCO negativo
    historico: Dict[str, List] = {k: [] for k in ind}
    # Histórico de valores nominais (em R$) para agregações por ano no dashboard
    _CAMPOS_NOMINAIS = ("receita", "lucro_bruto", "ebit", "da", "capex", "lucro_liq",
                        "desp_fin", "fco", "fci", "fcf", "fcf_captacao", "fcf_pagamentos",
                        "var_caixa", "ativo_total", "caixa", "divida_cp", "divida_lp", "pl",
                        "goodwill")   # goodwill p/ detectar anos de M&A no CAGR normalizado
    historico_brutos: Dict[str, List] = {k: [] for k in _CAMPOS_NOMINAIS}
    fco_negativo_count = 0
    anos_hist = [emp.ano(n) for n in range(min(3, len(emp.anos_dfp)) - 1, -1, -1)]
    # _CAMPOS_NOMINAIS sync check — capex precisa estar na lista para aparecer no historico_brutos

    anos_hist = [a for a in anos_hist if a]

    for dt in anos_hist:
        c_h = _extrair_campos(emp, dt, fonte="dfp")
        i_h = _calc_indicadores_de_campos(c_h, setor)
        for k in ind:
            historico[k].append(i_h.get(k))
        for k in _CAMPOS_NOMINAIS:
            historico_brutos[k].append(c_h.get(k))
        fco = c_h.get("fco")
        if fco is not None and fco < 0:
            fco_negativo_count += 1

    # ── conv_caixa: usa MEDIANA dos últimos anos como valor reportado ───────────
    # Single-year é volátil (working capital pode girar muito). Mediana de 3 anos
    # captura a "qualidade estrutural" de conversão de EBITDA em caixa.
    # Se LTM tem valor, INCLUIMOS no cálculo da mediana (combina anuais + LTM atual).
    cv_hist_vals = [v for v in historico["conv_caixa"] if v is not None]
    if ind.get("conv_caixa") is not None:
        cv_hist_vals.append(ind["conv_caixa"])
    if len(cv_hist_vals) >= 2:
        cv_sorted = sorted(cv_hist_vals)
        n = len(cv_sorted)
        mediana_cv = cv_sorted[n // 2] if n % 2 else (cv_sorted[n//2 - 1] + cv_sorted[n//2]) / 2
        ind["conv_caixa"] = round(mediana_cv, 1)

    resultado["historico"] = historico
    resultado["historico_brutos"] = historico_brutos
    resultado["anos_hist"] = anos_hist
    resultado["fco_negativo_anos"] = fco_negativo_count

    # ── Snapshot do ITR mais recente (não-LTM, valores do filing direto) ─────
    # Útil para mostrar "1T26", "1S26" etc. como coluna ao lado dos exercícios anuais.
    if emp.trimestres_itr:
        dt_itr_ultimo = emp.trimestres_itr[-1]
        campos_itr_raw = _extrair_campos(emp, dt_itr_ultimo, fonte="itr")
        resultado["campos_itr"] = {k: v for k, v in campos_itr_raw.items() if v is not None}
        resultado["data_itr"] = dt_itr_ultimo
    else:
        resultado["campos_itr"] = {}
        resultado["data_itr"] = None

    # Flags adicionais para o rating engine
    pl = campos.get("pl")
    resultado["pl_negativo"] = (pl is not None and pl < 0)

    return resultado


def calcular_serie_trimestral(emp: "EmpresaData") -> List[Dict]:
    """
    Extrai série de P&L por trimestre INDIVIDUAL (não acumulado YTD).

    Para cada ano com dados DFP, tenta decompor em Q1-Q4:
      Q1 = ITR_Q1 (YTD = Q1 sozinho)
      Q2 = ITR_Q2 − ITR_Q1
      Q3 = ITR_Q3 − ITR_Q2
      Q4 = DFP    − ITR_Q3

    Cada ponto retorna:
      {data_ref, ano, quarter, is_annual,
       receita, ebit, ebitda, lucro_liq, lucro_bruto, fco,
       margem_ebitda, margem_bruta}

    Trimestres não computáveis (ITR ausente) são omitidos.
    Para empresas sem ITR, adiciona pontos anuais (quarter=4, is_annual=True).
    Retorna lista ordenada do mais antigo ao mais recente.
    """
    dfp_set = set(emp.anos_dfp or [])
    itr_set = set(emp.trimestres_itr or [])
    pts: List[Dict] = []

    def _build_q(data_ref: str, ano: int, q: int, is_annual: bool,
                 ytd_cur: Dict, ytd_prv: Optional[Dict] = None) -> Optional[Dict]:
        """Monta um ponto trimestral subtraindo ytd_cur - ytd_prv."""
        f: Dict[str, Optional[float]] = {}
        for campo in ("receita", "ebit", "lucro_liq", "lucro_bruto", "da", "fco"):
            v_cur = ytd_cur.get(campo)
            v_prv = ytd_prv.get(campo) if ytd_prv else None
            if v_cur is None:
                f[campo] = None
            elif v_prv is not None:
                f[campo] = v_cur - v_prv
            else:
                f[campo] = v_cur  # Q1: YTD = Q1 sozinho; ou anual sem ITR

        rec = f.get("receita")
        if not rec or rec <= 0:
            return None          # trimestre sem receita → descarta

        ebit_q = f.get("ebit") or 0.0
        da_q   = abs(f.get("da") or 0.0)
        ebitda_q = ebit_q + da_q if (f.get("ebit") is not None or da_q > 0) else None

        mb = (f["lucro_bruto"] / rec * 100) if f.get("lucro_bruto") is not None else None
        me = (ebitda_q / rec * 100) if ebitda_q is not None and rec else None

        return {
            "data_ref":     data_ref,
            "ano":          ano,
            "quarter":      q,
            "is_annual":    is_annual,
            "receita":      rec,
            "ebit":         f.get("ebit"),
            "ebitda":       ebitda_q,
            "lucro_liq":    f.get("lucro_liq"),
            "lucro_bruto":  f.get("lucro_bruto"),
            "fco":          f.get("fco"),
            "margem_bruta":  round(mb, 2) if mb is not None else None,
            "margem_ebitda": round(me, 2) if me is not None else None,
        }

    for dt_fy in sorted(dfp_set):
        ano = int(dt_fy[:4])
        # Deriva datas dos ITRs relativamente ao fim do ano fiscal (DFP),
        # suportando empresas com ano fiscal não-calendário (ex: Pettenati, Jun-30).
        from datetime import date as _date, timedelta as _td
        _fy_end = _date.fromisoformat(dt_fy)
        def _quarter_end(months_back: int) -> str:
            # Subtrai meses do fim do FY e ajusta para último dia do mês
            import calendar as _cal
            y, m = _fy_end.year, _fy_end.month - months_back
            while m <= 0:
                m += 12; y -= 1
            last_day = _cal.monthrange(y, m)[1]
            return f"{y}-{m:02d}-{last_day:02d}"
        dt_q1 = _quarter_end(9)  # 9 meses antes do fim FY
        dt_q2 = _quarter_end(6)  # 6 meses antes
        dt_q3 = _quarter_end(3)  # 3 meses antes

        fy  = _extrair_campos(emp, dt_fy, fonte="dfp")
        q1c = _extrair_campos(emp, dt_q1, fonte="itr") if dt_q1 in itr_set else None
        q2c = _extrair_campos(emp, dt_q2, fonte="itr") if dt_q2 in itr_set else None
        q3c = _extrair_campos(emp, dt_q3, fonte="itr") if dt_q3 in itr_set else None

        if q1c:
            p = _build_q(dt_q1, ano, 1, False, q1c, None)
            if p: pts.append(p)
        if q2c and q1c:
            p = _build_q(dt_q2, ano, 2, False, q2c, q1c)
            if p: pts.append(p)
        if q3c and q2c:
            p = _build_q(dt_q3, ano, 3, False, q3c, q2c)
            if p: pts.append(p)
        if q3c:
            p = _build_q(dt_fy, ano, 4, False, fy, q3c)
            if p: pts.append(p)
        elif not q1c and not q2c:
            # Empresa só tem DFP (Categoria B) → ponto anual
            p = _build_q(dt_fy, ano, 4, True, fy, None)
            if p: pts.append(p)

    # Adiciona trimestres do ano fiscal CORRENTE (ITRs após o último DFP).
    # Necessário para empresas com FY não encerrado (ex: FY jun-26 ainda em aberto)
    # e para manter os scores up-to-date com os últimos ITRs publicados.
    if dfp_set:
        last_dfp = max(dfp_set)
        from datetime import date as _d2
        _last_end = _d2.fromisoformat(last_dfp)
        import calendar as _cal2
        y_next, m_next = _last_end.year, _last_end.month
        y_next += 1  # próximo FY termina 1 ano depois
        _next_fy = f"{y_next}-{m_next:02d}-{_cal2.monthrange(y_next, m_next)[1]:02d}"
        # Datas dos ITRs do próximo FY (parcial)
        def _q_of_next(months_back: int) -> str:
            import calendar as _c3
            y, m = y_next, m_next - months_back
            while m <= 0:
                m += 12; y -= 1
            return f"{y}-{m:02d}-{_c3.monthrange(y, m)[1]:02d}"
        nq1 = _q_of_next(9)
        nq2 = _q_of_next(6)
        nq3 = _q_of_next(3)
        nq1c = _extrair_campos(emp, nq1, fonte="itr") if nq1 in itr_set else None
        nq2c = _extrair_campos(emp, nq2, fonte="itr") if nq2 in itr_set else None
        nq3c = _extrair_campos(emp, nq3, fonte="itr") if nq3 in itr_set else None
        if nq1c:
            p = _build_q(nq1, y_next, 1, False, nq1c, None)
            if p: pts.append(p)
        if nq2c and nq1c:
            p = _build_q(nq2, y_next, 2, False, nq2c, nq1c)
            if p: pts.append(p)
        if nq3c and nq2c:
            p = _build_q(nq3, y_next, 3, False, nq3c, nq2c)
            if p: pts.append(p)

    pts.sort(key=lambda p: (p["ano"], p["quarter"]))
    return pts


def _indicadores_neutros() -> Dict[str, Optional[float]]:
    """Retorna dict com todos os indicadores com valor None (empresa sem dados)."""
    chaves = [
        "dl_ebitda", "dl_pl", "cob_juros", "cob_ebitda_capex", "pct_divida_cp",
        "margem_ebitda", "conv_caixa", "roic", "custo_divida_roic", "capex_receita",
        "liq_corrente", "caixa_div_cp", "dscr", "fcf_margem",
        "dias_receb", "dias_estoque", "dias_pag", "ciclo_caixa",
        "roe", "margem_liq", "cresc_receita",
        "gov_situacao",   # mantido apenas para hard ceiling RJ no rating_engine
    ]
    return {k: None for k in chaves}
