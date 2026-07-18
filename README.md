# br-public-spending-pipeline

**Pra onde vai o dinheiro público federal — convênios com estados/municípios e emendas parlamentares — atualizado sozinho, todo mundo pode ver, ninguém paga nada.**

Este projeto baixa dados públicos do [Portal da Transparência](https://portaldatransparencia.gov.br/), organiza tudo com boas práticas de engenharia de dados, e publica um painel estático que qualquer pessoa acessa só com um link — sem instalar programa, sem criar conta, sem servidor rodando o tempo todo.

**🔗 Site ao vivo:** https://dexcarva.github.io/br-public-spending-pipeline/
*(fica no ar depois que o GitHub Pages processa o primeiro deploy e o workflow de dados roda pela primeira vez — veja "Como a automação funciona" mais abaixo)*

Este README é intencionalmente longo e explica o **porquê** de cada decisão, não só o *como* rodar — esse é o objetivo didático do projeto: qualquer pessoa lendo o código (ou este arquivo) deveria sair entendendo não só "o que" foi feito, mas por que foi feito assim.

## Por que essa arquitetura (e não Docker + Airflow + Postgres + Streamlit)

A ideia original de um pipeline de dados "de livro-texto" seria: Docker orquestrando containers de Airflow (orquestração), PostgreSQL (banco) e Streamlit (visualização). É uma stack ótima pra aprender conceitos de plataforma de dados corporativa — mas ela tem um problema fatal pro objetivo deste projeto: **para qualquer pessoa ver o resultado, ela precisaria rodar esses containers na própria máquina.** Isso é o oposto de "qualquer um, sem gastar nada, sem instalar nada".

Em vez disso, este projeto separa duas coisas que normalmente ficam grudadas:

1. **Processar o dado** — feito periodicamente, na nuvem, de graça.
2. **Consumir o dado** — um site estático que qualquer navegador abre.

| Peça do pipeline "clássico" | Aqui vira | Por quê |
|---|---|---|
| Docker | nada (não existe container) | O único lugar onde código roda é o runner do GitHub Actions, que já vem pronto — não precisa provisionar nada |
| Airflow | GitHub Actions com `cron` (`.github/workflows/update-data.yml`) | Mesma ideia (agendar + rodar um pipeline), sem precisar de um servidor de orquestração de pé 24h |
| PostgreSQL | [DuckDB](https://duckdb.org/) | Banco analítico que roda **dentro do processo Python**, como se fosse um arquivo — não existe "instalar o banco", só existe abrir um arquivo `.duckdb` |
| Streamlit (app com servidor) | HTML + CSS + JavaScript puro, com [Chart.js](https://www.chartjs.org/) | Não precisa de servidor rodando pra sempre — o "app" é só arquivos estáticos que o [GitHub Pages](https://pages.github.com/) serve de graça |

E o que **não** mudou em relação à ideia original: ainda existe uma ingestão com paginação e retry, ainda existe uma modelagem em camadas com [dbt](https://www.getdbt.com/) (staging → dimensões → fato), e ainda existem testes automáticos de qualidade de dado. A engenharia de dados "de verdade" continua toda lá — só o *onde* ela roda que mudou.

## Arquitetura

```mermaid
flowchart LR
    subgraph "GitHub Actions (roda 1x/semana ou sob demanda, de graça)"
        A1[ingestion/extract_convenios.py] -->|JSONL bruto| B[(data/raw/)]
        A2[ingestion/extract_emendas.py] -->|JSONL bruto| B
        B --> C["dbt build (transform/)"]
        C -->|DuckDB local| D[transform/export/export_static_json.py]
        D -->|JSON pequenos e agregados| E[docs/data/]
    end
    E -->|commit automático| F[GitHub Pages]
    F -->|HTML/CSS/JS + Chart.js| G((Qualquer visitante,\nsó com um link))
```

Cada seta desse diagrama é um arquivo real neste repositório — não é um diagrama conceitual, é literalmente o que roda.

## De onde vêm os dados

A fonte é a [API de Dados do Portal da Transparência](https://portaldatransparencia.gov.br/api-de-dados). O pipeline consome dois endpoints, tratados como duas fontes independentes (ingestão, modelagem e seção do site separadas para cada uma — não existe uma chave confiável pra juntar as duas num dataset só):

### Convênios (`/api-de-dados/convenios`)

**O que é um convênio:** o instrumento jurídico que o Governo Federal usa pra repassar dinheiro a estados, municípios e outras entidades pra executar um projeto específico (uma obra, um programa de saúde, etc.).

**Por que esse endpoint e não outro:** a API tem dezenas de endpoints, mas boa parte deles (ex.: parcelas do Bolsa Família por município) exige informar o código IBGE de **um** município por chamada — cobrir os ~5.570 municípios do Brasil exigiria milhares de requisições só pra um mês de um programa. O endpoint de convênios, ao contrário, aceita um intervalo de datas e devolve convênios de todos os municípios e órgãos nesse intervalo, paginado — o que permite manter o pipeline rápido e dentro do limite de requisições da API (documentado como 90 requisições/minuto em horário comercial, com suspensão de 8h se ultrapassar) rodando de graça no GitHub Actions.

### Emendas parlamentares (`/api-de-dados/emendas`)

**O que é uma emenda parlamentar:** o mecanismo pelo qual um deputado, senador, bancada estadual ou comissão do Congresso destina uma fatia do orçamento federal a uma finalidade específica. É o dado que liga "político" a "dinheiro público" de forma direta.

**O ciclo orçamentário** (a fonte real dos números "proposto" e "executado" — não são categorias que inventamos, são os 3 estágios formais que todo gasto público federal passa antes de sair do caixa):

| Estágio | Campo na API | O que significa |
|---|---|---|
| 1. Empenho | `valorEmpenhado` | O parlamentar reserva/destina o valor — o mais próximo de "proposto". |
| 2. Liquidação | `valorLiquidado` | Comprovação de que o bem/serviço foi entregue. |
| 3. Pagamento | `valorPago` | O dinheiro efetivamente sai do caixa do governo — o mais próximo de "executado". |
| (cancelamento) | `valorRestoCancelado` | Valor empenhado e depois formalmente **cancelado** — prometido e nunca virou nada. |

A métrica central do painel, **taxa de execução = pago ÷ empenhado**, vem direto desses campos oficiais. Não inventamos um "score de impacto" — impacto social de um projeto não dá pra medir só com valor financeiro, e o painel não finge que dá.

**Cuidado de justiça aplicado no código:** nem todo `autor` de emenda é uma pessoa — existem emendas de bancada estadual (coletiva, todos os deputados de um estado) e de comissão/relator. O campo `tipoEmenda` é preservado e exposto (`dim_autor_emenda.eh_parlamentar_individual`) pra essas entidades coletivas não aparecerem misturadas num "ranking de políticos" como se fossem uma pessoa só — o site mostra só emendas individuais por padrão, com opção de incluir as coletivas.

**Limitação honesta desse endpoint:** a API não devolve um identificador estável (CPF, ID de parlamentar) pro autor, só o nome em texto — então o agrupamento por parlamentar depende do nome vir escrito de forma consistente na fonte. Nomes de deputados/senadores federais raramente colidem na prática, mas isso é uma limitação da fonte de dados, não uma garantia matemática de unicidade.

### O que este painel não cobre

Convênios e emendas são dois entre vários instrumentos de repasse do governo (existem outros, como transferências constitucionais). Isto não é o gasto público total.

## Estrutura do repositório

```
ingestion/
  portal_transparencia.py  # cliente HTTP compartilhado: autenticação, retry/backoff, rate limit
  extract_convenios.py     # pagina por intervalo de datas, salva data/raw/convenios.jsonl
  extract_emendas.py       # pagina por ano, salva data/raw/emendas.jsonl
transform/                 # projeto dbt
  dbt_project.yml
  profiles.yml              # aponta pra um arquivo .duckdb local — sem servidor
  macros/normalizar_valor_brl.sql  # converte "1.234,56" (formato BR) pra decimal
  models/
    staging/                # achata o JSON bruto, corrige tipos (dbt: camada "staging")
    marts/dimensions/        # dim_municipio, dim_orgao_superior, dim_autor_emenda
    marts/facts/             # fct_convenios, fct_emendas
    marts/schema.yml         # testes de qualidade (not_null, unique, etc.)
  tests/                     # testes customizados: valores nunca negativos
  export/export_static_json.py  # lê o resultado do dbt, gera os JSON do site
docs/                       # é isso que o GitHub Pages publica
  index.html
  css/style.css
  js/app.js                  # busca os JSON, desenha os gráficos (Chart.js)
  data/                      # kpis.json, ranking_orgaos.json, serie_temporal.json, municipios.json,
                              # kpis_emendas.json, ranking_parlamentares.json
.github/workflows/update-data.yml   # o "orquestrador": roda tudo isso 1x/semana
```

## Rodando localmente

Precisa só de Python 3.11+ — nada de Docker, nada de instalar banco de dados.

```bash
pip install -r requirements.txt
```

### 1. Conseguir uma chave da API (grátis, leva menos de 1 minuto)

Cadastre seu e-mail em **https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email** — a chave chega por e-mail. Depois:

```bash
# Linux/macOS
export PORTAL_TRANSPARENCIA_API_KEY="sua-chave-aqui"

# Windows (PowerShell)
$env:PORTAL_TRANSPARENCIA_API_KEY = "sua-chave-aqui"
```

### 2. Rodar o pipeline, passo a passo

```bash
# 1) Ingestão: baixa convênios (últimos 24 meses) e emendas parlamentares
#    (últimos 3 anos) — janelas configuráveis via env vars, veja o topo de
#    cada script em ingestion/.
python ingestion/extract_convenios.py
python ingestion/extract_emendas.py

# 2) Transformação: roda os models do dbt (staging -> dimensões -> fato) e os
#    testes de qualidade de dado, tudo dentro de um arquivo DuckDB local.
cd transform
dbt build --profiles-dir .
cd ..

# 3) Export: gera os JSON agregados que o site consome, em docs/data/.
python transform/export/export_static_json.py
```

### 3. Ver o site

Navegadores bloqueiam `fetch()` de arquivos abertos direto (`file://`) por segurança — então sirva a pasta `docs/` com um servidor HTTP simples:

```bash
cd docs
python -m http.server 8000
```

Abra **http://localhost:8000** no navegador.

## Como a automação funciona (`.github/workflows/update-data.yml`)

Uma vez por semana (e também sob demanda, pela aba *Actions* do GitHub → "Atualizar dados de convênios" → *Run workflow*), o GitHub roda os mesmos passos acima (as duas ingestões, o `dbt build`, o export) numa máquina temporária, e depois commita os JSON atualizados de volta na branch `main`. O GitHub Pages, configurado pra servir a pasta `docs/` dessa branch, republica o site sozinho sempre que esses arquivos mudam.

### Configurando num fork/cópia deste repositório

1. **Gerar a chave da API** (mesmo passo do "rodando localmente" acima).
2. **Adicionar a chave como secret do repositório**: Settings → Secrets and variables → Actions → *New repository secret* → nome `PORTAL_TRANSPARENCIA_API_KEY`, valor a chave recebida por e-mail.
3. **Habilitar o GitHub Pages**: Settings → Pages → Build and deployment → Source: *Deploy from a branch* → Branch: `main`, pasta `/docs`.
4. **Disparar a primeira execução**: aba Actions → "Atualizar dados de convênios" → *Run workflow* (não precisa esperar a segunda-feira). Depois disso, o cron semanal mantém tudo atualizado sozinho.

## Qualidade de dado (o "data contract")

O checklist original deste projeto pedia explicitamente testes de qualidade — eles estão em `transform/models/marts/schema.yml` (chaves primárias únicas e não-nulas nas dimensões, chaves estrangeiras não-nulas nas fatos) e nos testes customizados em `transform/tests/` (`assert_valor_convenio_nao_negativo.sql`, `assert_valor_empenhado_nao_negativo.sql`: gasto público não pode ser negativo). Rodando `dbt build`, esses testes falham **visivelmente** no log do GitHub Actions se algum dado vier fora do esperado — em vez de um número errado aparecer silenciosamente no site.

## Licença

Veja [LICENSE](LICENSE). Os dados em si são públicos, sob responsabilidade da Controladoria-Geral da União (CGU).
