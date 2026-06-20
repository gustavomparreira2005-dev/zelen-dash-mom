# Dash - Mom · Momentum de Ações (Zelen Invest)

Dashboard de momentum de ações da B3 com **dois rankings independentes**:

- **Ranking Operacional** — derivada dos fundamentos (CVM DFP/ITR, série LTM): aceleração de receita, expansão de margem, tendência de ROIC.
- **Ranking Técnico** — momentum de preço (Yahoo Finance): momentum 12-1, retornos 1/3/6/12m, MM50/200, RSI, força relativa vs Ibovespa, distância da máxima de 52 semanas.

Os scores são **percentis cross-sectional** dentro do universo (posição relativa), não recomendação de investimento.

## Uso

```bash
python main_acoes.py                          # 10 ações MVP (default)
python main_acoes.py --tickers VALE3 PETR4    # subset
python main_acoes.py --all                    # universo listado (empresas_lista.csv)
python main_acoes.py --no-cache               # força re-download (CVM + preços)
```

Saída: `relatorios/momentum_acoes.html`.

## Módulos

| Arquivo | Papel |
|---|---|
| `main_acoes.py` | Orquestrador do pipeline |
| `price_client.py` | Séries de preço (Yahoo; backend plugável p/ brapi+token) |
| `momentum_tecnico.py` | Score técnico |
| `momentum_operacional.py` | Score operacional (reusa `serie_ltm`) |
| `html_generator_acoes.py` | Geração do HTML |
| `cvm_client.py`, `indicadores_empresas.py`, `build_empresa_lista.py` | **Cópias** do projeto de crédito — ver aviso no topo de cada um |

## Notas

- **Módulos compartilhados**: `cvm_client.py`, `indicadores_empresas.py` e `build_empresa_lista.py` são cópias do projeto de crédito (`1 Renda Fixa/Dash - Credito Privado`). A fonte de verdade é lá; melhorias na metodologia de crédito **não** chegam aqui automaticamente — re-sincronize à mão quando relevante.
- **Cache CVM (458MB)**: na 1ª execução o `cache_cvm/` é baixado da CVM (lento, uma vez). Para evitar duplicar o download, aponte para o cache do projeto de crédito:
  ```bash
  python main_acoes.py --cache-dir "../../1 Renda Fixa/Dash - Credito Privado/cache_cvm"
  ```
- **Preços**: cacheados em `_cache/precos/` com atualização diária (transitório, gitignored).
- **brapi**: para trocar Yahoo → brapi (oficial BR, market cap limpo) basta um token free e implementar `_fetch_brapi` em `price_client.py` (`_BACKENDS`).
