"""
Exporta o resultado final do dbt (as tabelas em transform/../data/warehouse.duckdb)
para arquivos JSON pequenos e já agregados, prontos pro site estático consumir.

POR QUE exportar em vez de o site consultar o banco direto: o site roda no
navegador de qualquer visitante, sem servidor por trás (GitHub Pages só serve
arquivos estáticos) — não existe "banco" pra consultar em tempo real depois
que o job do GitHub Actions termina. Então, em vez disso, a gente faz as
agregações pesadas UMA VEZ aqui (com SQL, que é ótimo nisso) e salva só o
resultado já pronto: uns poucos KB de JSON que o navegador só precisa ler e
desenhar em gráfico, sem processar milhares de linhas.

Isso também é mais rápido pro visitante: baixar 50 KB de JSON agregado é
instantâneo, mesmo num celular com internet ruim — bem diferente de baixar o
dataset bruto inteiro e agregar no navegador.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# Resolvemos os caminhos a partir da localização deste arquivo (não do
# diretório de onde o script foi chamado). Isso faz o script funcionar igual
# rodando `python transform/export/export_static_json.py` da raiz do repo ou
# `python export_static_json.py` de dentro da própria pasta — não depende de
# "de onde você está" quando aperta enter.
REPO_ROOT = Path(__file__).resolve().parents[2]
DUCKDB_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
OUTPUT_DIR = REPO_ROOT / "docs" / "data"

# O dbt-duckdb nomeia o schema físico concatenando o schema "default" do
# profile (aqui, "main", porque não fixamos outro em profiles.yml) com o
# +schema declarado em dbt_project.yml ("marts") -> "main_marts". Não é
# mágica, é só a convenção padrão do macro generate_schema_name do dbt.
SCHEMA_MARTS = "main_marts"


def _conectar_somente_leitura() -> duckdb.DuckDBPyConnection:
    """Abre o banco gerado pelo dbt em modo read_only: este script só lê o
    resultado final, nunca deveria escrever no warehouse."""
    if not DUCKDB_PATH.exists():
        raise SystemExit(
            f"{DUCKDB_PATH} não existe. Rode a ingestão e `dbt run` antes do export."
        )
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _escrever_json(nome_arquivo: str, dado) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    caminho = OUTPUT_DIR / nome_arquivo
    with caminho.open("w", encoding="utf-8") as f:
        # separators compactos + sem espaço extra: são arquivos que vão pro
        # navegador do visitante, então cada KB a menos importa um pouco.
        json.dump(dado, f, ensure_ascii=False, separators=(",", ":"), default=str)
    print(f"  escrito {caminho} ({caminho.stat().st_size / 1024:.1f} KB)")


def exportar_kpis(con: duckdb.DuckDBPyConnection) -> None:
    """Números-resumo pros cartões de KPI no topo do site."""
    linha = con.sql(f"""
        select
            sum(valor)                                    as total_valor,
            sum(valor_liberado)                           as total_valor_liberado,
            count(*)                                       as numero_convenios,
            count(distinct sk_municipio)                  as numero_municipios,
            count(distinct sk_orgao_superior)              as numero_orgaos,
            min(data_publicacao)                           as periodo_inicio,
            max(data_publicacao)                           as periodo_fim
        from {SCHEMA_MARTS}.fct_convenios
    """).fetchone()

    orgao_topo = con.sql(f"""
        select o.nome_orgao_superior, sum(f.valor) as valor_total
        from {SCHEMA_MARTS}.fct_convenios f
        join {SCHEMA_MARTS}.dim_orgao_superior o on f.sk_orgao_superior = o.sk_orgao_superior
        group by o.nome_orgao_superior
        order by valor_total desc
        limit 1
    """).fetchone()

    mes_pico = con.sql(f"""
        select mes_referencia, sum(valor) as valor_total
        from {SCHEMA_MARTS}.fct_convenios
        group by mes_referencia
        order by valor_total desc
        limit 1
    """).fetchone()

    kpis = {
        "total_valor": linha[0],
        "total_valor_liberado": linha[1],
        "numero_convenios": linha[2],
        "numero_municipios": linha[3],
        "numero_orgaos": linha[4],
        "periodo_inicio": str(linha[5]) if linha[5] else None,
        "periodo_fim": str(linha[6]) if linha[6] else None,
        "orgao_maior_gasto": {"nome": orgao_topo[0], "valor": orgao_topo[1]} if orgao_topo else None,
        "mes_pico": {"mes": str(mes_pico[0])[:7], "valor": mes_pico[1]} if mes_pico else None,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
    }
    _escrever_json("kpis.json", kpis)


def exportar_ranking_orgaos(con: duckdb.DuckDBPyConnection) -> None:
    """Todos os órgãos superiores com seu total gasto — o frontend decide
    quantos mostrar no gráfico (ex.: top 15), não precisamos truncar aqui."""
    linhas = con.sql(f"""
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
    """).fetchall()

    dados = [
        {"codigo": r[0], "nome": r[1], "sigla": r[2], "valor_total": r[3], "numero_convenios": r[4]}
        for r in linhas
    ]
    _escrever_json("ranking_orgaos.json", dados)


def exportar_serie_temporal(con: duckdb.DuckDBPyConnection) -> None:
    """Total gasto por mês — alimenta o gráfico de sazonalidade."""
    linhas = con.sql(f"""
        select
            mes_referencia,
            sum(valor) as valor_total,
            count(*)   as numero_convenios
        from {SCHEMA_MARTS}.fct_convenios
        where mes_referencia is not null
        group by mes_referencia
        order by mes_referencia
    """).fetchall()

    dados = [
        {"mes": str(r[0])[:7], "valor_total": r[1], "numero_convenios": r[2]}
        for r in linhas
    ]
    _escrever_json("serie_temporal.json", dados)


def exportar_municipios(con: duckdb.DuckDBPyConnection) -> None:
    """Um agregado por município — alimenta o ranking de municípios e o
    filtro em cascata região -> UF -> município no frontend (o JS deriva as
    listas de região/UF a partir deste mesmo arquivo, sem precisar de outro)."""
    linhas = con.sql(f"""
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
    """).fetchall()

    dados = [
        {
            "codigo_ibge": r[0],
            "nome": r[1],
            "uf_sigla": r[2],
            "uf_nome": r[3],
            "regiao": r[4],
            "valor_total": r[5],
            "numero_convenios": r[6],
        }
        for r in linhas
    ]
    _escrever_json("municipios.json", dados)


def main() -> None:
    print(f"Lendo {DUCKDB_PATH}...")
    con = _conectar_somente_leitura()
    try:
        exportar_kpis(con)
        exportar_ranking_orgaos(con)
        exportar_serie_temporal(con)
        exportar_municipios(con)
    finally:
        con.close()
    print("Export concluído.")


if __name__ == "__main__":
    main()
