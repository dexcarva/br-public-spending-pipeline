"""
Painel Streamlit — a versão "com servidor" do site estático, lendo DIRETO do
Postgres da stack Docker (schema analytics_marts, materializado pelo dbt).

QUAL É O PAPEL DELE, se o site estático já existe: latência de feedback. O
site público só muda depois de rodar publicar_site.py + commit + push +
GitHub Pages — este painel mostra o warehouse NO SEGUNDO em que a DAG
termina (ou no meio de uma investigação ad-hoc), sem publicar nada. O site
continua sendo a vitrine pública; isto aqui é o painel de quem opera o
pipeline. Por isso ele lê as MESMAS tabelas fato/dimensão — se os números
divergirem do site, é bug de verdade, não "cada painel calcula diferente".

DECISÕES DE VISUALIZAÇÃO (mesmo critério do site estático, validado com a
skill de dataviz):
  - Série ÚNICA em tudo -> UMA cor (o azul #2a78d6 da paleta validada do
    site), sem legenda (o título nomeia a série), sem eixo duplo.
  - Ranking = barras HORIZONTAIS: nome de ministério é longo — na vertical,
    os rótulos viram picadinho ilegível de 45°.
  - Agregação SEMPRE no SQL, nunca em pandas: o banco agrega milhares de
    linhas em milissegundos e o Python recebe só o resultado — é o mesmo
    princípio do export estático ("agrega uma vez, no lugar certo").
"""

from __future__ import annotations

import os

import altair as alt
import pandas as pd
import psycopg2
import streamlit as st

# Mesma convenção de nome de schema explicada em scripts/publicar_site.py:
# schema base "analytics" (profiles.yml) + "marts" (dbt_project.yml).
SCHEMA = "analytics_marts"

# O azul de série da paleta do site estático (docs/css/style.css,
# --serie-azul), já validada contra daltonismo/contraste — reusar a MESMA cor
# faz os dois painéis parecerem o mesmo produto, porque são.
AZUL_SERIE = "#2a78d6"

st.set_page_config(
    page_title="Gastos Públicos — Convênios Federais",
    page_icon="📊",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Conexão e consultas
# ---------------------------------------------------------------------------

@st.cache_resource
def conectar() -> psycopg2.extensions.connection:
    """Uma conexão pro processo INTEIRO do Streamlit, não uma por interação.

    st.cache_resource guarda objetos "vivos" (conexões, modelos) entre
    re-execuções do script — e o Streamlit re-executa o script INTEIRO a cada
    clique do usuário: sem o cache, cada mudança de filtro abriria uma
    conexão nova no Postgres (e as órfãs se acumulariam até estourar o
    max_connections do servidor).

    SOMENTE-LEITURA de verdade, não por disciplina: a sessão liga
    default_transaction_read_only, então qualquer escrita acidental falha na
    hora. Painel de visualização jamais deveria conseguir alterar o
    warehouse — é o mesmo princípio do read_only=True no export DuckDB e em
    publicar_site.py.
    """
    conexao = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "gastos"),
        user=os.environ.get("POSTGRES_USER", "gastos"),
        password=os.environ.get("POSTGRES_PASSWORD", "gastos"),
        options="-c default_transaction_read_only=on",
    )
    # autocommit: cada SELECT roda na sua própria transação implícita. Sem
    # isso, o psycopg2 abre uma transação e a mantém — e se UMA query falhar
    # (ex.: painel aberto antes do primeiro `dbt run` criar os schemas), a
    # transação fica "abortada" e TODAS as queries seguintes falham na mesma
    # conexão... que é cacheada pelo st.cache_resource: o painel ficaria
    # travado até reiniciar o container. Pra um leitor puro, autocommit é o
    # comportamento simples e correto (e o read_only acima continua valendo:
    # cada transação implícita nasce somente-leitura).
    conexao.autocommit = True
    return conexao


@st.cache_data(ttl=600)
def consultar(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Roda um SELECT e devolve DataFrame, com cache de 10 minutos.

    st.cache_data (diferente do cache_resource acima) guarda RESULTADOS por
    argumento: dois usuários com o mesmo filtro reusam a mesma resposta sem
    tocar no banco. O TTL de 10 min é maior que qualquer sessão de exploração
    típica e menor que o ciclo da DAG (@daily) — dado novo aparece no máximo
    10 min depois de carregado, sem ninguém precisar "limpar cache".
    """
    df = pd.read_sql(sql, conectar(), params=params)
    # O psycopg2 devolve NUMERIC como decimal.Decimal (preciso, mas os
    # gráficos serializam pra JSON e não sabem o que fazer com Decimal) —
    # convertemos as colunas de valor pra float aqui, uma vez, na fronteira.
    for coluna in df.columns:
        if coluna.startswith("valor") or coluna in ("total",):
            df[coluna] = df[coluna].astype(float)
    return df


def formatar_reais(valor: float) -> str:
    """R$ compacto no padrão BR (1,2 bi / 345,6 mi) — número gigante por
    extenso em cartão de KPI não é legível, é ruído."""
    if valor is None:
        return "—"
    for corte, sufixo in ((1e9, "bi"), (1e6, "mi"), (1e3, "mil")):
        if abs(valor) >= corte:
            return f"R$ {valor / corte:,.1f} {sufixo}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {valor:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Sidebar: filtro em cascata região -> UF -> município
# (mesma cascata do site estático — lá derivada de municipios.json no JS,
# aqui derivada de dim_municipio no SQL: mesma dimensão, duas portas)
# ---------------------------------------------------------------------------

st.sidebar.header("Filtros")

# dim_municipio tem ~poucos milhares de linhas — cabe inteira em memória, e
# uma consulta só alimenta os três selects da cascata.
municipios = consultar(f"""
    select nome_regiao, uf_nome, nome_municipio, codigo_ibge
    from {SCHEMA}.dim_municipio
    order by nome_regiao, uf_nome, nome_municipio
""")

TODAS = "Todas"
TODOS = "Todos"

regiao = st.sidebar.selectbox("Região", [TODAS] + sorted(municipios["nome_regiao"].unique()))

# Cascata de verdade: cada select abaixo só oferece opções compatíveis com o
# nível acima — impossível montar um filtro contraditório (ex.: região
# Nordeste + UF do Sul), então nenhum "estado inválido" precisa de tratamento.
ufs_visiveis = municipios if regiao == TODAS else municipios[municipios["nome_regiao"] == regiao]
uf = st.sidebar.selectbox("UF", [TODAS] + sorted(ufs_visiveis["uf_nome"].unique()))

municipios_visiveis = ufs_visiveis if uf == TODAS else ufs_visiveis[ufs_visiveis["uf_nome"] == uf]
municipio = st.sidebar.selectbox(
    "Município", [TODOS] + sorted(municipios_visiveis["nome_municipio"].unique())
)

st.sidebar.caption(
    "Dados do Portal da Transparência (CGU), processados pela DAG "
    "`pipeline_gastos_publicos` — ver Airflow em http://localhost:8080."
)

# Os filtros viram parâmetros NOMEADOS de SQL (%(regiao)s...), nunca
# concatenação de string: com f-string, um nome de município com apóstrofo
# (Santa Bárbara d'Oeste...) quebraria a query — e concatenar input de
# usuário em SQL é exatamente o hábito que vira SQL injection em sistemas
# expostos. O padrão "%(x)s is null or coluna = %(x)s" faz o filtro vazio
# ("Todas") desligar a condição dentro da própria query.
FILTRO_SQL = """
    (%(regiao)s is null or m.nome_regiao = %(regiao)s)
    and (%(uf)s is null or m.uf_nome = %(uf)s)
    and (%(municipio)s is null or m.nome_municipio = %(municipio)s)
"""
params = {
    "regiao": None if regiao == TODAS else regiao,
    "uf": None if uf == TODAS else uf,
    "municipio": None if municipio == TODOS else municipio,
}


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

st.title("📊 Gastos Públicos — Convênios Federais")
st.caption(
    "Repasses do Governo Federal a estados, municípios e entidades via "
    "convênios. Warehouse local (Postgres + dbt), irmão do site estático."
)
# Aviso de completude SEMPRE visível (não escondido em expander): este painel
# mostra dados sobre pessoas reais, e conclusão apressada em cima de
# histórico parcial é risco jurídico — pro leitor e pro projeto.
st.caption(
    "⚠️ **Projeto independente, não-oficial. O histórico pode estar "
    "incompleto** e os valores podem não refletir o estado oficial mais "
    "recente (a fonte atualiza retroativamente). Não use como fonte única "
    "pra conclusões sobre qualquer pessoa ou órgão — detalhes no rodapé."
)

kpis = consultar(f"""
    select
        sum(f.valor)                       as valor_total,
        sum(f.valor_liberado)              as valor_liberado,
        count(*)                           as numero_convenios,
        count(distinct f.sk_municipio)     as numero_municipios
    from {SCHEMA}.fct_convenios f
    join {SCHEMA}.dim_municipio m on f.sk_municipio = m.sk_municipio
    where {FILTRO_SQL}
""", params).iloc[0]

if kpis["numero_convenios"] == 0:
    st.warning(
        "Nenhum convênio pro filtro selecionado. Se o painel inteiro está "
        "vazio, a DAG ainda não rodou — dispare `pipeline_gastos_publicos` "
        "no Airflow (http://localhost:8080) e volte aqui."
    )
    st.stop()

# Quatro st.metric lado a lado: número-resumo é TILE, não gráfico — um
# gráfico de um número só é decoração (ver a skill de dataviz: "às vezes a
# resposta não é um gráfico").
col1, col2, col3, col4 = st.columns(4)
col1.metric("Valor total", formatar_reais(kpis["valor_total"]))
col2.metric("Valor liberado", formatar_reais(kpis["valor_liberado"]))
col3.metric("Convênios", f"{int(kpis['numero_convenios']):,}".replace(",", "."))
col4.metric("Municípios atendidos", f"{int(kpis['numero_municipios']):,}".replace(",", "."))


# ---------------------------------------------------------------------------
# Ranking por órgão superior
# ---------------------------------------------------------------------------

st.subheader("Top 15 órgãos superiores por valor")

ranking = consultar(f"""
    select
        o.nome_orgao_superior as orgao,
        sum(f.valor)          as valor_total,
        count(*)              as numero_convenios
    from {SCHEMA}.fct_convenios f
    join {SCHEMA}.dim_orgao_superior o on f.sk_orgao_superior = o.sk_orgao_superior
    join {SCHEMA}.dim_municipio m on f.sk_municipio = m.sk_municipio
    where {FILTRO_SQL}
    group by 1
    order by valor_total desc
    limit 15
""", params)

grafico_ranking = (
    alt.Chart(ranking)
    .mark_bar(color=AZUL_SERIE, cornerRadiusEnd=4, height={"band": 0.7})
    .encode(
        x=alt.X("valor_total:Q", title="Valor total (R$)", axis=alt.Axis(format="~s")),
        # sort="-x": a barra maior em cima — ranking sem ordenação não é
        # ranking, é lista embaralhada.
        y=alt.Y("orgao:N", sort="-x", title=None),
        # Tooltip = a "camada de hover" que a skill de dataviz pede por
        # padrão: o rótulo direto mostra só o essencial, o detalhe fica a um
        # hover de distância.
        tooltip=[
            alt.Tooltip("orgao:N", title="Órgão"),
            alt.Tooltip("valor_total:Q", title="Valor (R$)", format=",.0f"),
            alt.Tooltip("numero_convenios:Q", title="Convênios"),
        ],
    )
    .properties(height=420)
)
st.altair_chart(grafico_ranking, use_container_width=True)


# ---------------------------------------------------------------------------
# Série temporal mensal
# ---------------------------------------------------------------------------

st.subheader("Evolução mensal do valor publicado")

serie = consultar(f"""
    select
        f.mes_referencia      as mes,
        sum(f.valor)          as valor_total,
        count(*)              as numero_convenios
    from {SCHEMA}.fct_convenios f
    join {SCHEMA}.dim_municipio m on f.sk_municipio = m.sk_municipio
    where {FILTRO_SQL}
      and f.mes_referencia is not null
    group by 1
    order by 1
""", params)

grafico_serie = (
    alt.Chart(serie)
    # Linha de 2px + pontos visíveis (spec de marcas da skill: linha fina,
    # marcador >= 8px de alvo) — os pontos também são o alvo do tooltip.
    .mark_line(color=AZUL_SERIE, strokeWidth=2, point=alt.OverlayMarkDef(color=AZUL_SERIE, size=60))
    .encode(
        x=alt.X("mes:T", title=None, axis=alt.Axis(format="%b/%Y")),
        # Eixo Y começando em ZERO: série de valor absoluto com eixo cortado
        # exagera visualmente qualquer variação — clássico de gráfico
        # enganoso, mesmo sem intenção.
        y=alt.Y("valor_total:Q", title="Valor (R$)", scale=alt.Scale(zero=True),
                axis=alt.Axis(format="~s")),
        tooltip=[
            alt.Tooltip("mes:T", title="Mês", format="%m/%Y"),
            alt.Tooltip("valor_total:Q", title="Valor (R$)", format=",.0f"),
            alt.Tooltip("numero_convenios:Q", title="Convênios"),
        ],
    )
    .properties(height=320)
)
st.altair_chart(grafico_serie, use_container_width=True)

# ---------------------------------------------------------------------------
# Emendas × ciclo eleitoral (Fase 8): "safadeza programada"?
# ---------------------------------------------------------------------------

st.subheader("Emendas parlamentares × ciclo eleitoral")
st.caption(
    "Hipótese em teste: o empenho de emendas incha em ano de eleição. O "
    "calendário eleitoral é derivado por aritmética (ano par = eleição; "
    "resto 2 ÷ 4 = geral, múltiplo de 4 = municipal) — nenhuma API extra. "
    "Esta seção ignora o filtro geográfico da barra lateral: emenda não tem "
    "vínculo confiável com município na fonte."
)

emendas_ano = consultar(f"""
    select
        d.ano,
        d.eh_ano_eleitoral,
        coalesce(d.tipo_eleicao, 'Sem eleição') as tipo_eleicao,
        d.posicao_mandato_federal,
        sum(f.valor_empenhado) as valor_empenhado,
        sum(f.valor_pago)      as valor_pago,
        count(*)               as numero_emendas
    from {SCHEMA}.fct_emendas f
    join {SCHEMA}.dim_ano_eleitoral d on f.ano = d.ano
    group by 1, 2, 3, 4
    order by 1
""")

# "Wide -> long": os gráficos declarativos (Altair) esperam uma linha por
# (ano, medida) pra desenhar barras pareadas — o melt faz essa dobra.
emendas_longo = emendas_ano.melt(
    id_vars=["ano", "eh_ano_eleitoral", "tipo_eleicao", "posicao_mandato_federal"],
    value_vars=["valor_empenhado", "valor_pago"],
    var_name="medida",
    value_name="valor",
)
emendas_longo["medida"] = emendas_longo["medida"].map(
    {"valor_empenhado": "Empenhado (prometido)", "valor_pago": "Pago (entregue)"}
)

grafico_eleitoral = (
    alt.Chart(emendas_longo)
    .mark_bar(cornerRadiusEnd=4)
    .encode(
        x=alt.X("ano:O", title=None, axis=alt.Axis(labelAngle=0)),
        # xOffset agrupa as duas barras lado a lado dentro de cada ano.
        xOffset=alt.XOffset("medida:N"),
        y=alt.Y("valor:Q", title="Valor (R$)", axis=alt.Axis(format="~s")),
        # O MESMO par de azuis do site estático (empenhado fraco, pago forte)
        # — a cor segue a MEDIDA (entidade), nunca o ano, e a distinção de
        # "ano eleitoral" fica no tooltip/tabela em vez de virar uma terceira
        # cor competindo (regra da skill de dataviz: cor tem um só papel).
        color=alt.Color(
            "medida:N",
            title=None,
            scale=alt.Scale(
                domain=["Empenhado (prometido)", "Pago (entregue)"],
                range=["#9ec5f4", AZUL_SERIE],
            ),
        ),
        tooltip=[
            alt.Tooltip("ano:O", title="Ano"),
            alt.Tooltip("tipo_eleicao:N", title="Eleição"),
            alt.Tooltip("posicao_mandato_federal:Q", title="Ano do mandato federal"),
            alt.Tooltip("medida:N", title="Medida"),
            alt.Tooltip("valor:Q", title="Valor (R$)", format=",.0f"),
        ],
    )
    .properties(height=320)
)
st.altair_chart(grafico_eleitoral, use_container_width=True)

# A leitura honesta fica em texto, não em cor de gráfico: mostrar O QUE cada
# ano é (eleição geral? municipal? ano 4 do mandato?) e deixar o leitor
# comparar — o painel expõe o padrão, não carimba veredito.
st.caption(
    " · ".join(
        f"**{int(r.ano)}**: {r.tipo_eleicao}, ano {int(r.posicao_mandato_federal)} do mandato federal"
        for r in emendas_ano.itertuples()
    )
)

# Vista em tabela dos mesmos dados (passo de acessibilidade da skill: todo
# gráfico tem uma alternativa textual) — recolhida pra não competir com os
# gráficos, mas a um clique pra quem precisa do número exato ou usa leitor
# de tela.
with st.expander("Ver dados em tabela"):
    st.dataframe(ranking, use_container_width=True, hide_index=True)
    st.dataframe(serie, use_container_width=True, hide_index=True)
    st.dataframe(emendas_ano, use_container_width=True, hide_index=True)

# Rodapé: a versão completa do aviso de completude/precisão (o resumo fica
# fixo no topo). Mesmo texto do site estático — um aviso, duas vitrines.
st.divider()
st.caption(
    "**⚠️ Aviso sobre completude e precisão:** este é um projeto independente "
    "de visualização de dados públicos — **não** é uma publicação oficial do "
    "Governo Federal. A cobertura é parcial e em construção: **o histórico "
    "exibido pode estar incompleto** (a ingestão cobre janelas limitadas de "
    "tempo, e a própria fonte atualiza valores retroativamente — pagamentos, "
    "cancelamentos e restos a pagar mudam depois da publicação), e os números "
    "podem não refletir o estado oficial mais recente. Estas informações "
    "**não devem ser usadas como fonte única** para decisões, denúncias ou "
    "conclusões definitivas sobre qualquer pessoa ou órgão. Uma taxa de "
    "execução baixa tem explicações legítimas (ex.: restos a pagar atravessam "
    "anos-exercício) e não implica, por si só, qualquer irregularidade. Para "
    "dados oficiais e completos: [Portal da Transparência (CGU)]"
    "(https://portaldatransparencia.gov.br/)."
)
