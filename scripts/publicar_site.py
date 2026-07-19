"""
Gera os JSON estáticos do site (docs/data/*.json) a partir do POSTGRES da
stack Docker — o espelho de transform/export/export_static_json.py, que faz
o mesmo a partir do DuckDB.

ESTE SCRIPT É A PONTE entre os dois mundos do projeto: a stack local
(Airflow -> Postgres -> dbt) processa o dado pesado sem limite de tempo, e no
final este script escreve EXATAMENTE os mesmos arquivos que o site estático
do GitHub Pages consome. Publicar = rodar a DAG e depois, manualmente:

    git add docs/data && git commit -m "chore: atualiza dados" && git push

O push fica fora da DAG de propósito: commit no repositório é publicação
pública — decisão de gente olhando o resultado, não de agendador.

POR QUE UM SCRIPT ESPELHADO em vez de reaproveitar export_static_json.py:
as queries são as mesmas, mas TUDO em volta muda — driver (psycopg2 vs
duckdb), forma de conectar (servidor vs arquivo), nome do schema
("analytics_marts" vs "main_marts", ver profiles.yml) e até o tipo Python
que cada driver devolve pra NUMERIC. Parametrizar um script único pra ambos
esconderia essas diferenças atrás de abstração — e cada mundo deixaria de
rodar sem carregar as dependências do outro. Duplicação consciente e
documentada, aqui, é mais barata que o acoplamento. O CONTRATO que os dois
têm que honrar é o mesmo: os JSON de saída são byte-a-byte equivalentes em
estrutura, porque docs/js/app.js não sabe (nem deve saber) quem os gerou.

DETALHE DE DRIVER QUE VIRA BUG SILENCIOSO: o psycopg2 devolve colunas
NUMERIC como decimal.Decimal — e o json do Python serializa Decimal como
STRING (via default=str), não como número. O site espera números (Chart.js
não soma "123.45"). Por isso todo valor passa por _num() antes de ir pro
JSON — a conversão explícita Decimal -> float que o export DuckDB não
precisou fazer (o duckdb devolve float nativo pra agregações).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2

# Caminhos a partir DESTE arquivo (scripts/ -> raiz), não do cwd do shell —
# mesma prática dos outros scripts do repo: funciona igual do host ou de
# dentro do container (repo montado em /opt/pipeline).
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "docs" / "data"

# O dbt-postgres nomeia o schema físico concatenando o schema base do profile
# ("analytics", ver transform/profiles.yml) com o +schema declarado em
# dbt_project.yml ("marts") -> "analytics_marts". Não é mágica, é a convenção
# padrão do macro generate_schema_name do dbt — a mesma que no DuckDB produz
# o "main_marts" que export_static_json.py consulta.
SCHEMA_MARTS = "analytics_marts"


def _conectar() -> psycopg2.extensions.connection:
    """Conexão SOMENTE-LEITURA com o Postgres do docker-compose (mesmas env
    vars/defaults dos outros scripts). O options abaixo liga
    default_transaction_read_only na sessão: este script só consulta o
    resultado final do dbt — qualquer INSERT/UPDATE acidental que um dia
    alguém introduza aqui falha na hora, em vez de corromper o warehouse.
    É o espelho do read_only=True que o export DuckDB usa."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "gastos"),
        user=os.environ.get("POSTGRES_USER", "gastos"),
        password=os.environ.get("POSTGRES_PASSWORD", "gastos"),
        options="-c default_transaction_read_only=on",
    )


def _num(valor):
    """Decimal -> float pra serializar como NÚMERO no JSON (ver o cabeçalho).
    Deixa None e tipos já-numéricos passarem intactos."""
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def _escrever_json(nome_arquivo: str, dado) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    caminho = OUTPUT_DIR / nome_arquivo
    with caminho.open("w", encoding="utf-8") as f:
        # separators compactos + sem espaço extra: são arquivos que vão pro
        # navegador do visitante, então cada KB a menos importa um pouco.
        json.dump(dado, f, ensure_ascii=False, separators=(",", ":"), default=str)
    print(f"  escrito {caminho} ({caminho.stat().st_size / 1024:.1f} KB)")


def exportar_kpis(cur) -> None:
    """Números-resumo pros cartões de KPI no topo do site."""
    cur.execute(f"""
        select
            sum(valor)                                    as total_valor,
            sum(valor_liberado)                           as total_valor_liberado,
            count(*)                                       as numero_convenios,
            count(distinct sk_municipio)                  as numero_municipios,
            count(distinct sk_orgao_superior)              as numero_orgaos,
            min(data_publicacao)                           as periodo_inicio,
            max(data_publicacao)                           as periodo_fim
        from {SCHEMA_MARTS}.fct_convenios
    """)
    linha = cur.fetchone()

    cur.execute(f"""
        select o.nome_orgao_superior, sum(f.valor) as valor_total
        from {SCHEMA_MARTS}.fct_convenios f
        join {SCHEMA_MARTS}.dim_orgao_superior o on f.sk_orgao_superior = o.sk_orgao_superior
        group by o.nome_orgao_superior
        order by valor_total desc
        limit 1
    """)
    orgao_topo = cur.fetchone()

    cur.execute(f"""
        select mes_referencia, sum(valor) as valor_total
        from {SCHEMA_MARTS}.fct_convenios
        group by mes_referencia
        order by valor_total desc
        limit 1
    """)
    mes_pico = cur.fetchone()

    kpis = {
        "total_valor": _num(linha[0]),
        "total_valor_liberado": _num(linha[1]),
        "numero_convenios": linha[2],
        "numero_municipios": linha[3],
        "numero_orgaos": linha[4],
        "periodo_inicio": str(linha[5]) if linha[5] else None,
        "periodo_fim": str(linha[6]) if linha[6] else None,
        "orgao_maior_gasto": {"nome": orgao_topo[0], "valor": _num(orgao_topo[1])} if orgao_topo else None,
        # [:7] corta "AAAA-MM" do início — no Postgres, date_trunc devolve
        # timestamp ("2026-07-01 00:00:00"), no DuckDB devolve date; os dois
        # começam com AAAA-MM, então o corte produz o mesmo resultado.
        "mes_pico": {"mes": str(mes_pico[0])[:7], "valor": _num(mes_pico[1])} if mes_pico else None,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
    }
    _escrever_json("kpis.json", kpis)


def exportar_ranking_orgaos(cur) -> None:
    """Todos os órgãos superiores com seu total gasto — o frontend decide
    quantos mostrar no gráfico (ex.: top 15), não precisamos truncar aqui."""
    cur.execute(f"""
        select
            o.codigo_orgao_superior as codigo,
            o.nome_orgao_superior   as nome,
            o.sigla_orgao_superior  as sigla,
            sum(f.valor)            as valor_total,
            count(*)                as numero_convenios
        from {SCHEMA_MARTS}.fct_convenios f
        join {SCHEMA_MARTS}.dim_orgao_superior o on f.sk_orgao_superior = o.sk_orgao_superior
        group by 1, 2, 3
        order by valor_total desc
    """)

    dados = [
        {"codigo": r[0], "nome": r[1], "sigla": r[2], "valor_total": _num(r[3]), "numero_convenios": r[4]}
        for r in cur.fetchall()
    ]
    _escrever_json("ranking_orgaos.json", dados)


def exportar_serie_temporal(cur) -> None:
    """Total gasto por mês — alimenta o gráfico de sazonalidade."""
    cur.execute(f"""
        select
            mes_referencia,
            sum(valor) as valor_total,
            count(*)   as numero_convenios
        from {SCHEMA_MARTS}.fct_convenios
        where mes_referencia is not null
        group by mes_referencia
        order by mes_referencia
    """)

    dados = [
        {"mes": str(r[0])[:7], "valor_total": _num(r[1]), "numero_convenios": r[2]}
        for r in cur.fetchall()
    ]
    _escrever_json("serie_temporal.json", dados)


def exportar_municipios(cur) -> None:
    """Um agregado por município — alimenta o ranking de municípios e o
    filtro em cascata região -> UF -> município no frontend (o JS deriva as
    listas de região/UF a partir deste mesmo arquivo, sem precisar de outro)."""
    cur.execute(f"""
        select
            m.codigo_ibge,
            m.nome_municipio,
            m.uf_sigla,
            m.uf_nome,
            m.nome_regiao,
            sum(f.valor) as valor_total,
            count(*)     as numero_convenios
        from {SCHEMA_MARTS}.fct_convenios f
        join {SCHEMA_MARTS}.dim_municipio m on f.sk_municipio = m.sk_municipio
        group by 1, 2, 3, 4, 5
        order by valor_total desc
    """)

    dados = [
        {
            "codigo_ibge": r[0],
            "nome": r[1],
            "uf_sigla": r[2],
            "uf_nome": r[3],
            "regiao": r[4],
            "valor_total": _num(r[5]),
            "numero_convenios": r[6],
        }
        for r in cur.fetchall()
    ]
    _escrever_json("municipios.json", dados)


def exportar_kpis_emendas(cur) -> None:
    """Números-resumo das emendas parlamentares — arquivo separado de
    kpis.json de propósito: são duas fontes de dado diferentes (convênios x
    emendas), e misturar tudo num JSON só confundiria mais do que ajudaria."""
    cur.execute(f"""
        select
            sum(f.valor_empenhado)  as total_empenhado,
            sum(f.valor_pago)       as total_pago,
            sum(f.valor_resto_cancelado) as total_cancelado,
            sum(f.valor_pago) / nullif(sum(f.valor_empenhado), 0) as taxa_execucao_geral,
            count(*)                as numero_emendas,
            count(distinct case when a.eh_parlamentar_individual then a.sk_autor_emenda end)
                                     as numero_parlamentares_individuais,
            min(f.ano)               as ano_inicio,
            max(f.ano)               as ano_fim
        from {SCHEMA_MARTS}.fct_emendas f
        join {SCHEMA_MARTS}.dim_autor_emenda a on f.sk_autor_emenda = a.sk_autor_emenda
    """)
    linha = cur.fetchone()

    kpis = {
        "total_empenhado": _num(linha[0]),
        "total_pago": _num(linha[1]),
        # Dinheiro destinado por um parlamentar e depois oficialmente
        # cancelado — nunca virou entrega nenhuma. É o número mais direto
        # sobre "prometeu e não entregou" que os dados oficiais sustentam.
        "total_cancelado": _num(linha[2]),
        "taxa_execucao_geral": float(linha[3]) if linha[3] is not None else None,
        "numero_emendas": linha[4],
        "numero_parlamentares_individuais": linha[5],
        "ano_inicio": linha[6],
        "ano_fim": linha[7],
        "gerado_em": datetime.now(timezone.utc).isoformat(),
    }
    _escrever_json("kpis_emendas.json", kpis)


def exportar_ranking_parlamentares(cur) -> None:
    """Um agregado por autor de emenda (parlamentar individual OU bancada/
    comissão — rotulado via tipo_emenda/individual, ver dim_autor_emenda.sql).
    A taxa_execucao é recalculada aqui como soma(pago)/soma(empenhado), não
    como média das taxas por emenda — média de taxas ponderaria emendas
    pequenas e grandes igualmente, o que distorceria o número."""
    cur.execute(f"""
        select
            a.nome_autor,
            a.tipo_emenda,
            a.eh_parlamentar_individual,
            sum(f.valor_empenhado)       as valor_empenhado,
            sum(f.valor_pago)            as valor_pago,
            sum(f.valor_resto_cancelado) as valor_resto_cancelado,
            sum(f.valor_pago) / nullif(sum(f.valor_empenhado), 0) as taxa_execucao,
            count(*)                     as numero_emendas
        from {SCHEMA_MARTS}.fct_emendas f
        join {SCHEMA_MARTS}.dim_autor_emenda a on f.sk_autor_emenda = a.sk_autor_emenda
        group by 1, 2, 3
        order by valor_empenhado desc
    """)

    dados = [
        {
            "nome": r[0],
            "tipo_emenda": r[1],
            "individual": r[2],
            "valor_empenhado": _num(r[3]),
            "valor_pago": _num(r[4]),
            "valor_resto_cancelado": _num(r[5]),
            "taxa_execucao": float(r[6]) if r[6] is not None else None,
            "numero_emendas": r[7],
        }
        for r in cur.fetchall()
    ]
    _escrever_json("ranking_parlamentares.json", dados)


def exportar_emendas_por_ano(cur) -> None:
    """Gasto de emenda cruzado com o CALENDÁRIO ELEITORAL (dim_ano_eleitoral)
    — o agregado que sustenta a pergunta "empenha-se mais em ano de eleição?".
    A dimensão é derivada por aritmética de calendário (ver o model), então
    este export não depende de nenhuma API além das que o pipeline já usa."""
    cur.execute(f"""
        select
            d.ano,
            d.eh_ano_eleitoral,
            d.tipo_eleicao,
            d.posicao_mandato_federal,
            sum(f.valor_empenhado)  as valor_empenhado,
            sum(f.valor_pago)       as valor_pago,
            sum(f.valor_resto_cancelado) as valor_resto_cancelado,
            sum(f.valor_pago) / nullif(sum(f.valor_empenhado), 0) as taxa_execucao,
            count(*)                as numero_emendas
        from {SCHEMA_MARTS}.fct_emendas f
        join {SCHEMA_MARTS}.dim_ano_eleitoral d on f.ano = d.ano
        group by 1, 2, 3, 4
        order by 1
    """)

    dados = [
        {
            "ano": r[0],
            "eh_ano_eleitoral": r[1],
            "tipo_eleicao": r[2],
            "posicao_mandato_federal": r[3],
            "valor_empenhado": _num(r[4]),
            "valor_pago": _num(r[5]),
            "valor_resto_cancelado": _num(r[6]),
            "taxa_execucao": float(r[7]) if r[7] is not None else None,
            "numero_emendas": r[8],
        }
        for r in cur.fetchall()
    ]
    _escrever_json("emendas_por_ano.json", dados)


def main() -> None:
    print(f"Lendo {SCHEMA_MARTS} no Postgres e escrevendo {OUTPUT_DIR}...")
    conexao = _conectar()
    try:
        with conexao.cursor() as cur:
            exportar_kpis(cur)
            exportar_ranking_orgaos(cur)
            exportar_serie_temporal(cur)
            exportar_municipios(cur)
            exportar_kpis_emendas(cur)
            exportar_ranking_parlamentares(cur)
            exportar_emendas_por_ano(cur)
    finally:
        conexao.close()
    print("Publicação concluída. Pra atualizar o site público:")
    print("  git add docs/data && git commit -m 'chore: atualiza dados' && git push")


if __name__ == "__main__":
    main()
