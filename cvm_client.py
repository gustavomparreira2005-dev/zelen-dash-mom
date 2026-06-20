# ============================================================================
# AVISO: CÓPIA do projeto de crédito ("1 Renda Fixa/Dash - Credito Privado").
# A fonte de verdade deste módulo é aquele projeto. Melhorias na metodologia
# de crédito NÃO se propagam automaticamente para cá — re-sincronize à mão.
# ============================================================================

"""
CVM Client — dados de não-financeiras para análise de crédito.
Baixa DFP (anual) e ITR (trimestral) da CVM Open Data e retorna
demonstrativos pivotados por conta × período para um lote de empresas.

Arquitetura batch: cada ZIP é aberto exatamente uma vez para extrair dados
de TODAS as empresas simultaneamente — O(ZIPs) em vez de O(N × ZIPs).
"""

from __future__ import annotations

import csv
import io
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ─── URLs CVM Open Data ────────────────────────────────────────────────────────
_CAD_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
_DFP_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip"
_ITR_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{year}.zip"

STATEMENTS = ("DRE", "BPA", "BPP", "DFC_MI", "DVA")

# ─── Empresas do MVP ───────────────────────────────────────────────────────────
# query: substring para busca no cadastro CVM (DENOM_SOCIAL normalizada)
EMPRESAS_MVP: Dict[str, Dict] = {
    # cd_cvm: código CVM direto — evita ambiguidade no match fuzzy
    "VALE3": {"nome": "Vale",          "setor": "Materiais",        "query": "VALE S A",                          "cd_cvm": "4170"},
    "PETR4": {"nome": "Petrobras",     "setor": "Energia",          "query": "PETROLEO BRASILEIRO",               "cd_cvm": "9512"},
    "WEGE3": {"nome": "WEG",           "setor": "Industria",        "query": "WEG S A",                           "cd_cvm": "5410"},
    "ABEV3": {"nome": "Ambev",         "setor": "Consumo Basico",   "query": "AMBEV S A",                         "cd_cvm": "23264"},
    "MGLU3": {"nome": "Magalu",        "setor": "Consumo Disc.",    "query": "MAGAZINE LUIZA",                    "cd_cvm": "22470"},
    "RADL3": {"nome": "Raia Drogasil", "setor": "Saude",            "query": "RAIA DROGASIL",                     "cd_cvm": "5258"},
    "VIVT3": {"nome": "Vivo",          "setor": "Comunicacao",      "query": "TELEFONICA BRASIL",                 "cd_cvm": "17671"},
    "SBSP3": {"nome": "Sabesp",        "setor": "Utilities",        "query": "SANEAMENTO BASICO ESTADO SAO PAULO","cd_cvm": "14443"},
    "CYRE3": {"nome": "Cyrela",        "setor": "Imobiliario",      "query": "CYRELA BRAZIL REALTY",              "cd_cvm": "14460"},
    "RENT3": {"nome": "Localiza",      "setor": "Locação de Frota", "query": "LOCALIZA RENT A CAR",               "cd_cvm": "19739"},
}

# Mapeamento setor → ajuste setorial usado no rating engine
SETOR_AJUSTE = {
    "Utilities":         "utilities",
    "Imobiliario":       "real_estate",
    "Energia":           "commodities",
    "Materiais":         "commodities",
    "Consumo Disc.":     "varejo",
    "Locação de Frota":  "default",
    "Consumo Basico":    "consumo_basico",  # ciclo de caixa negativo (vende cash, paga 30-60d)
    "Industria":         "default",
    "Saude":             "default",
    "Comunicacao":       "comunicacao",    # telcos e SaaS — capex de rede 15-25% receita é estrutural, não é sinal de stress
}


# ─── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class DemosPivot:
    """
    Demonstrativo pivotado: CD_CONTA → {DT_REFER (YYYY-MM-DD) → valor}.
    Mantém também as descrições e a escala monetária.
    """
    pivot: Dict[str, Dict[str, float]] = field(default_factory=dict)
    descricoes: Dict[str, str] = field(default_factory=dict)   # CD_CONTA → DS_CONTA
    escala: int = 1000  # 1000 = MIL, 1 = UNIDADE

    def get(self, cd_conta: str, dt: str, default: float = 0.0) -> float:
        """Retorna o valor de uma conta em uma data, ou default."""
        return self.pivot.get(cd_conta, {}).get(dt, default)

    def find_under(self, parent: str, pattern: str, dt: str) -> float:
        """
        Soma sub-contas de `parent` cujo DS_CONTA casa com `pattern`.
        Evita dupla contagem: se pai e filho casam, conta só o pai.
        """
        rx = re.compile(pattern, re.IGNORECASE)
        # Candidatos: CD_CONTA começando com parent (inclui o próprio parent)
        cands = [
            cd for cd, desc in self.descricoes.items()
            if (cd == parent or cd.startswith(parent + ".")) and rx.search(desc)
        ]
        if not cands:
            return 0.0
        # Ordenar do mais agregado para o mais específico
        cands.sort(key=lambda c: c.count("."))
        counted: set = set()
        total = 0.0
        for cd in cands:
            # Pular se já contamos um pai desse conta
            if any(cd.startswith(p + ".") for p in counted):
                continue
            val = self.pivot.get(cd, {}).get(dt)
            if val is not None:
                total += val
                counted.add(cd)
        return total

    def debt_ex_lease(self, parent: str, debt_pat: str, lease_pat: str,
                      dt: str) -> float:
        """
        Soma a dívida financeira sob `parent` (empréstimos/financiamentos/
        debêntures), excluindo passivos de arrendamento (IFRS 16).

        Diferença vs subtrair o arrendamento total da árvore: aqui o lease é
        descontado APENAS quando está contido dentro de um nó de dívida contado
        (dupla contagem do agregado). Empresas que classificam o lease fora da
        conta de dívida — ex.: Drogasil, com "Passivo de Arrendamento" sob
        "Outras Obrigações" (2.01.05) em vez de "Empréstimos e Financiamentos"
        (2.01.04) — não têm a dívida zerada indevidamente.
        """
        rx_d = re.compile(debt_pat, re.IGNORECASE)
        rx_l = re.compile(lease_pat, re.IGNORECASE)

        # Nós de dívida: casam o padrão de dívida e NÃO são, eles mesmos, leases
        # (ex.: "Financiamento por Arrendamento" casa "financiamento" mas é lease).
        cands = [
            cd for cd, desc in self.descricoes.items()
            if (cd == parent or cd.startswith(parent + "."))
            and rx_d.search(desc) and not rx_l.search(desc)
        ]
        if not cands:
            return 0.0
        cands.sort(key=lambda c: c.count("."))

        total = 0.0
        counted: List[str] = []
        for cd in cands:
            if any(cd.startswith(p + ".") for p in counted):
                continue
            val = self.pivot.get(cd, {}).get(dt)
            if val is None:
                continue
            total += val
            counted.append(cd)
            # Desconta o arrendamento embutido neste nó de dívida (sem dupla
            # contagem entre lease-pai e lease-filho).
            lease_cands = sorted(
                (c for c, d in self.descricoes.items()
                 if c.startswith(cd + ".") and rx_l.search(d)),
                key=lambda c: c.count("."),
            )
            lc_counted: List[str] = []
            for lc in lease_cands:
                if any(lc.startswith(p + ".") for p in lc_counted):
                    continue
                lv = self.pivot.get(lc, {}).get(dt)
                if lv is not None:
                    total -= lv
                    lc_counted.append(lc)
        return total

    def first_match(self, parent: str, pattern: str, dt: str) -> Optional[float]:
        """Retorna o primeiro valor encontrado (conta mais agregada) que casa com o padrão."""
        rx = re.compile(pattern, re.IGNORECASE)
        cands = [
            (cd, desc) for cd, desc in self.descricoes.items()
            if (cd == parent or cd.startswith(parent + ".")) and rx.search(desc)
        ]
        if not cands:
            return None
        cands.sort(key=lambda x: x[0].count("."))
        for cd, _ in cands:
            val = self.pivot.get(cd, {}).get(dt)
            if val is not None:
                return val
        return None

    def get_pos_neg_children(self, parent: str, dt: str) -> tuple:
        """
        Retorna (soma_positivos, soma_negativos) para filhos diretos de parent em dt.
        Considera apenas contas exatamente um nível abaixo do parent (ex: 6.03 → 6.03.01, 6.03.02).
        Útil para separar captação (inflows positivos) de pagamentos (outflows negativos) no FCF.
        """
        parent_depth = parent.count(".")
        pos = 0.0
        neg = 0.0
        for cd, time_dict in self.pivot.items():
            if not cd.startswith(parent + "."):
                continue
            if cd.count(".") != parent_depth + 1:
                continue
            v = time_dict.get(dt)
            if v is None:
                continue
            if v > 0:
                pos += v
            else:
                neg += v
        return pos, neg


@dataclass
class EmpresaData:
    ticker: str
    cd_cvm: str
    nome: str
    cnpj: str
    categ_reg: str            # "Categoria A" ou "Categoria B" (CVM)
    setor_gics: str
    ajuste_setor: str = "default"
    tp_merc: str = ""         # "BOLSA", "BALCÃO ORGANIZADO", "BALCÃO NÃO ORGANIZADO" ou ""
    ticker_b3: str = ""       # Ticker B3 quando diferente do cd_cvm (ex: "ASAI3", "CRFB3")
    sit_cvm: str = "ATIVO"    # "ATIVO" ou "CANCELADA" (empresas canceladas recentes inclusas)
    # ── Metadados ricos para descrições ────────────────────────────────────────
    denom_social: str = ""    # Razão social completa
    denom_comerc: str = ""    # Nome de pregão / marca
    setor_cvm: str = ""       # SETOR_ATIV granular (ex: "Petróleo e Gás")
    controle_acionario: str = ""  # ESTATAL / PRIVADO NACIONAL / ESTRANGEIRO / etc
    mun: str = ""             # Município da sede
    uf: str = ""              # UF da sede
    dt_const: str = ""        # Ano de constituição
    dt_reg: str = ""          # Ano de registro CVM
    auditor: str = ""         # Empresa de auditoria
    sit_emissor: str = ""     # Status como emissor (FASE OPERACIONAL, etc)
    # ── Demonstrações ──────────────────────────────────────────────────────────
    dfp: Dict[str, DemosPivot] = field(default_factory=dict)   # "DRE" → DemosPivot
    itr: Dict[str, DemosPivot] = field(default_factory=dict)
    anos_dfp: List[str] = field(default_factory=list)          # ["2022-12-31", ...]
    trimestres_itr: List[str] = field(default_factory=list)
    # Data de divulgação (recebimento CVM) por período — point-in-time / backtest
    receb_dfp: Dict[str, str] = field(default_factory=dict)    # {"2024-12-31": "2025-02-19"}
    receb_itr: Dict[str, str] = field(default_factory=dict)
    erro: Optional[str] = None

    def ano(self, n: int = 0) -> Optional[str]:
        """n=0 → mais recente, n=1 → anterior, etc."""
        idx = len(self.anos_dfp) - 1 - n
        return self.anos_dfp[idx] if 0 <= idx < len(self.anos_dfp) else None

    def ultimo_itr(self) -> Optional[str]:
        return self.trimestres_itr[-1] if self.trimestres_itr else None

    def _divulgado_ate(self, periodo: str, fonte: str, ate: str) -> bool:
        """
        True se o período (DT_REFER do balanço) já estava público na data `ate`.
        Usa DT_RECEB quando disponível; senão assume defasagem padrão sobre o fim do
        período (DFP ~90 dias, ITR ~45 dias) — conservador para evitar look-ahead.
        """
        receb = (self.receb_dfp if fonte == "dfp" else self.receb_itr).get(periodo)
        if receb:
            return receb <= ate
        from datetime import date as _date, timedelta as _td
        try:
            fim = _date.fromisoformat(periodo[:10])
        except ValueError:
            return False
        lag = 90 if fonte == "dfp" else 45
        return (fim + _td(days=lag)).isoformat() <= ate

    def as_of(self, ate: str) -> "EmpresaData":
        """
        Retorna uma cópia da empresa "como conhecida" na data `ate` (YYYY-MM-DD):
        anos_dfp e trimestres_itr filtrados aos períodos já divulgados até essa data.
        Os pivots são compartilhados (somente leitura) — só as listas de períodos mudam,
        e as funções de score consultam exclusivamente datas dessas listas.
        """
        import dataclasses
        anos  = [d for d in self.anos_dfp       if self._divulgado_ate(d, "dfp", ate)]
        trims = [d for d in self.trimestres_itr if self._divulgado_ate(d, "itr", ate)]
        return dataclasses.replace(self, anos_dfp=anos, trimestres_itr=trims)


# ─── Utilitários internos ──────────────────────────────────────────────────────

def _log(msg: str, end: str = "\n") -> None:
    print(msg, end=end, file=sys.stderr, flush=True)


def _norm_id(val: str) -> str:
    """Normaliza CD_CVM removendo zeros à esquerda."""
    if val and val.strip().isdigit():
        return str(int(val.strip()))
    return (val or "").strip()


def _normalize(text: str) -> str:
    """Remove acentos, converte para maiúsculas, remove sufixos societários."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper()
    text = re.sub(r"\b(SA|S A|S/A|S\.A\.|ON|PN|PNA|PNB|UNT|UNIT|N1|N2|NM)\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    return text


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


def _iter_zip_csv(zip_path: Path, member: str):
    """Itera as linhas de um CSV dentro de um ZIP (abre o ZIP internamente)."""
    with zipfile.ZipFile(zip_path) as zf:
        if member not in zf.namelist():
            return
        with zf.open(member) as raw:
            handle = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            yield from csv.DictReader(handle, delimiter=";")


def _iter_member(zf: zipfile.ZipFile, member: str):
    """Itera as linhas de um CSV dentro de um ZipFile já aberto."""
    if member not in zf.namelist():
        return
    with zf.open(member) as raw:
        handle = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        yield from csv.DictReader(handle, delimiter=";")


def _check_ordem(row: Dict) -> bool:
    """Retorna True se ORDEM_EXERC == 'ULTIMO' (normaliza Unicode/acentos)."""
    ordem_raw = re.sub(r"\s+", "", row.get("ORDEM_EXERC", "") or "").upper()
    ordem = "".join(
        c for c in unicodedata.normalize("NFKD", ordem_raw)
        if not unicodedata.combining(c)
    )
    return ordem == "ULTIMO"


def _parse_float(val: str) -> Optional[float]:
    if not val or not val.strip():
        return None
    try:
        return float(Decimal(val.strip()))
    except InvalidOperation:
        return None


# ─── Registro de companhias CVM ────────────────────────────────────────────────

def _load_cad(cache_dir: Path) -> List[Dict[str, str]]:
    path = cache_dir / "cad_cia_aberta.csv"
    _download(_CAD_URL, path)
    with path.open("r", encoding="latin-1", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _score_match(query: str, row: Dict[str, str]) -> float:
    qn = _normalize(query)
    best = 0.0
    for campo in ("DENOM_SOCIAL", "DENOM_COMERC"):
        cand = _normalize(row.get(campo, ""))
        if not cand:
            continue
        if qn == cand:
            score = 1.0
        elif qn in cand or cand in qn:
            score = 0.95
        else:
            ratio = SequenceMatcher(None, qn, cand).ratio()
            qt, ct = set(qn.split()), set(cand.split())
            overlap = len(qt & ct) / max(len(qt), 1)
            score = ratio * 0.8 + overlap * 0.2
        if row.get("SIT") == "ATIVO":
            score += 0.01
        best = max(best, score)
    return best


def resolve_tickers(tickers: List[str], cache_dir: Path) -> Dict[str, Dict[str, str]]:
    """
    Mapeia cada ticker para a linha do cadastro CVM.
    Se cd_cvm estiver em EMPRESAS_MVP, faz lookup direto (mais confiável).
    Caso contrário, usa busca fuzzy pelo nome.
    """
    _log("Carregando cadastro CVM…")
    rows = _load_cad(cache_dir)
    # Índice por cd_cvm para lookup direto O(1)
    by_cd = {_norm_id(r["CD_CVM"]): r for r in rows if r.get("CD_CVM")}

    result: Dict[str, Dict[str, str]] = {}
    for ticker in tickers:
        meta = EMPRESAS_MVP.get(ticker, {})
        cd_fixo = meta.get("cd_cvm", "")
        if cd_fixo and cd_fixo in by_cd:
            row = by_cd[cd_fixo]
            result[ticker] = row
            _log(f"  ✓ {ticker} → {row['DENOM_SOCIAL']} (cd_cvm={cd_fixo})")
            continue

        # Fallback: busca fuzzy
        query = meta.get("query", ticker)
        ranked = sorted(rows, key=lambda r: _score_match(query, r), reverse=True)
        top = ranked[0] if ranked else None
        if top and _score_match(query, top) >= 0.60:
            result[ticker] = top
            cd = _norm_id(top["CD_CVM"])
            _log(f"  ✓ {ticker} → {top['DENOM_SOCIAL']} (cd_cvm={cd}) [fuzzy]")
        else:
            _log(f"  ✗ {ticker}: não encontrado")
    return result


# ─── Extração batch de demonstrativos ─────────────────────────────────────────

def _pivot(raw_rows: List[Dict]) -> DemosPivot:
    """Converte linhas brutas em DemosPivot {CD_CONTA → {DT_REFER → valor}}."""
    pivot: Dict[str, Dict[str, float]] = {}
    pivot_ini: Dict[str, Dict[str, str]] = {}  # DT_INI_EXERC vencedor por (cd, dt)
    desc_counts: Dict[str, Counter] = {}
    is_unidade = False

    for row in raw_rows:
        cd = row.get("CD_CONTA", "")
        dt = (row.get("DT_REFER", "") or "")[:10]
        if not cd or not dt:
            continue
        val = _parse_float(row.get("VL_CONTA", ""))
        if val is None:
            continue
        # Quando há duas linhas para o mesmo (CD_CONTA, DT_REFER) — ex: empresas que
        # reportam tanto o acumulado YTD (DT_INI=jan) quanto o trimestre isolado
        # (DT_INI=abr/jul/out) — prefere o de DT_INI_EXERC mais cedo (= YTD cumulativo).
        ini = (row.get("DT_INI_EXERC", "") or "")[:10]
        existing_ini = pivot_ini.get(cd, {}).get(dt)
        if existing_ini is not None and ini and ini >= existing_ini:
            # Linha atual cobre período mais curto: descarta em favor do YTD já armazenado
            desc_counts.setdefault(cd, Counter())[row.get("DS_CONTA", "")] += 1
            continue
        pivot.setdefault(cd, {})[dt] = val
        pivot_ini.setdefault(cd, {})[dt] = ini
        desc_counts.setdefault(cd, Counter())[row.get("DS_CONTA", "")] += 1
        if row.get("ESCALA_MOEDA", "") == "UNIDADE":
            is_unidade = True

    descricoes = {cd: c.most_common(1)[0][0] for cd, c in desc_counts.items()}
    return DemosPivot(pivot=pivot, descricoes=descricoes, escala=1 if is_unidade else 1000)


def _extract_zip(
    zip_path: Path,
    prefix: str,           # "dfp" ou "itr"
    year: int,
    target_cds: Set[str],  # cd_cvm normalizados a extrair
) -> Tuple[Dict[str, Dict[str, List]], Dict[str, Set[str]], Dict[str, Dict[str, str]]]:
    """
    Abre um ZIP exatamente uma vez e extrai demonstrativos para TODAS as empresas.

    Lógica de prioridade por (stmt, scope):
      · stmt → (stmt_con, stmt_ind) — prefere consolidado
      · DFC_MI sem dados → tenta DFC_MD (con, ind) como fallback

    Returns:
        acc   — {cd_cvm: {stmt_name: [rows]}}
        dates — {cd_cvm: set(DT_REFER)}  (datas com documentos no índice)
        receb — {cd_cvm: {DT_REFER: DT_RECEB}}  (data de divulgação por período)
    """
    index_member = f"{prefix}_cia_aberta_{year}.csv"

    # ── Passo 1: índice — versão mais recente por empresa × data ──────────────
    allowed: Dict[str, Dict[str, Dict]] = {}  # {cd: {dt: doc_row}}
    dates:   Dict[str, Set[str]]        = {}  # {cd: set(dt)}
    receb:   Dict[str, Dict[str, str]]  = {}  # {cd: {dt[:10]: DT_RECEB[:10]}}

    with zipfile.ZipFile(zip_path) as zf:
        namelist = set(zf.namelist())

        for row in _iter_member(zf, index_member):
            cd = _norm_id(row.get("CD_CVM", ""))
            if cd not in target_cds:
                continue
            dt = row.get("DT_REFER", "")
            if not dt:
                continue
            v = int(row.get("VERSAO", "0") or 0)
            if cd not in allowed:
                allowed[cd] = {}
                dates[cd] = set()
                receb[cd] = {}
            if dt not in allowed[cd] or v > int(allowed[cd][dt].get("VERSAO", "0")):
                allowed[cd][dt] = row
                dates[cd].add(dt[:10])
                # Data de divulgação (recebimento CVM) da versão vencedora
                receb[cd][dt[:10]] = (row.get("DT_RECEB", "") or "")[:10]

        # Empresas presentes neste ZIP (com pelo menos um documento)
        present: Set[str] = set(allowed.keys())

        # ── Passo 2: demonstrativos ────────────────────────────────────────────
        acc:      Dict[str, Dict[str, List]] = {cd: {s: [] for s in STATEMENTS} for cd in present}
        has_data: Dict[str, Set[str]]        = {cd: set() for cd in present}

        for stmt in STATEMENTS:
            # Ordem de tentativa: stmt_con → stmt_ind → DFC_MD_con → DFC_MD_ind
            scope_variants: List[Tuple[str, str]] = [(stmt, "con"), (stmt, "ind")]
            if stmt == "DFC_MI":
                scope_variants += [("DFC_MD", "con"), ("DFC_MD", "ind")]

            for variant, scope in scope_variants:
                # Empresas que ainda não têm dados para este stmt neste ZIP
                need = {cd for cd in present if stmt not in has_data[cd]}
                if not need:
                    break  # Todas preenchidas → pular variantes restantes

                member = f"{prefix}_cia_aberta_{variant}_{scope}_{year}.csv"
                if member not in namelist:
                    continue

                rows_added: Set[str] = set()
                for row in _iter_member(zf, member):
                    cd = _norm_id(row.get("CD_CVM", ""))
                    if cd not in need:
                        continue
                    dt = row.get("DT_REFER", "")
                    doc = allowed[cd].get(dt)
                    if not doc:
                        continue
                    if row.get("VERSAO", "") != doc.get("VERSAO", ""):
                        continue
                    if not _check_ordem(row):
                        continue
                    acc[cd][stmt].append(row)
                    rows_added.add(cd)

                # Marca empresas que obtiveram dados COM valores não-nulos → não serão tentadas
                # com próxima variante (con→ind).  Se o DFP consolidado tiver ÚLTIMO=0 em todas
                # as contas (ex: TIM S.A. após fusão de subsidiárias), a empresa permanece em
                # 'need' e o fallback ind será usado no próximo loop.
                for cd in rows_added:
                    has_nonzero = any(
                        (float(r.get("VL_CONTA") or 0) != 0.0)
                        for r in acc[cd][stmt]
                    )
                    if has_nonzero:
                        has_data[cd].add(stmt)

    # Completar empresas ausentes neste ZIP com listas vazias
    for cd in target_cds:
        if cd not in acc:
            acc[cd] = {s: [] for s in STATEMENTS}

    return acc, dates, receb


def _extract_all(
    target_cds: Set[str],
    dfp_zips: Dict[int, Path],
    itr_zips: Dict[int, Path],
) -> Dict[str, Tuple[Dict[str, DemosPivot], Dict[str, DemosPivot], List[str], List[str]]]:
    """
    Extrai e pivota demonstrativos para todas as empresas em batch.
    Complexidade: O(ZIPs) em vez de O(N × ZIPs).

    Returns: {cd_cvm: (dfp_pivots, itr_pivots, sorted_dfp_dates, sorted_itr_dates,
                       dfp_receb, itr_receb)}
    """
    dfp_raw:   Dict[str, Dict[str, List]] = {cd: {s: [] for s in STATEMENTS} for cd in target_cds}
    itr_raw:   Dict[str, Dict[str, List]] = {cd: {s: [] for s in STATEMENTS} for cd in target_cds}
    dfp_dates: Dict[str, Set[str]]        = {cd: set() for cd in target_cds}
    itr_dates: Dict[str, Set[str]]        = {cd: set() for cd in target_cds}
    dfp_receb: Dict[str, Dict[str, str]]  = {cd: {} for cd in target_cds}
    itr_receb: Dict[str, Dict[str, str]]  = {cd: {} for cd in target_cds}

    all_zips = (
        [("dfp", y, p) for y, p in sorted(dfp_zips.items())] +
        [("itr", y, p) for y, p in sorted(itr_zips.items())]
    )
    total  = len(all_zips)
    bar_w  = 20

    for i, (prefix, year, zip_path) in enumerate(all_zips, 1):
        filled = int(bar_w * i / total)
        bar    = "█" * filled + "░" * (bar_w - filled)
        _log(f"  {bar} {i}/{total}  {zip_path.name}  ", end="\r")

        try:
            acc, dates, receb = _extract_zip(zip_path, prefix, year, target_cds)
        except Exception as exc:
            _log(f"\n  ⚠ Erro em {zip_path.name}: {exc}")
            continue

        raw_map   = dfp_raw   if prefix == "dfp" else itr_raw
        dates_map = dfp_dates if prefix == "dfp" else itr_dates
        receb_map = dfp_receb if prefix == "dfp" else itr_receb

        for cd in target_cds:
            for stmt in STATEMENTS:
                raw_map[cd][stmt].extend(acc[cd][stmt])
            if cd in dates:
                dates_map[cd].update(dates[cd])
            if cd in receb:
                receb_map[cd].update(receb[cd])

    _log("")  # newline após barra de progresso

    return {
        cd: (
            {s: _pivot(dfp_raw[cd][s]) for s in STATEMENTS},
            {s: _pivot(itr_raw[cd][s]) for s in STATEMENTS},
            sorted(dfp_dates[cd]),
            sorted(itr_dates[cd]),
            dfp_receb[cd],
            itr_receb[cd],
        )
        for cd in target_cds
    }


# ─── Entry point público ───────────────────────────────────────────────────────

def load_companies(
    tickers: Optional[List[str]] = None,
    anos_dfp: int = 4,
    cache_dir: Path = Path("cache_cvm"),
    force_download: bool = False,
) -> Dict[str, EmpresaData]:
    """
    Carrega demonstrativos DFP e ITR para uma lista de tickers.

    Args:
        tickers:        lista de tickers; None = todos os EMPRESAS_MVP
        anos_dfp:       anos de DFP a baixar (padrão 4 → 2022-2025)
        cache_dir:      pasta local de cache dos ZIPs
        force_download: ignora cache e baixa novamente

    Returns:
        dict {ticker: EmpresaData}
    """
    if tickers is None:
        tickers = list(EMPRESAS_MVP.keys())

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    hoje       = date.today()
    ano_atual  = hoje.year
    ultimo_dfp = ano_atual if hoje.month >= 5 else ano_atual - 1
    dfp_years  = list(range(ultimo_dfp - anos_dfp + 1, ultimo_dfp + 1))
    # ITR acompanha a janela do DFP (habilita série LTM trimestral profunda; precisa
    # dos comparativos YTD do mesmo trimestre do ano anterior p/ LTM = FY+YTD_t−YTD_t-1ano).
    # Mínimo de 4 anos para preservar o comportamento padrão (--anos pequeno).
    _itr_ini   = min(dfp_years[0], ano_atual - 3)
    itr_years  = list(range(_itr_ini, ano_atual + 1))

    # ── Resolve tickers → registro CVM ────────────────────────────────────────
    registry = resolve_tickers(tickers, cache_dir)

    # ── Download dos ZIPs ─────────────────────────────────────────────────────
    _log(f"\nDFP: anos {dfp_years[0]}-{dfp_years[-1]}")
    dfp_zips: Dict[int, Path] = {}
    for year in dfp_years:
        p = _download(
            _DFP_URL.format(year=year),
            cache_dir / "dfp" / f"dfp_cia_aberta_{year}.zip",
            force=force_download,
        )
        if p:
            dfp_zips[year] = p

    _log(f"ITR: anos {itr_years}")
    itr_zips: Dict[int, Path] = {}
    for year in itr_years:
        p = _download(
            _ITR_URL.format(year=year),
            cache_dir / "itr" / f"itr_cia_aberta_{year}.zip",
            force=force_download,
        )
        if p:
            itr_zips[year] = p

    # ── Coleta cd_cvm de todas as empresas resolvidas ─────────────────────────
    cd_to_ticker: Dict[str, str] = {}
    for ticker in tickers:
        reg = registry.get(ticker)
        if reg:
            cd = _norm_id(reg["CD_CVM"])
            cd_to_ticker[cd] = ticker
    target_cds: Set[str] = set(cd_to_ticker.keys())

    # ── Extração batch: O(ZIPs) em vez de O(N × ZIPs) ────────────────────────
    _log(f"\nExtraindo {len(tickers)} empresas…")
    all_data = _extract_all(target_cds, dfp_zips, itr_zips)

    # ── Monta EmpresaData por ticker ──────────────────────────────────────────
    results: Dict[str, EmpresaData] = {}

    for ticker in tickers:
        meta = EMPRESAS_MVP.get(ticker, {})
        reg  = registry.get(ticker)

        if not reg:
            results[ticker] = EmpresaData(
                ticker=ticker, cd_cvm="", nome=meta.get("nome", ticker),
                cnpj="", categ_reg="", setor_gics=meta.get("setor", ""),
                erro="Não resolvido no cadastro CVM",
            )
            continue

        cd_cvm = _norm_id(reg["CD_CVM"])
        nome   = reg.get("DENOM_COMERC") or reg.get("DENOM_SOCIAL", ticker)
        setor  = meta.get("setor", "")
        ajuste = SETOR_AJUSTE.get(setor, "default")

        # Fallback seguro caso cd_cvm não estivesse em target_cds
        dfp, itr, anos, trims, receb_d, receb_i = all_data.get(cd_cvm, (
            {s: DemosPivot() for s in STATEMENTS},
            {s: DemosPivot() for s in STATEMENTS},
            [], [], {}, {},
        ))

        nome_curto = nome[:30] if len(nome) > 30 else nome
        _log(f"  {ticker} ({nome_curto})… {len(anos)}a DFP / {len(trims)}q ITR ✓")

        results[ticker] = EmpresaData(
            ticker=ticker, cd_cvm=cd_cvm, nome=nome,
            cnpj=reg.get("CNPJ_CIA", ""),
            categ_reg=reg.get("CATEG_REG", ""),
            tp_merc=reg.get("TP_MERC", ""),
            setor_gics=setor, ajuste_setor=ajuste,
            setor_cvm=reg.get("SETOR_ATIV", ""),
            controle_acionario=reg.get("CONTROLE_ACIONARIO", ""),
            auditor=reg.get("AUDITOR", ""),
            sit_emissor=reg.get("SIT_EMISSOR", ""),
            dfp=dfp, itr=itr,
            anos_dfp=anos, trimestres_itr=trims,
            receb_dfp=receb_d, receb_itr=receb_i,
        )

    return results


# ─── Carregamento em bulk (lista completa sem ticker) ─────────────────────────

def load_companies_bulk(
    empresa_list: List[Dict],
    anos_dfp: int = 4,
    cache_dir: Path = Path("cache_cvm"),
    force_download: bool = False,
) -> Dict[str, "EmpresaData"]:
    """
    Carrega demonstrativos para uma lista arbitrária de empresas (Categoria A + B).
    Usa cd_cvm como identificador primário (ticker) no dict de retorno.

    Args:
        empresa_list:   lista de dicts com chaves obrigatórias:
                        cd_cvm, nome, setor, categ_reg, tp_merc
                        e opcional: gov_listagem_default, cnpj
        anos_dfp:       anos de DFP a baixar
        cache_dir:      pasta de cache dos ZIPs
        force_download: ignora cache e baixa novamente

    Returns:
        dict {cd_cvm: EmpresaData}
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    hoje       = date.today()
    ano_atual  = hoje.year
    ultimo_dfp = ano_atual if hoje.month >= 5 else ano_atual - 1
    dfp_years  = list(range(ultimo_dfp - anos_dfp + 1, ultimo_dfp + 1))
    # ITR acompanha a janela do DFP (habilita série LTM trimestral profunda; precisa
    # dos comparativos YTD do mesmo trimestre do ano anterior p/ LTM = FY+YTD_t−YTD_t-1ano).
    # Mínimo de 4 anos para preservar o comportamento padrão (--anos pequeno).
    _itr_ini   = min(dfp_years[0], ano_atual - 3)
    itr_years  = list(range(_itr_ini, ano_atual + 1))

    # ── Download dos ZIPs ─────────────────────────────────────────────────────
    _log(f"\nDFP: anos {dfp_years[0]}-{dfp_years[-1]}")
    dfp_zips: Dict[int, Path] = {}
    for year in dfp_years:
        p = _download(
            _DFP_URL.format(year=year),
            cache_dir / "dfp" / f"dfp_cia_aberta_{year}.zip",
            force=force_download,
        )
        if p:
            dfp_zips[year] = p

    _log(f"ITR: anos {itr_years}")
    itr_zips: Dict[int, Path] = {}
    for year in itr_years:
        p = _download(
            _ITR_URL.format(year=year),
            cache_dir / "itr" / f"itr_cia_aberta_{year}.zip",
            force=force_download,
        )
        if p:
            itr_zips[year] = p

    # ── Coleta todos os cd_cvm ────────────────────────────────────────────────
    target_cds: Set[str] = {_norm_id(e["cd_cvm"]) for e in empresa_list}
    cd_meta: Dict[str, Dict] = {_norm_id(e["cd_cvm"]): e for e in empresa_list}

    # ── Extração batch ────────────────────────────────────────────────────────
    _log(f"\nExtraindo {len(target_cds)} empresas…")
    all_data = _extract_all(target_cds, dfp_zips, itr_zips)

    # ── Monta EmpresaData ─────────────────────────────────────────────────────
    results: Dict[str, EmpresaData] = {}
    ok_count = 0

    for cd in sorted(target_cds, key=lambda c: cd_meta[c].get("nome", "")):
        meta   = cd_meta[cd]
        setor  = meta.get("setor", "Industria")
        ajuste = SETOR_AJUSTE.get(setor, "default")

        dfp, itr, anos, trims, receb_d, receb_i = all_data.get(cd, (
            {s: DemosPivot() for s in STATEMENTS},
            {s: DemosPivot() for s in STATEMENTS},
            [], [], {}, {},
        ))

        nome_curto = meta.get("nome", cd)[:30]
        if anos:
            _log(f"  {cd} ({nome_curto})… {len(anos)}a DFP / {len(trims)}q ITR ✓")
            ok_count += 1
        else:
            _log(f"  {cd} ({nome_curto})… sem dados DFP")

        results[cd] = EmpresaData(
            ticker=cd,
            cd_cvm=cd,
            nome=meta.get("nome", cd),
            cnpj=meta.get("cnpj", ""),
            categ_reg=meta.get("categ_reg", ""),
            tp_merc=meta.get("tp_merc", ""),
            ticker_b3=meta.get("ticker_b3", ""),
            sit_cvm=meta.get("sit_cvm", "ATIVO"),
            setor_gics=setor,
            ajuste_setor=ajuste,
            denom_social=meta.get("denom_social", ""),
            denom_comerc=meta.get("denom_comerc", ""),
            setor_cvm=meta.get("setor_cvm", ""),
            controle_acionario=meta.get("controle_acionario", ""),
            mun=meta.get("mun", ""),
            uf=meta.get("uf", ""),
            dt_const=meta.get("dt_const", ""),
            dt_reg=meta.get("dt_reg", ""),
            auditor=meta.get("auditor", ""),
            sit_emissor=meta.get("sit_emissor", ""),
            dfp=dfp,
            itr=itr,
            anos_dfp=anos,
            trimestres_itr=trims,
            receb_dfp=receb_d,
            receb_itr=receb_i,
            erro=None if anos else "Sem dados DFP",
        )

    _log(f"OK — {ok_count} com dados, {len(target_cds) - ok_count} sem dados DFP")
    return results
