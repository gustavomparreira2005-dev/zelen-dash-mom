# ============================================================================
# AVISO: CÓPIA do projeto de crédito ("1 Renda Fixa/Dash - Credito Privado").
# A fonte de verdade deste módulo é aquele projeto. Melhorias na metodologia
# de crédito NÃO se propagam automaticamente para cá — re-sincronize à mão.
# ============================================================================
"""
Build Empresa Lista — gera empresas_lista.csv com todas as não-financeiras ativas da CVM.

Inclui Categoria A (listadas B3 + registradas CVM) e Categoria B (não-listadas com
valores mobiliários públicos como debêntures, CRI, CRA).

Uso:
    python build_empresa_lista.py
    python build_empresa_lista.py --cache-dir cache_cvm --output empresas_lista.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


# ─── Setores financeiros a excluir ────────────────────────────────────────────
# Match por substring na versão normalizada (sem acentos, maiúsculas)
_SETORES_FINANCEIROS_KEYWORDS = [
    "BANCO",
    "ARRENDAMENTO MERCANTIL",
    "BOLSAS DE VALORES",
    "BOLSA DE VALORES",
    "INTERMEDIACAO FINANCEIRA",
    "SEGURADORAS",
    "CORRETORAS",
    "SECURITIZACAO DE RECEBIVEIS",
    "CREDITO IMOBILIARIO",
]

# Situações do emissor incompatíveis com análise (sem dados operacionais)
_SIT_EXCLUIR_KEYWORDS = [
    "PRE-OPERACIONAL",
    "PRE OPERACIONAL",
    "LIQUIDACAO EXTRAJUDICIAL",
    "FALIDA",
    "PARALISADA",
    "EM LIQUIDACAO JUDICIAL",
]


# ─── Ticker B3 para empresas conhecidas ───────────────────────────────────────
# Mapeamento cd_cvm → ticker B3 para empresas em que o nome CVM diverge da marca
_TICKER_B3_MAP: dict[str, str] = {
    # Renda Variável (Bolsa)
    "4170":  "VALE3",
    "9512":  "PETR4",
    "5410":  "WEGE3",
    "23264": "ABEV3",
    "22470": "MGLU3",
    "5258":  "RADL3",
    "17671": "VIVT3",
    "14443": "SBSP3",
    "14460": "CYRE3",
    "19739": "RENT3",
    # Nomes divergentes da marca — essenciais para busca
    "25372": "ASAI3",   # Sendas Distribuidora → Assaí
    "24171": "CRFB3",   # Atacadão S.A. → Carrefour Brasil (ex-CRFB3, cancelado dez/25)
    "14826": "PCAR3",   # Pão de Açúcar / CBD
    "27707": "AMOB3",   # Automob Participações — CVM DENOM_COMERC ainda mostra nome anterior

    # ── Financeiras (bancos, seguradoras, B3/corretoras) — valuation por FCFE ────
    "19348": "ITUB4",   # Itaú Unibanco — PN (mais líquido)
    "1023":  "BBAS3",   # Banco do Brasil
    "906":   "BBDC4",   # Bradesco — PN
    "20532": "SANB11",  # Santander Brasil — Unit
    "22616": "BPAC11",  # BTG Pactual — Unit (cadastro: Banco UBS Pactual)
    "20958": "ABCB4",   # Banco ABC Brasil — PN
    "1210":  "BRSR6",   # Banrisul — PNB
    "24600": "BMGB4",   # Banco BMG — PN
    "20567": "PINE4",   # Banco Pine — PN
    "1325":  "BMEB4",   # Banco Mercantil do Brasil — PN
    "922":   "BAZA3",   # Banco da Amazônia
    "1228":  "BNBR3",   # Banco do Nordeste
    "1155":  "BEES3",   # Banestes — ON
    "1171":  "BPAR3",   # Banpará (Banco do Estado do Pará)
    "1120":  "BGIP4",   # Banese (Banco do Estado de Sergipe) — PN
    "14206": "BSLI4",   # BRB (Banco de Brasília) — PN
    "7617":  "ITSA4",   # Itaúsa — PN (holding financeira)
    "16659": "PSSA3",   # Porto Seguro
    "23159": "BBSE3",   # BB Seguridade
    "23795": "CXSE3",   # Caixa Seguridade
    "24180": "IRBR3",   # IRB Brasil RE
    "23590": "WIZC3",   # Wiz Co
    "22497": "QUAL3",   # Qualicorp (corretora/administradora de saúde)
    "21610": "B3SA3",   # B3 (bolsa)

    # ── Energia Elétrica ───────────────────────────────────────────────────────
    "21490": "ALUP11",  # Alupar Investimento — Unit
    "26620": "AURE3",   # Auren Energia (ex-CESP / AES)
    "14451": "CEBR6",   # CEB — PNB
    "20648": "CEED3",   # CEEE-D
    "2461":  "CLSC4",   # Celesc — PN
    "2453":  "CMIG4",   # CEMIG — PN (mais líquido)
    "2577":  "CESP6",   # CESP — PNB (ações remanescentes pós-fusão Auren)
    "14869": "COCE5",   # Coelce — PNB
    "15636": "CGAS3",   # Comgás — ON
    "25127": "CMPG3",   # Compass Gas e Energia
    "19445": "CSMG3",   # Copasa
    "14311": "CPLE11",  # Copel — Unit (pós-privatização)
    "18139": "CSRN6",   # Cosern — PNB
    "18660": "CPFE3",   # CPFL Energia
    "18376": "TRPL4",   # CTEEP / ISA CTEEP — PN
    "19763": "ENBR3",   # EDP Energias do Brasil
    "16993": "EMAE4",   # EMAE — PN
    "15253": "ENGI11",  # Energisa — Unit
    "21237": "ENEV3",   # Eneva
    "20010": "EQTL3",   # Equatorial Energia
    "8036":  "LIGT3",   # Light S.A. (distribuidora)
    "19879": "LIGH3",   # Light S.A. (holding)
    "15539": "NEOE3",   # Neoenergia
    "23426": "SRNA3",   # Serena Geração (ex-Omega Energia)
    "20257": "TAEE11",  # Taesa — Unit
    "17329": "EGIE3",   # Tractebel → Engie Brasil Energia
    "11258": "TELB3",   # Telebras — ON
    "21636": "RNEW11",  # Renova Energia — Unit (distressed)
    "14176": "ELPL4",   # Enel Eletropaulo (AES Eletropaulo → Enel SP) — PN

    # ── Petróleo, Gás e Petroquímica ───────────────────────────────────────────
    "25291": "BRAV3",   # Brava Energia (ex-3R Petroleum)
    "4820":  "BRKM5",   # Braskem — PNA
    "22187": "PRIO3",   # PRIO (ex-PetroRio)
    "18465": "UGPA3",   # Ultrapar
    "24295": "VBBR3",   # Vibra Energia (ex-BR Distribuidora)
    "25534": "OPCT3",   # Oceanpact Serviços Marítimos

    # ── Mineração e Siderurgia ─────────────────────────────────────────────────
    "10456": "ALPA4",   # Alpargatas — PN
    "25984": "CBAV3",   # CBA (Companhia Brasileira de Alumínio)
    "4030":  "CSNA3",   # CSN
    "25585": "CMIN3",   # CSN Mineração
    "3069":  "FESA4",   # Ferbasa — PN
    "3980":  "GGBR4",   # Gerdau — PN
    "8656":  "GOAU4",   # Gerdau Metalúrgica — PN
    "94":    "PATI4",   # Panatlantica — PN
    "9393":  "PMAM3",   # Paranapanema
    "14664": "SHUL4",   # Schulz — PN
    "14320": "USIM5",   # Usiminas — PNA
    "5380":  "ALTE4",   # Altona — PN

    # ── Papel, Celulose e Madeira ──────────────────────────────────────────────
    "12653": "KLBN11",  # Klabin — Unit
    "13986": "SUZB3",   # Suzano
    "21091": "DXCO3",   # Dexco (ex-Duratex)
    "5770":  "EUCA4",   # Eucatex — PN
    "5762":  "ETER3",   # Eternit

    # ── Agronegócio ───────────────────────────────────────────────────────────
    "20036": "AGRO3",   # BrasilAgro
    "25704": "SOJA3",   # Boa Safra Sementes
    "24228": "CAML3",   # Camil Alimentos
    "25496": "JALL3",   # Jalles Machado
    "20745": "SLCE3",   # SLC Agrícola
    "20516": "SMTO3",   # São Martinho

    # ── Alimentos e Bebidas ────────────────────────────────────────────────────
    "20575": "JBSS3",   # JBS
    "20338": "MDIA3",   # M. Dias Branco
    "20788": "MRFG3",   # Marfrig
    "20931": "BEEF3",   # Minerva Foods
    "24317": "ZAMP3",   # Zamp (Burger King / Popeyes)
    "23574": "MEAL3",   # IMC (International Meal Company)

    # ── Varejo ────────────────────────────────────────────────────────────────
    "22357": "ALOS3",   # Allos (ex-BR Malls + Aliansce Sonae)
    "24848": "CEAB3",   # C&A Modas
    "27057": "VTRU3",   # Vitru Educação (Uniasselvi) — migrou da NASDAQ p/ B3 em 2023; TP_MERC vazio no cadastro CVM
    "6505":  "BHIA3",   # Casas Bahia (ex-Via)
    "19615": "GRND3",   # Grendene
    "4537":  "CGRA4",   # Grazziotin — PN
    "25186": "GMAT3",   # Grupo Mateus
    "24694": "SBFG3",   # Grupo SBF (Centauro / Nike)
    "21440": "LLIS3",   # Le Lis Blanc
    "8133":  "LREN3",   # Lojas Renner
    "22055": "AMAR3",   # Marisa
    "22608": "PGMN3",   # Pague Menos (Extrafarma)
    "9342":  "PNVL3",   # Panvel
    "20346": "PFRM3",   # Profarma
    "25038": "LJQQ3",   # Quero-Quero
    "4669":  "GUAR3",   # Riachuelo / Guararapes — ON
    "25208": "TFCO4",   # Track & Field — PN
    "11762": "VULC3",   # Vulcabras
    "25518": "WEST3",   # Westwing
    "24805": "VIVA3",   # Vivara
    "25836": "DOTZ3",   # Dotz
    "25461": "TKTO3",   # Grupo Toky  (cd_cvm correto)
    "25046": "DMVF3",   # D1000 Varejo Farma
    "23507": "OFSA3",   # Ourofino Saúde Animal
    "22519": "TECN3",   # Technos
    "19909": "BRML3",   # BR Malls
    "21008": "GSHP3",   # General Shopping Brasil

    # ── Saúde ─────────────────────────────────────────────────────────────────
    "24058": "AALR3",   # Aliança Saúde e Participações
    "24627": "BLAU3",   # Blau Farmacêutica
    "19623": "DASA3",   # DASA
    "21881": "FLRY3",   # Fleury
    "24392": "HAPV3",   # Hapvida
    "21431": "HYPE3",   # Hypera Pharma
    "25690": "MATD3",   # Mater Dei
    "24821": "RDOR3",   # Rede D'Or
    "19550": "NTCO3",   # Natura Cosméticos (holding pós-reorganização 2024)
    "19305": "BIOM3",   # Biomm

    # ── Construção Civil ──────────────────────────────────────────────────────
    "25275": "AVLL3",   # Alphaville Urbanismo
    "20630": "CRDE3",   # CR2 Empreendimentos
    "25100": "CURY3",   # Cury Construtora
    "21350": "DIRR3",   # Direcional Engenharia
    "20524": "EVEN3",   # Even Construtora
    "20770": "EZTC3",   # EZ Tec
    "16101": "GFSA3",   # Gafisa
    "25402": "HBRE3",   # HBR Realty
    "20877": "HBOR3",   # Helbor
    "20605": "JHSF3",   # JHSF Participações
    "25062": "LAVV3",   # Lavvi
    "23272": "LOGG3",   # Log Commercial Properties
    "24902": "MTRE3",   # Mitre Realty
    "21067": "MDNE3",   # Moura Dubeux
    "20915": "MRVE3",   # MRV Engenharia
    "20982": "MULT3",   # Multiplan
    "20494": "IGTI11",  # Iguatemi S.A. — Unit
    "13773": "PTBL3",   # Portobello
    "20451": "RDNI3",   # Rodobens
    "13781": "SCAR3",   # São Carlos Empreendimentos
    "21148": "TEND3",   # Tenda (Construtora)
    "20435": "TCSA3",   # Tecnisa
    "21130": "TRIS3",   # Trisul
    "25119": "MELK3",   # Melnick Even
    "21180": "NEXE3",   # Nexpe Participações (ex-PDG)

    # ── Indústria / Máquinas ──────────────────────────────────────────────────
    "25283": "AERI3",   # Aeris Energy (pás eólicas)
    "24953": "ALPK3",   # Allpark
    "26069": "ARML3",   # Armac Locação e Serviços
    "11975": "AZEV4",   # Azevedo & Travassos — PN
    "1520":  "BDLL4",   # Bardella — PN
    "1562":  "BALM4",   # Baumer — PN
    "20087": "EMBJ3",   # Embraer — ticker B3 passou a EMBJ3 (EMBR3 descontinuado)
    "6211":  "FRAS3",   # Frasle Mobility
    "11932": "MYPK3",   # Iochpe-Maxion
    "8575":  "LEVE3",   # Mahle Metal Leve
    "8451":  "POMO4",   # Marcopolo — PN
    "20613": "FRIO3",   # Metalfrio Solutions
    "22012": "MILS3",   # Mills Estruturas e Serviços
    "7510":  "ROMI3",   # Romi
    "6173":  "TASA4",   # Taurus Armas — PN
    "20800": "TGMA3",   # Tegma
    "6343":  "TUPY3",   # Tupy
    "11592": "UNIP6",   # Unipar Carbocloro — PNB
    "14346": "WHRL4",   # Whirlpool — PN
    "11070": "WLMM4",   # WLM Indústrias e Comércio — PN
    "12572": "RCSL4",   # Recrusul — PN
    "7870":  "KEPL3",   # Kepler Weber

    # ── Logística e Transporte ─────────────────────────────────────────────────
    "24112": "AZUL4",   # Azul Linhas Aéreas — PN
    "19453": "ECOR3",   # Ecorodovias
    "22675": "HBSA3",   # Hidrovias do Brasil
    "22020": "JSLG3",   # JSL
    "20710": "LOGN3",   # Log-In Logística
    "23612": "MAEL3",   # Maestro Locadora de Veículos
    "23825": "MOVI3",   # Movida
    "17450": "RAIL3",   # Rumo
    "18627": "SAPR11",  # Sanepar — Unit
    "17892": "STBP3",   # Santos Brasil
    "25160": "SEQL3",   # Sequoia Logística
    "25003": "SIMH3",   # Simpar
    "24716": "VAMO3",   # Vamos Locação
    "21202": "VLOG3",   # Vix Logística

    # ── Tecnologia e Telecom ──────────────────────────────────────────────────
    "25500": "BMOB3",   # Bemobi Mobile Tech
    "27693": "BRIT3",   # Brisanet Telecomunicações
    "23817": "BRQB3",   # BRQ Digital Solutions
    "20044": "CSUD3",   # CSU Digital
    "26026": "DESK3",   # Desktop Sigmanet
    "25453": "INTB3",   # Intelbras
    "24910": "LWSA3",   # Locaweb
    "25232": "CASH3",   # Méliuz
    "20362": "POSI3",   # Positivo Tecnologia
    "23302": "QUAL3",   # Quality Digital
    "19992": "TOTS3",   # TOTVS
    "20028": "VLID3",   # Valid Soluções
    "25836": "DOTZ3",   # Dotz (já acima, duplicata — ignorada pelo dict)

    # ── Educação ─────────────────────────────────────────────────────────────
    "17973": "COGN3",   # Cogna Educação
    "25526": "CSED3",   # Cruzeiro do Sul Educacional
    "23221": "SEER3",   # Ser Educacional
    "21016": "YDUQ3",   # Yduqs
    "23248": "ANIM3",   # Ânima Educação
    "27677": "ATMG3",   # Atom Educação e Editora

    # ── Saneamento ────────────────────────────────────────────────────────────
    "23175": "CABC3",   # CAB Ambiental
    "25550": "ORVR3",   # Orizon Resíduos
    "24961": "AMBP3",   # Ambipar

    # ── Financeiro / Holdings ─────────────────────────────────────────────────
    "18724": "BRAP4",   # Bradespar — PN
    "19836": "CSAN3",   # Cosan
    "22454": "SHOW3",   # T4F Entretenimento
    "19100": "CTAX4",   # Contax Participações — PN (RJ)

    # ── Lazer e Turismo ───────────────────────────────────────────────────────
    "24260": "SMFT3",   # SmartFit
    "23310": "CVCB3",   # CVC Brasil
    "1694":  "BMKS3",   # Monark

    # ── Têxtil e Vestuário ────────────────────────────────────────────────────
    "5207":  "DOHL4",   # Dohler — PN
    "22349": "AZZAS",   # Azzas 2154 (ex-Arezzo) — verificar série exata

    # ── Indústria Diversa / Pequenas Caps ─────────────────────────────────────
    "1570":  "BAUH4",   # Excelsior Alimentos — PN
    "3069":  "FESA4",   # Ferbasa (já acima)
    "7811":  "JFEN3",   # João Fortes Engenharia
    "9539":  "PTNT4",   # Pettenati — PN
    "7544":  "FTRX4",   # Renauxview — PN
    "20060": "LUPA3",   # Lupatech (distressed)
    "13471": "PLAS3",   # Plascar Participações Industriais
    "9989":  "RPMG3",   # Refinaria de Petróleo Manguinhos
    "21342": "OSXB3",   # OSX Brasil (RJ)
    "20478": "PDGR3",   # PDG Realty (distressed)
    "20702": "VIVR3",   # Viver Incorporadora (distressed)
    "12190": "BOBR4",   # Bombril — PN (RJ)
    "25658": "AGXY3",   # Agrogalaxy (RJ)
    "20990": "AMER3",   # Americanas (RJ)

    # ── Segunda rodada — cobertura adicional ──────────────────────────────────

    # Alimentos / Agro
    "16292": "BRFS3",   # BRF S.A.
    "13285": "JOPA3",   # Josapar — Joaquim Oliveira S.A.
    "13765": "MNPR3",   # Minupar Participações
    "20621": "FHER3",   # Fertilizantes Heringer
    "4693":  "ODER4",   # Oderich — PN

    # Energia elétrica — distribuidoras subsidiárias
    "14524": "CEEB3",   # Coelba (Neoenergia BA)
    "14362": "CEPE3",   # Celpe (Neoenergia PE)
    "17485": "EKTR4",   # Elektro Redes — PN (Neoenergia SP)
    "16616": "CEGR3",   # CEG Distribuidora de Gás RJ
    "16861": "CASN3",   # Casan (saneamento SC)
    "3190":  "REDE3",   # Rede Energia Participações

    # Energia — geração/concessão
    "18368": "GPAR3",   # Duke Energy Geração Paranapanema (ex-AES)
    "20540": "CPRE3",   # CPFL Renováveis (cancelada 2022)
    "24929": "TIMS3",   # TIM S.A. (opco)

    # Logística e transporte
    "24660": "BBML3",   # BBM Logística
    "19330": "TPIS3",   # TPI — Triunfo Participações e Investimentos
    "18775": "IVPR3",   # Invepar

    # Tecnologia / Digital
    "25259": "ENJU3",   # Enjoei S.A.
    "25569": "ELMD3",   # Eletromidia (cancelada — mídia OOH)

    # Imobiliário / Construção
    "25070": "PLPL3",   # Plano&Plano Desenvolvimento Imobiliário
    "21040": "SYNE3",   # Syn Prop & Tech (ex-BR Properties)

    # Indústria / Manufatura
    "14109": "RAPT4",   # Randon S.A. Implementos — PN
    "4146":  "CTSA4",   # Karsten — PN (têxtil)
    "3077":  "CEDO4",   # Cedro Cachoeira — PN (têxtil)
    "8397":  "MGEL4",   # Mangels Industrial — PN
    "8753":  "MTSA4",   # Metisa Metalúrgica Timboense — PN
    "13439": "RSUL4",   # Metalúrgica Riosulense — PN
    "5312":  "MNDL3",   # Mundial S.A.
    "11991": "MWET3",   # Wetzel S.A.
    "13366": "HAGA4",   # Haga S.A. Indústrias Mecânicas — PN
    "6629":  "HETA4",   # Hercules S.A. Fábricas de Talheres — PN
    "8427":  "ESTR4",   # Estrela S.A. (brinquedos) — PN
    "12696": "SNSY5",   # Sansuy Indústria de Plásticos — PNB
    "2100":  "PENL3",   # Penalty (Artigos Esportivos)

    # Imobiliário / Serviços
    "20370": "LPSB3",   # Lopes (consultoria imóveis)
    "24236": "PRNR3",   # Priner Serviços Industriais
    "22780": "UCAS3",   # Unicasa Indústria de Móveis

    # Saúde / Farmácia
    "21334": "NUTR3",   # Nutriplant (em recuperação extrajudicial)

    # Lazer / Turismo
    "6700":  "HOOT4",   # Hotéis Othon — PN (em RJ)

    # Holdings / Investimento
    "8893":  "MOAR3",   # Monteiro Aranha — cancelada
    "11231": "TKNO3",   # Tekno S.A. — cancelada

    # Infraestrutura / RJ
    "7595":  "INEP4",   # Inepar S.A. Indústria e Construções — PN (em RJ)
    "22365": "ENAT3",   # Enauta Participações (ex-QGEP) — cancelada
    "19569": "GOLL4",   # GOL Linhas Aéreas — PN — cancelada
}

# ─── Overrides de nome comercial ──────────────────────────────────────────────
# Quando DENOM_COMERC da CVM está desatualizado ou é nome de subsidiária
_NOME_OVERRIDE: dict[str, str] = {

    # ════════════════════════════════════════════════════════════════
    # REBRANDINGS — CVM mantém nome antigo, marca comercial mudou
    # ════════════════════════════════════════════════════════════════
    "17973": "COGNA",               # KROTON EDUCACIONAL (rebranding 2020)
    "21016": "YDUQS",               # ESTÁCIO PARTICIPAÇÕES (rebranding 2020)
    "22470": "MAGALU",              # MAGAZINE LUIZA
    "19623": "DASA",                # DIAGNOSTICOS DA AMERICA
    "25372": "ASSAÍ",               # SENDAS DISTRIBUIDORA
    "5258":  "RAIA DROGASIL",       # DROGASIL SA (fusão Raia + Drogasil)
    "17450": "RUMO",                # ALL – AMÉRICA LATINA LOGÍSTICA (holding)
    "15300": "RUMO MALHA NORTE",    # ALL MALHA NORTE
    "17850": "RUMO MALHA OESTE",    # ALL MALHA OESTE
    "17930": "RUMO MALHA PAULISTA", # ALL MALHA PAULISTA
    "15709": "RUMO MALHA SUL",      # ALL MALHA SUL
    "26484": "RUMO MALHA CENTRAL",  # RUMO MALHA CENTRAL (SPE 2023)
    "11312": "OI",                  # BRASIL TELECOM (incorporada pela Oi)
    "27022": "V.TAL",               # BRASIL TELECOM (rede neutra V.tal)
    "14826": "GPA",                 # PÃO DE AÇÚCAR (Grupo Pão de Açúcar)
    "26620": "AUREN",               # AUREN ENERGIA (rebranding CESP/AES)
    "24783": "NATURA &CO",          # NATURA & CO HOLDING
    "19550": "NATURA",              # NATURA COSMETICOS (opco Brasil)
    "19836": "COSAN",               # COSAN SA INDUSTRIA E COMERCIO
    "22365": "ENAUTA",              # QGEP PARTICIPAÇÕES (rebranding 2021)
    "20257": "TAESA",               # TRANSMISSORA ALIANÇA DE ENERGIA ELÉTRICA
    "22187": "PRIO",                # PETRO RIO (rebranding 2022)
    "24910": "LOCAWEB",             # LWSA S/A (holding Locaweb)
    "19909": "BR MALLS",            # BR MALLS PARTICIPAÇÕES
    "27707": "AUTOMOB",             # VAMOS COMÉRCIO DE MÁQUINAS (nome antigo CVM)
    "17671": "VIVO",                # TELEFÔNICA BRASIL
    "25291": "BRAVA ENERGIA",       # BRAVA ENERGIA (ex-3R Petroleum)
    "24317": "ZAMP",                # BURGER KING/POPEYES (operador)
    "20036": "BRASILAGRO",          # BRASILAGRO CIA BRAS DE PROP AGRICOLAS
    "13854": "MARCOPOLO",           # CIA MARCOPOLO
    "2577":  "CESP",                # CESP CIA ENERGETICA SAO PAULO
    "20044": "CSU DIGITAL",         # CSU DIGITAL
    "16292": "BRF",                 # BRF S.A.
    "5410":  "WEG",                 # WEG SA

    # ════════════════════════════════════════════════════════════════
    # NOMES LONGOS / DESCRIÇÃO JURÍDICA → MARCA CURTA
    # ════════════════════════════════════════════════════════════════

    # Infraestrutura de transportes
    "24708": "NTS",                 # NOVA TRANSPORTADORA DO SUDESTE
    "22675": "HIDROVIAS",           # HIDROVIAS DO BRASIL
    "20710": "LOG-IN",              # LOG-IN LOGISTICA INTERMODAL
    "17949": "MRS LOGÍSTICA",       # MRS LOGÍSTICA
    "22020": "JSL",                 # JSL S.A.
    "25003": "SIMPAR",              # SIMPAR (holding JSL/Vamos/Movida)
    "23825": "MOVIDA",              # MOVIDA PARTICIPAÇÕES
    "24716": "VAMOS",               # VAMOS LOCAÇÃO DE CAMINHÕES, MÁQUINAS...
    "19453": "ECORODOVIAS",         # ECORODOVIAS INFRAESTRUTURA E LOGÍSTICA
    "21903": "ECORODOVIAS CONC.",   # ECORODOVIAS CONCESSÕES E SERVIÇOS
    "20800": "TEGMA",               # TEGMA GESTÃO LOGÍSTICA SA
    "21202": "VIX LOGÍSTICA",       # VIX LOGÍSTICA S/A
    "25160": "SEQUOIA",             # SEQUOIA LOGÍSTICA E TRANSPORTES
    "27260": "CORREDOR LOG.",       # CORREDOR LOGÍSTICA E INFRAESTRUTURA

    # Concessionárias de rodovias
    "20192": "AUTOBAN",             # CONC. SISTEMA ANHANGUERA-BANDEIRANTES
    "22411": "ECOVIAS DOS IMIGRANTES", # CONC. AYRTON SENNA E CARVALHO PINTO
    "22071": "ROTA DAS BANDEIRAS",  # CONC. ROTA DAS BANDEIRAS
    "23922": "ROTA DO OESTE",       # CONC. ROTA DO OESTE
    "23515": "GRU AIRPORT",         # CONC. AEROPORTO INTERNACIONAL GUARULHOS
    "22268": "CART",                # CONC. AUTO RAPOSO TAVARES
    "21997": "AUTOPISTA LITORAL SUL",
    "21989": "AUTOPISTA FERNÃO DIAS",
    "22004": "AUTOPISTA FLUMINENSE",
    "21970": "AUTOPISTA PLANALTO SUL",
    "21962": "AUTOPISTA RÉGIS BITTENCOURT",
    "23167": "RODOVIAS DAS COLINAS",
    "24341": "ENTREVIAS",           # ENTREVIAS CONC. DE RODOVIAS
    "23833": "CRS-MS",              # CONC. RODOVIA SUL-MATOGROSSENSE
    "22721": "RODOVIAS TIETÊ",      # CONC. RODOVIAS TIETÊ
    "22225": "TRANSBRASILIANA",     # TRANSBRASILIANA CONC. DE RODOVIA
    "24104": "MG-050",              # CONC. RODOVIA MG-050
    "23884": "INVIASUL",            # CONC. RODOVIAS MINAS GERAIS
    "25356": "RIS",                 # CONC. RODOVIAS INTEGRADAS DO SUL
    "21849": "INTERVIAS",           # INTERVIAS
    "18775": "INVEPAR",             # INVEPAR
    "23868": "CONCEBRA",            # CONC. RODOVIAS CENTRAIS DO BRASIL
    "26638": "ECOVIAS DO ARAGUAIA", # CONC. ECOVIAS DO ARAGUAIA
    "26581": "ECOVIAS DO CERRADO",  # CONC. ECOVIAS DO CERRADO
    "24139": "ECOPONTE",            # CONC. PONTE RIO-NITERÓI
    "26859": "VIA CATARINA",        # CONC. CATARINENSE DE RODOVIAS

    # Energia elétrica — distribuidoras
    "14176": "ENEL ELETROPAULO",    # Eletropaulo → AES Eletropaulo → Enel SP
    "16527": "RGE SUL",             # AES Sul Distrib. Gaúcha → RGE Sul (nome atual)
    "16608": "EQUATORIAL MA",       # EQUATORIAL MARANHÃO
    "18309": "EQUATORIAL PA",       # EQUATORIAL PARÁ
    "25577": "CELG-D",              # CELG D
    "5576":  "ENERGISA MS",         # ENERGISA MATO GROSSO DO SUL
    "14605": "ENERGISA MT",         # ENERGISA MATO GROSSO
    "3271":  "ENERGISA MR",         # ENERGISA MINAS RIO
    "21938": "ENERGISA PB",         # ENERGISA PARAÍBA
    "18996": "ENERGISA SE",         # ENERGISA SERGIPE
    "20303": "CEMIG D",             # CEMIG DISTRIBUIÇÃO
    "20320": "CEMIG GT",            # CEMIG GERAÇÃO E TRANSMISSÃO
    "26808": "COPEL D",             # COPEL DISTRIBUIÇÃO
    "24740": "COPEL GT",            # COPEL GERAÇÃO E TRANSMISSÃO
    "3050":  "AMPLA",               # AMPLA (Enel Rio)
    "17485": "ELEKTRO",             # ELEKTRO REDES
    "14362": "CELPE",               # CELPE
    "14524": "COELBA",              # COELBA
    "14869": "COELCE",              # CIA ENERG CEARA - COELCE
    "18139": "COSERN",              # COSERN
    "5576":  "ENERSUL",             # ENERSUL
    "16241": "SANASA",              # SANASA-CAMPINAS
    "18546": "CAGECE",              # CAGECE
    "19445": "COPASA",              # COPASA MG
    "16861": "CASAN",               # CASAN
    "27154": "CEEE-G",              # CIA ESTADUAL GERAÇÃO ENERGIA ELÉTRICA CEEE-G
    "27472": "ELETRONORTE",         # CENTRAIS ELÉTRICAS DO NORTE DO BRASIL
    "27480": "ELETROSUL",           # ELETROBRAS CGT ELETROSUL
    "3328":  "CHESF",               # CHESF
    "16985": "BANDEIRANTE",         # BANDEIRANTE
    "2437":  "AXIA ENERGIA",        # AXIA ENERGIA
    "17329": "TRACTEBEL",           # TRACTEBEL ENERGIA
    "23230": "RAÍZEN ENERGIA",      # RAÍZEN ENERGIA
    "25917": "RAÍZEN",              # RAÍZEN S.A.
    "27103": "CTG BRASIL",          # CHINA THREE GORGES BRASIL ENERGIA
    "26441": "SERENA",              # SERENA ENERGIA (ex-Votorantim Energia)

    # Energia elétrica — geração/transmissão (SPEs com nomes longos)
    "22683": "CPTE",                # CACHOEIRA PAULISTA TRANSMISSORA
    "23388": "SANTO ANTÔNIO ENERGIA",
    "25097": "NORTE ENERGIA",
    "26670": "ESPERANZA TRANSMISSORA",
    "22179": "AFLUENTE TRANSMISSÃO",
    "19364": "ITAPEBI GERAÇÃO",
    "18589": "INVESTCO",
    "22594": "DESENVIX",
    "20052": "DINÂMICA ENERGIA",
    "27219": "GRANJA FARIA",        # obscure
    "26174": "AUREN OPERAÇÕES",
    "25640": "AUREN PARTICIPAÇÕES",
    "3204":  "CPFL TRANSMISSÃO",
    "24422": "ENERG. JAGUARA",      # COMPANHIA ENERGÉTICA JAGUARA
    "24430": "ENERG. MIRANDA",      # COMPANHIA ENERGÉTICA MIRANDA
    "24155": "ENERG. SINOP",        # COMPANHIA ENERGÉTICA SINOP

    # Gás e saneamento
    "25178": "GASMIG",              # COMPANHIA DE GÁS DE MINAS GERAIS
    "15636": "COMGÁS",              # COMPANHIA DE GÁS DE SÃO PAULO
    "27065": "METRÔ SP",            # COMPANHIA DO METROPOLITANO DE SÃO PAULO
    "16616": "CEG",                 # CEG (Gás do RJ)
    "25127": "COMPASS",             # COMPASS GÁS E ENERGIA
    "24830": "BRK AMBIENTAL",       # BRK AMBIENTAL PARTICIPAÇÕES
    "23175": "CAB AMBIENTAL",       # COMPANHIA DE AGUAS DO BRASIL

    # Telecom
    "23531": "CLARO",               # CLARO TELECOM PARTICIPAÇÕES
    "27090": "TIM BRASIL",          # TIM BRASIL SERVIÇOS E PARTICIPAÇÕES
    "24929": "TIM",                 # TIM S.A. (opco)
    "26050": "UNIFIQUE",            # UNIFIQUE TELECOMUNICAÇÕES
    "21032": "CTBC TELECOM",        # CTBC TELECOM
    "27316": "BRAZIL TOWER",        # BRAZIL TOWER, CESSÃO DE INFRA
    "27693": "BRISANET",            # BRISANET SERVIÇOS DE TELECOMUNICAÇÕES
    "25194": "ALARES",              # ALARES INTERNET PARTICIPAÇÕES
    "27367": "LIGGA",               # LIGGA TELECOMUNICAÇÕES
    "26948": "ELEA DIGITAL",        # ELEA DIGITAL INFRA E REDES TELECOM
    "27880": "HIGHLINE",            # HIGHLINE BRASIL II INFRA TELECOM
    "18597": "DTCOM",
    "27499": "BTP",                 # BRASIL TECNOLOGIA E PARTICIPAÇÕES

    # Educação
    "23248": "ÂNIMA",               # ANIMA HOLDING
    "23221": "SER EDUCACIONAL",     # SER EDUCACIONAL
    "27251": "INSPIRALI",           # INSPIRALI EDUCAÇÃO
    "25526": "CRUZEIRO DO SUL",     # CRUZEIRO DO SUL EDUCACIONAL
    "28002": "CBESE",               # COMPANHIA BRASILEIRA DE EDUCAÇÃO E SISTEMA DE ENSINO
    "27057": "VITRU",               # VITRU EDUCAÇÃO

    # Saúde
    "24392": "HAPVIDA",             # HAPVIDA PARTICIPAÇÕES E INVESTIMENTOS
    "25690": "MATER DEI",           # HOSPITAL MATER DEI
    "25879": "KORA SAÚDE",          # KORA SAÚDE PARTICIPAÇÕES
    "21881": "FLEURY",              # FLEURY SA
    "19623": "DASA",                # DIAGNOSTICOS DA AMERICA
    "20125": "BRADSAÚDE",           # BRADSAÚDE S.A.
    "25771": "CALEDÔNIA SAÚDE",     # CALEDÔNIA SAÚDE
    "25682": "CM HOSPITALAR",       # CM HOSPITALAR
    "26123": "ONCOCLÍNICAS",        # ONCOCLÍNICAS DO BRASIL SERVIÇOS MÉDICOS
    "26700": "EUROFARMA",           # EUROFARMA LABORATÓRIOS
    "9342":  "PANVEL",              # DIMED – DISTRIBUIDORA DE MEDICAMENTOS
    "22608": "PAGUE MENOS",         # EMPREENDIMENTOS PAGUE MENOS
    "25046": "D1000",               # D1000 VAREJO FARMA
    "20346": "PROFARMA",            # PROFARMA
    "26182": "TEUTO",               # LABORATÓRIO TEUTO BRASILEIRO
    "24627": "BLAU",                # BLAU FARMACÊUTICA
    "21431": "HYPERA",              # HYPERA PHARMA S/A

    # Varejo e bens de consumo
    "24260": "SMARTFIT",            # SMARTFIT ESCOLA DE GINÁSTICA E DANÇA
    "23310": "CVC",                 # CVC BRASIL OPERADORA E AGÊNCIA DE VIAGENS
    "25410": "CENTAURO",            # SBF COMÉRCIO DE PRODUTOS ESPORTIVOS
    "24694": "GRUPO SBF",           # GRUPO SBF (holding Centauro)
    "4669":  "RIACHUELO",           # GUARARAPES CONFECÇÕES (marca Riachuelo)
    "22055": "MARISA",              # MARISA LOJAS SA
    "25038": "QUERO-QUERO",         # LOJAS QUERO-QUERO
    "8133":  "LOJAS RENNER",        # RENNER
    "21440": "LE LIS BLANC",        # LE LIS BLANC DEUX
    "24848": "C&A",                 # C&A MODAS LTDA
    "6505":  "CASAS BAHIA",         # GRUPO CASAS BAHIA (ex-Via Varejo / ex-Ponto Frio)
    "20494": "IGUATEMI",            # IGUATEMI EMPRESA DE SHOPPING CENTERS
    "5312":  "MUNDIAL",
    "26204": "BLUEFIT",             # BLUEFIT ACADEMIAS DE GINÁSTICA E PARTICIPAÇÕES
    "24694": "GRUPO SBF",
    "25208": "TRACK&FIELD",         # TRACK & FIELD CO
    "27529": "FISIA",               # FISIA COMÉRCIO DE PRODUTOS ESPORTIVOS

    # Imobiliário / construção
    "14460": "CYRELA",              # CYRELA BRAZIL REALTY
    "25100": "CURY",                # CURY CONSTRUTORA E INCORPORADORA
    "21350": "DIRECIONAL",          # DIRECIONAL ENGENHARIA SA
    "21148": "TENDA",               # CONSTRUTORA TENDA S/A
    "20605": "JHSF",                # JHSF PART
    "25062": "LAVVI",               # LAVVI EMPREENDIMENTOS IMOBILIÁRIOS
    "24902": "MITRE",               # MITRE REALTY EMPREENDIMENTOS E PARTICIPAÇÕES
    "21067": "MOURA DUBEUX",        # MOURA DUBEUX
    "25275": "ALPHAVILLE",          # ALPHAVILLE S.A.
    "23272": "LOG",                 # LOG COMMERCIAL PROPERTIES E PARTICIPAÇÕES
    "20982": "MULTIPLAN",           # MULTIPLAN
    "25143": "PACAEMBU",            # PACAEMBU CONSTRUTORA
    "22357": "ALLOS",               # ALLOS S.A. (fusão BR Malls + Aliansce Sonae)
    "24953": "ALLPARK",             # ALLPARK EMPREENDIMENTOS
    "13781": "SÃO CARLOS",          # SÃO CARLOS EMPREEND. E PARTICIPAÇÕES
    "20710": "LOG-IN",
    "25437": "URBA",                # URBA DESENVOLVIMENTO URBANO

    # Agro
    "20745": "SLC AGRÍCOLA",        # SLC AGRICOLA SA
    "20516": "SÃO MARTINHO",        # SÃO MARTINHO SA
    "25496": "JALLES MACHADO",      # JALLES MACHADO S.A
    "25186": "GRUPO MATEUS",        # GRUPO MATEUS S.A.
    "25704": "BOA SAFRA",           # BOA SAFRA SEMENTES
    "26166": "HORTIFRUTI",          # GRUPO FARTURA DE HORTIFRUT
    "25950": "TRÊS TENTOS",         # TRÊS TENTOS AGROINDUSTRIAL
    "19232": "PROMAN",
    "26000": "TS AGRO",

    # Indústria e materiais
    "25984": "CBA",                 # COMPANHIA BRASILEIRA DE ALUMINIO
    "25283": "AERIS",               # AERIS IND. E COM. DE EQUIP. PARA GER. DE ENG.
    "22012": "MILLS",               # MILLS ESTRUTURAS E SERVIÇOS DE ENGENHARIA
    "11932": "IOCHPE-MAXION",       # IOCHPE-MAXION
    "14109": "RANDON",              # RANDON SA IMPLEMENTOS E PARTICIPAÇÕES
    "6211":  "FRASLE",              # FRASLE MOBILITY S.A.
    "13986": "SUZANO",              # SUZANO PAPEL E CELULOSE SA
    "9067":  "SUZANO HOLDING",      # SUZANO HOLDING S.A.
    "12653": "KLABIN",              # KLABIN
    "22810": "ELDORADO",            # ELDORADO CELULOSE E PAPEL
    "4820":  "BRASKEM",             # BRASKEM
    "11592": "UNIPAR",              # UNIPAR CARBOCLORO
    "3980":  "GERDAU",              # GERDAU S.A.
    "8656":  "GERDAU METALÚRGICA",  # METALÚRGICA GERDAU
    "14320": "USIMINAS",            # USIMINAS
    "4030":  "CSN",                 # CSN
    "25585": "CSN MINERAÇÃO",       # CSN MINERAÇÃO
    "4170":  "VALE",                # VALE
    "20931": "MINERVA",             # MINERVA S/A
    "5380":  "ALTONA",              # ELECTRO AÇO ALTONA
    "26980": "VIPAL",               # BORRACHAS VIPAL
    "21237": "ENEVA",               # ENEVA SA
    "27189": "VOTORANTIM CIMENTOS",
    "21490": "ALUPAR",              # ALUPAR INVESTIMENTO
    "24588": "ENERGISA TRANS.",     # ENERGISA TRANSMISSÃO

    # Financeiro / Holdings
    "18724": "BRADESPAR",           # BRADESPAR S/A
    "16772": "BNDESPAR",            # BNDESPAR
    "14451": "CEB",                 # CEB – COMPANHIA ENERGÉTICA DE BRASÍLIA
    "3824":  "CPFL",                # CPFL
    "19275": "CPFL PIRATININGA",    # CPFL - PIRATININGA
    "18660": "CPFL ENERGIA",        # CPFL ENERGIA S.A.
    "20540": "CPFL RENOVÁVEIS",     # CPFL RENOVÁVEIS
    "3204":  "CPFL TRANSMISSÃO",    # CPFL TRANSMISSÃO
    "20257": "TAESA",               # TRANSMISSORA ALIANÇA DE ENERGIA ELÉTRICA
    "18376": "CTEEP",               # CTEEP
    "18465": "ULTRAPAR",            # ULTRAPAR PARTICIPAÇÕES SA

    # Alimentação / bebidas
    "23264": "AMBEV",               # AMBEV S.A.
    "20575": "JBS",                 # JBS SA
    "20788": "MARFRIG",             # MARFRIG
    "16292": "BRF",                 # BRF S.A.
    "4693":  "ODERICH",             # CONSERVAS ODERICH
    "24228": "CAMIL",               # CAMIL ALIMENTOS
    "20338": "M. DIAS BRANCO",      # M DIAS BRANCO SA IND E COM DE ALIMENTOS
    "26190": "CARAMURU",            # CARAMURU ALIMENTOS
    "27014": "SOLAR BEBIDAS",       # SOLAR BEBIDAS S.A.
    "25712": "GPS",                 # GPS PARTICIPAÇÕES E EMPREENDIMENTOS
    "5258":  "RAIA DROGASIL",

    # Tecnologia / digital
    "20028": "VALID",               # Valid Soluções (CVM tem nome antigo: American Banknote Co.)
    "19992": "TOTVS",               # TOTVS S.A.
    "24910": "LOCAWEB",             # LWSA S/A
    "25232": "MELIUZ",              # MELIUZ S.A.
    "25500": "BEMOBI",              # BEMOBI MOBILE TECH S.A.
    "23817": "BRQ",                 # BRQ SOLUÇÕES EM INFORMÁTICA
    "25747": "INFRACOMMERCE",       # INFRACOMMERCE CXAAS S.A.
    "26786": "SENIOR",              # SENIOR SISTEMAS S.A.
    "23302": "QUALITY",             # QUALITY SOFTWARE S.A.
    "26034": "MULTILASER",          # GRUPO MULTILASER S.A.
    "25453": "INTELBRAS",           # INTELBRAS S.A.
    "20362": "POSITIVO",            # POSITIVO INFORMATICA SA
    "26077": "TC",                  # TC S.A.
    "25836": "DOTZ",                # DOTZ S.A.
    "25259": "ENJOEI",              # ENJOEI S.A.
    "25399": "NEOGRID",             # NEOGRID PARTICIPAÇÕES

    # Aviação / turismo
    "19569": "GOL",                 # GOL LINHAS AÉREAS INTELIGENTES SA
    "24112": "AZUL",                # AZUL S.A.
    "22454": "T4F",                 # T4F ENTRETENIMENTO SA
    "23310": "CVC",
    "23604": "YOU INC",             # YOU INC INCORPORADORA

    # Petróleo e gás
    "9512":  "PETROBRAS",           # PETROBRAS
    "22187": "PRIO",                # PETRO RIO
    "27200": "PRIO JAGUAR",         # PETRO RIO JAGUAR PETRÓLEO
    "25780": "PETRORECÔNCAVO",      # PETRORRECÔNCAVO S.A.
    "26590": "AURA ALMAS",          # AURA ALMAS MINERAÇÃO
    "24295": "VIBRA",               # VIBRA ENERGIA S/A
    "21814": "FNS",                 # FERROVIA NORTE SUL
    "15369": "FCA",                 # FERROVIA CENTRO-ATLÂNTICA

    # Saneamento
    "23396": "AEGEA",               # AEGEA SANEAMENTO E PARTICIPAÇÕES
    "14443": "SABESP",
    "19186": "SANEAGO",
    "18627": "SANEPAR",
    "16748": "CORSAN",
    "23469": "PROLAGOS",
    "27642": "EQUIPAV SANEAMENTO",

    # Outros serviços / diversificados
    "27049": "ORIZON",              # ORIZON MEIO AMBIENTE
    "25550": "ORIZON RESÍDUOS",     # ORIZON VALORIZAÇÃO DE RESÍDUOS
    "24961": "AMBIPAR",             # AMBIPAR PARTICIPAÇÕES E EMPREENDIMENTOS
    "27073": "SOLVI",               # SOLVI ESSENCIS AMBIENTAL
    "20494": "IGUATEMI",            # IGUATEMI EMPRESA DE SHOPPING CENTERS
    "8672":  "IGUATEMI",            # IGUATEMI S.A. (holding)
    "23574": "IMC",                 # INTERNATIONAL MEAL COMPANY ALIMENTAÇÃO
    "26379": "MADERO",              # MADERO INDÚSTRIA E COMÉRCIO
    "20613": "METALFRIO",           # METALFRIO SOLUTIONS
    "22144": "METRÓRIO",            # METRÓRIO
    "25469": "GRUPO SALTA",         # GRUPO SALTA EDUCAÇÃO... wait this is 27820
    "27820": "GRUPO SALTA",         # GRUPO SALTA EDUCAÇÃO
    "24236": "PRINER",              # PRINER SERVIÇOS INDUSTRIAIS
    "23507": "OUROFINO",            # OUROFINO S.A.
    "25801": "RODOBENS",            # RODOBENS S.A.
    "24821": "REDE D'OR",           # REDE D'OR SÃO LUIZ
    "27049": "ORIZON",
    "22675": "HIDROVIAS",

    # ════════════════════════════════════════════════════════════════
    # EM RECUPERAÇÃO JUDICIAL
    # ════════════════════════════════════════════════════════════════
    "20990": "AMERICANAS (RJ)",
    "19879": "LIGHT (RJ)",
    "12190": "BOMBRIL (RJ)",
    "25658": "AGROGALAXY (RJ)",
    "19100": "CONTAX (RJ)",
    "21342": "OSX (RJ)",
    "12690": "OGX (RJ)",            # OGX PETRÓLEO E GÁS
}

# ─── Critério para incluir empresas recentemente canceladas ───────────────────
# Empresas com SIT=CANCELADA mas que ainda têm dados DFP recentes e podem ter
# debentures/debitos em circulação (relevantes para análise de crédito de RF).
_CANCEL_LOOKBACK_DAYS = 730  # até 2 anos atrás


# ─── Utilitários ──────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Remove acentos e converte para maiúsculas."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.upper()


def _strip_sa(name: str) -> str:
    """Remove designações societárias (S.A., S/A, SA, LTDA, EIRELI) do nome."""
    # S.A. / S.A / SA / S A / S/A  — \b no início, lookahead no fim (evita falso match em SAO etc.)
    cleaned = re.sub(r'[,\s]*\bS\.?\s*/?\s*A\.?(?=[\s,.-]|$)', '', name, flags=re.IGNORECASE)
    # LTDA / EIRELI
    cleaned = re.sub(r'[,\s]*\b(?:LTDA|EIRELI)\.?\b', '', cleaned, flags=re.IGNORECASE)
    # Normaliza espaços e remove separadores soltos no início/fim
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ,.-')
    return cleaned


# Sufixos descritivos que não identificam a marca — removidos automaticamente
# se restarem pelo menos 2 palavras antes deles.
_SHORTEN_RULES: list[re.Pattern[str]] = [r
    for r in [
        re.compile(r'\s+PARTICIPA[ÇC][OÕ]ES?\s+E\s+INVESTIMENTOS?\s*$',   re.I),
        re.compile(r'\s+PARTICIPA[ÇC][OÕ]ES?\s+E\s+EMPREENDIMENTOS?\s*$',  re.I),
        re.compile(r'\s+EMPREENDIMENTOS?\s+E\s+PARTICIPA[ÇC][OÕ]ES?\s*$',  re.I),
        re.compile(r'\s+CONSTRUTORA\s+E\s+INCORPORADORA\s*$',               re.I),
        re.compile(r'\s+ESCOLA\s+DE\s+\S+.*$',                              re.I),
        re.compile(r'\s+SERVI[ÇC]OS\s+M[ÉE]DICOS\s*$',                    re.I),
        re.compile(r'\s+LABORAT[OÓ]RIOS?\s*$',                             re.I),
        re.compile(r'\s+OPERADORA\s+E\s+AG[ÊE]NCIA\s+DE\s+VIAGENS\s*$',   re.I),
        re.compile(r'\s+INFRA-?ESTRUTURA\s+E\s+LOG[ÍI]STICA\s*$',         re.I),
        re.compile(r'\s+CONCESS[OÕ]ES\s+E\s+SERVI[ÇC]OS\s*$',            re.I),
        re.compile(r'\s+LOCA[ÇC][AÃ]O\s+DE\s+CAMINHÕES?,.*$',             re.I),
        re.compile(r'\s+LOCA[ÇC][AÃ]O,\s+LOG[ÍI]STICA\s+E\s+SERVI[ÇC]OS\s*$', re.I),
        re.compile(r'\s+IND[ÚU]STRIA\s+E\s+COM[ÉE]RCIO\s*$',             re.I),
        re.compile(r'\s+IND[ÚU]STRIA\s+FARMAC[ÊE]UTICA\s*$',             re.I),
        re.compile(r'\s+COM[ÉE]RCIO\s+DE\s+PRODUTOS\s+ESPORTIVOS\s*$',    re.I),
        re.compile(r'\s+EDUCA[ÇC][AÃ]O\s*$',                              re.I),
        re.compile(r'\s+HOLDINGS?\s*$',                                     re.I),
        re.compile(r'\s+SANEAMENTO\s+E\s+PARTICIPA[ÇC][OÕ]ES?\s*$',       re.I),
        re.compile(r'\s+SERVI[ÇC]OS\s+DE\s+TELECOMUNICA[ÇC][OÕ]ES?\s*$',  re.I),
        re.compile(r'\s+GESTÃO\s+LOG[ÍI]STICA\s*$',                        re.I),
        re.compile(r'\s+METAIS\s+E\s+CABOS\s*$',                           re.I),
        # "COMPANHIA ... - NOME" → extrai só NOME
        re.compile(r'^(?:COMPANHIA|CIA\.?)\s+\S.*?\s+-\s+',                re.I),
        # Remove prefixo "CIA " ou "CIA. "
        re.compile(r'^CIA\.?\s+',                                           re.I),
        # "- EM RECUPERAÇÃO JUDICIAL" → " (RJ)"  (fallback p/ empresas sem override)
        re.compile(r'\s*-\s*EM\s+RECUPERA[ÇC][AÃ]O\s+JUDICIAL\s*$',       re.I),
    ]
]
# regra especial: "- EM RECUPERAÇÃO JUDICIAL" adiciona sufixo (RJ)
_RJ_RE = re.compile(r'\s*-\s*EM\s+RECUPERA[ÇC][AÃ]O\s+JUDICIAL\s*$', re.I)


def _shorten_name(name: str) -> str:
    """Remove sufixos corporativos descritivos que não identificam a marca."""
    # Recuperação judicial → adiciona (RJ)
    if _RJ_RE.search(name):
        name = _RJ_RE.sub('', name).strip() + ' (RJ)'
        return name.upper()
    for rule in _SHORTEN_RULES:
        candidate = rule.sub('', name).strip(' ,.-')
        # Só aplica se restar pelo menos 1 palavra com ≥ 3 caracteres
        if candidate and len(candidate.split()) >= 1 and any(len(w) >= 3 for w in candidate.split()):
            name = candidate
    return name.upper()


def _is_financeira(setor_norm: str) -> bool:
    return any(k in setor_norm for k in _SETORES_FINANCEIROS_KEYWORDS)


def _is_excluir_sit(sit_norm: str) -> bool:
    return any(k in sit_norm for k in _SIT_EXCLUIR_KEYWORDS)


# ─── Mapeamento SETOR_ATIV → setor_gics ──────────────────────────────────────

def _setor_para_gics(setor_ativ: str) -> str:
    """
    Mapeia SETOR_ATIV do cadastro CVM para o setor interno GICS usado pelo pipeline.
    Remove o prefixo 'Emp. Adm. Part.' antes de classificar.
    """
    s = _norm(setor_ativ)
    # Remove prefixo "EMP ADM PART - " ou "EMP ADM PART"
    s = re.sub(r"^EMP\s+ADM\s+PART\s*[-–]?\s*", "", s).strip()

    # Financeiras (bancos, seguradoras, B3/corretoras, intermediação) → modelo FCFE
    if any(k in s for k in [
        "BANCO", "INTERMEDIACAO FINANCEIRA", "SEGURADORAS", "CORRETORAS",
        "BOLSA", "ARRENDAMENTO MERCANTIL", "CREDITO IMOBILIARIO", "PREVIDENCIA",
        "SECURITIZ",
    ]):
        return "Financeiro"
    if any(k in s for k in ["ENERGIA ELETRICA", "SANEAMENTO", "AGUA E GAS"]):
        return "Utilities"
    if "PETROLEO" in s and "SANEAMENTO" not in s:
        return "Energia"
    if any(k in s for k in [
        "EXTRACAO MINERAL", "METALURGIA", "SIDERURGIA",
        "PAPEL E CELULOSE", "PETROQUIMIC", "BORRACHA",
        "AGRICULTURA", "ACUCAR", "ALCOOL", "CANA",
        "REFLORESTA", "EMBALAGEM",
    ]):
        return "Materiais"
    if any(k in s for k in [
        "TRANSPORTE", "LOGISTICA",
        "MAQUINAS", "MAQS", "EQUIPAMENTOS", "VEICULOS", "PECAS",
    ]):
        return "Industria"
    if any(k in s for k in ["TELECOMUNIC", "COMUNICACAO", "INFORMATICA"]):
        return "Comunicacao"
    if any(k in s for k in ["FARMACEUT", "HIGIENE", "MEDIC"]):
        return "Saude"
    if any(k in s for k in [
        "COMERCIO", "VAREJO", "ATACADO",
        "TEXTIL", "VESTUARIO",
        "BRINQUEDOS", "LAZER",
        "HOSPEDAGEM", "TURISMO",
        "EDUCACAO",
    ]):
        return "Consumo Disc."
    if any(k in s for k in ["ALIMENTOS", "BEBIDAS", "FUMO"]):
        return "Consumo Basico"
    if any(k in s for k in ["CONSTRUCAO", "MAT CONSTR", "DECORACAO", "IMOBILIARIO"]):
        return "Imobiliario"

    # Default para holdings e outros não classificados
    return "Industria"


def _gov_listagem_default(tp_merc: str) -> float:
    """
    Score padrão de governança baseado no tipo de mercado.
    Empresas listadas em BOLSA recebem 3.0 (Nível 1 mínimo) pois não sabemos
    o segmento exato. Não-listadas recebem 2.0.
    """
    return 3.0 if tp_merc.strip().upper() == "BOLSA" else 2.0


# ─── Main ─────────────────────────────────────────────────────────────────────

def build_lista(cache_dir: Path, output: Path) -> None:
    cad_path = cache_dir / "cad_cia_aberta.csv"
    if not cad_path.exists():
        print(f"Cadastro CVM não encontrado: {cad_path}")
        print("Execute primeiro: python main_empresas.py  (baixa o cadastro automaticamente)")
        return

    with cad_path.open("r", encoding="latin-1", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))

    hoje    = date.today()
    cutoff  = hoje - timedelta(days=_CANCEL_LOOKBACK_DAYS)

    def _is_recente_cancelada(r: dict) -> bool:
        """
        Inclui empresa CANCELADA se:
          - cancelamento VOLUNTÁRIO nos últimos 2 anos (pode ter debêntures pendentes)
          - SIT_EMISSOR ainda FASE OPERACIONAL
          - Era listada em BOLSA ou BALCÃO
        """
        if r.get("SIT") != "CANCELADA":
            return False
        if "FASE OPERACIONAL" not in r.get("SIT_EMISSOR", ""):
            return False
        tp = r.get("TP_MERC", "").upper()
        if "BOLSA" not in tp and "BALC" not in tp:
            return False
        dt_cancel = r.get("DT_CANCEL", "").strip()
        if not dt_cancel:
            return False
        try:
            d = date.fromisoformat(dt_cancel)
            return d >= cutoff
        except ValueError:
            return False

    # Filtro base: ativas + Categoria A ou B
    #   OU recentemente canceladas com operação em curso (ex: Atacadão/CRFB3)
    candidatas = [
        r for r in rows
        if r.get("CATEG_REG", "") in ("Categoria A", "Categoria B")
        and (r.get("SIT") == "ATIVO" or _is_recente_cancelada(r))
    ]

    excl_financ = 0
    excl_sit    = 0
    lista       = []

    for r in candidatas:
        setor_ativ  = r.get("SETOR_ATIV", "").strip()
        sit_emissor = r.get("SIT_EMISSOR", "").strip()
        setor_norm  = _norm(setor_ativ)
        sit_norm    = _norm(sit_emissor)
        cd_cvm    = str(int(r["CD_CVM"].strip()))

        # Financeiras: incluídas apenas quando há ticker B3 mapeado (set curado e
        # líquido). Valuation por FCFE/Re em vez de FCFF/WACC (ver indicadores_financeiras).
        # As não mapeadas (securitizadoras, leasing, holdings ilíquidas) seguem fora.
        if _is_financeira(setor_norm) and not _TICKER_B3_MAP.get(cd_cvm):
            excl_financ += 1
            continue

        if _is_excluir_sit(sit_norm):
            excl_sit += 1
            continue

        categ_reg = r.get("CATEG_REG", "")
        tp_merc   = r.get("TP_MERC", "").strip()
        denom_social  = r.get("DENOM_SOCIAL", "").strip()
        denom_comerc  = r.get("DENOM_COMERC", "").strip()
        _raw_nome = _NOME_OVERRIDE.get(cd_cvm) or denom_comerc or denom_social
        if _NOME_OVERRIDE.get(cd_cvm):
            nome = _NOME_OVERRIDE[cd_cvm].upper()   # override já é o nome final
        else:
            nome = _shorten_name(_strip_sa(_raw_nome))
        cnpj      = r.get("CNPJ_CIA", "").strip()
        sit_cvm   = r.get("SIT", "ATIVO").strip()
        setor_gics = _setor_para_gics(setor_ativ)
        ticker_b3  = _TICKER_B3_MAP.get(cd_cvm, "")
        # Empresa mapeada ⇒ tem ticker B3 ⇒ é listada em bolsa. Recupera o TP_MERC
        # quando o cadastro CVM veio em branco (ex.: VITRU/VTRU3, registrada em 2023
        # após migrar da NASDAQ — o cad_cia_aberta deixou TP_MERC vazio).
        if ticker_b3 and not tp_merc:
            tp_merc = "BOLSA"

        # ── Campos adicionais para enriquecer descrições ─────────────────────────
        controle  = r.get("CONTROLE_ACIONARIO", "").strip()
        mun       = r.get("MUN", "").strip()
        uf        = r.get("UF", "").strip()
        dt_const  = (r.get("DT_CONST", "") or "")[:4]   # ano fundação
        dt_reg    = (r.get("DT_REG", "")   or "")[:4]   # ano registro CVM
        auditor   = r.get("AUDITOR", "").strip()

        lista.append({
            "cd_cvm":               cd_cvm,
            "nome":                 nome,
            "denom_social":         denom_social,
            "denom_comerc":         denom_comerc,
            "setor":                setor_gics,
            "setor_cvm":            setor_ativ,
            "categ_reg":            categ_reg,
            "tp_merc":              tp_merc,
            "sit_emissor":          sit_emissor,
            "sit_cvm":              sit_cvm,
            "gov_listagem_default": _gov_listagem_default(tp_merc),
            "cnpj":                 cnpj,
            "ticker_b3":            ticker_b3,
            "controle_acionario":   controle,
            "mun":                  mun,
            "uf":                   uf,
            "dt_const":             dt_const,
            "dt_reg":               dt_reg,
            "auditor":              auditor,
        })

    # ── Deduplica por cd_cvm (CVM pode ter múltiplas linhas por empresa) ─────────
    # Preferência: ATIVO > CANCELADA; BOLSA > BALCÃO; qualquer outra
    _MERC_RANK = {"BOLSA": 2, "BALCÃO ORGANIZADO": 1, "BALCAO ORGANIZADO": 1}
    seen_cds: dict[str, dict] = {}
    for item in lista:
        cd = item["cd_cvm"]
        if cd not in seen_cds:
            seen_cds[cd] = item
        else:
            existing = seen_cds[cd]
            # Prefere ATIVO sobre CANCELADA
            if existing["sit_cvm"] == "CANCELADA" and item["sit_cvm"] == "ATIVO":
                seen_cds[cd] = item
            # Entre mesmo status, prefere TP_MERC de maior rank
            elif existing["sit_cvm"] == item["sit_cvm"]:
                r_new = _MERC_RANK.get(item["tp_merc"].upper(), 0)
                r_old = _MERC_RANK.get(existing["tp_merc"].upper(), 0)
                if r_new > r_old:
                    seen_cds[cd] = item
    lista = list(seen_cds.values())
    lista.sort(key=lambda x: x["nome"])

    # ── Escreve CSV ────────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cd_cvm", "nome", "denom_social", "denom_comerc",
        "setor", "setor_cvm",
        "categ_reg", "tp_merc", "sit_emissor", "sit_cvm",
        "gov_listagem_default", "cnpj", "ticker_b3",
        "controle_acionario", "mun", "uf",
        "dt_const", "dt_reg", "auditor",
    ]
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(lista)

    # ── Resumo ─────────────────────────────────────────────────────────────────
    cat_a     = sum(1 for e in lista if e["categ_reg"] == "Categoria A")
    cat_b     = sum(1 for e in lista if e["categ_reg"] == "Categoria B")
    bolsa     = sum(1 for e in lista if e["tp_merc"] == "BOLSA")
    balcao    = sum(1 for e in lista if "BALC" in e["tp_merc"].upper())
    setores   = Counter(e["setor"] for e in lista)
    recup     = sum(1 for e in lista if "RECUPERA" in e["sit_emissor"].upper())
    cancelada = sum(1 for e in lista if e["sit_cvm"] == "CANCELADA")

    print(f"\nEmpresas na lista:              {len(lista)}")
    print(f"  Categoria A (registradas):    {cat_a}")
    print(f"  Categoria B (não-listadas):   {cat_b}")
    print(f"  Listadas B3 (BOLSA):          {bolsa}")
    print(f"  Balcão (OTC):                 {balcao}")
    print(f"  Em recuperação judicial:      {recup}  (incluídas — úteis p/ crédito)")
    print(f"  Canceladas recentes (<=2a):   {cancelada}  (incluidas - podem ter debentures)")
    print(f"\nExcluídas financeiras:      {excl_financ}")
    print(f"Excluídas não-operacionais: {excl_sit}")
    print(f"\nDistribuição por setor:")
    for s, n in sorted(setores.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {s}")
    print(f"\nArquivo gerado: {output.resolve()}")


def main() -> None:
    p = argparse.ArgumentParser(description="Gera empresas_lista.csv do cadastro CVM")
    p.add_argument("--cache-dir", type=Path, default=Path("cache_cvm"),
                   help="Diretório com cad_cia_aberta.csv (default: ./cache_cvm)")
    p.add_argument("--output", type=Path, default=Path("empresas_lista.csv"),
                   help="Caminho do CSV de saída (default: ./empresas_lista.csv)")
    args = p.parse_args()
    build_lista(args.cache_dir, args.output)


if __name__ == "__main__":
    main()
