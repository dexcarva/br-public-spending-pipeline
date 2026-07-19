"""
Carrega o dado bruto (JSONL gerado pela ingestão) no Postgres — o elo entre a
ingestão e o dbt (target "postgres" em transform/profiles.yml, que serve
tanto o Postgres do docker-compose quanto um Postgres gerenciado tipo Neon).

DUAS ESTRATÉGIAS DE CARGA, uma por fonte — e a diferença é a lição:

  CONVÊNIOS -> INCREMENTAL (delete-insert por janela de datas).
    O endpoint filtra por "última atualização do registro", então a janela de
    1 dia que a DAG passa captura tudo que MUDOU naquele dia — um CDC de
    pobre. Cada run carrega só o seu intervalo, e o histórico ACUMULA na
    tabela: o regime permanente custa O(dado novo), não O(janela inteira) —
    a diferença entre uma run diária de minutos e uma de horas.
    Idempotência: antes de inserir, apagamos SÓ as linhas da própria janela
    (delete-insert por intervalo). Re-rodar qualquer dia é seguro e não toca
    no resto — o "truncar-e-recarregar" que usávamos antes também era
    idempotente, mas jogava o warehouse inteiro fora a cada run.
    Consequência pro staging: o mesmo convênio reaparece a cada movimentação
    (parcela liberada etc.), então quem lê raw.convenios precisa deduplicar
    por id ficando com a versão mais recente — feito em stg_convenios.sql.

  EMENDAS -> SNAPSHOT (truncar-e-recarregar, como sempre foi).
    O endpoint devolve o estado ACUMULADO do ano (valores consolidados até
    hoje), não eventos — não existe "só o que mudou ontem" pra pedir.
    Re-baixar o(s) ano(s) e substituir é a representação correta da fonte, e
    é barato (~5 min/ano). Forçar incremental aqui seria complexidade sem
    ganho: a natureza da fonte decide a estratégia, não a moda.

USO (subcomando escolhe a fonte — é assim que a DAG chama):
    python scripts/carregar_raw_postgres.py convenios
        (exige CARGA_JANELA_INICIO/CARGA_JANELA_FIM em AAAA-MM-DD; lê o
         arquivo apontado por CONVENIOS_RAW_OUTPUT)
    python scripts/carregar_raw_postgres.py emendas
        (truncar-e-recarrega raw.emendas a partir de EMENDAS_RAW_OUTPUT)

POR QUE UMA COLUNA JSONB (e não uma tabela com 30 colunas tipadas): o dado
bruto deve ser guardado COMO VEIO — quem interpreta (achata, renomeia, tipa)
é a camada de staging do dbt. Se a API mudar um campo amanhã, a carga não
quebra e o histórico bruto fica íntegro; só o staging acompanha. JSONB (e não
JSON texto) porque o Postgres o armazena decomposto em binário, deixando os
operadores ->/->>' do staging muito mais baratos que reparsear texto.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

# Caminhos resolvidos a partir DESTE arquivo (scripts/ -> raiz do repo), não
# do diretório atual do shell — o script funciona igual chamado de qualquer
# lugar (host, container com o repo em /opt/pipeline, runner do Actions).
REPO_ROOT = Path(__file__).resolve().parents[1]

# Mesmos nomes de env var dos scripts de ingestão: se a ingestão foi apontada
# pra outro arquivo (a DAG usa um arquivo POR INTERVALO), este script
# acompanha pela mesma variável — uma única fonte de verdade pro caminho.
CONVENIOS_PATH = Path(
    os.environ.get("CONVENIOS_RAW_OUTPUT", REPO_ROOT / "data" / "raw" / "convenios.jsonl")
)
EMENDAS_PATH = Path(
    os.environ.get("EMENDAS_RAW_OUTPUT", REPO_ROOT / "data" / "raw" / "emendas.jsonl")
)

# Inserimos em lotes de 1000 linhas por INSERT (execute_values), não 1 INSERT
# por linha: cada INSERT é uma ida-e-volta ao servidor — em lote, milhares de
# registros viram poucas dezenas de round-trips.
TAMANHO_LOTE = 1000


def _conectar() -> psycopg2.extensions.connection:
    """Conecta usando as mesmas env vars que o docker-compose injeta nos
    containers (defaults pra rodar da sua máquina contra o compose). No
    GitHub Actions, os MESMOS nomes vêm dos secrets do repositório apontando
    pro Postgres gerenciado (Neon) — o script não sabe nem precisa saber em
    qual dos dois mundos está."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "gastos"),
        user=os.environ.get("POSTGRES_USER", "gastos"),
        password=os.environ.get("POSTGRES_PASSWORD", "gastos"),
    )


def _ler_jsonl(caminho: Path) -> list[str]:
    """Lê o JSONL como lista de strings (uma por registro), ignorando linhas
    em branco. Falha com mensagem acionável se a ingestão não rodou antes."""
    if not caminho.exists():
        raise SystemExit(
            f"{caminho} não existe. Rode a ingestão antes — na DAG do Airflow "
            "essa ordem já é garantida pelas dependências entre tasks."
        )
    with caminho.open("r", encoding="utf-8") as arquivo:
        return [linha.strip() for linha in arquivo if linha.strip()]


def _inserir_lotes(cur, tabela: str, linhas: list[str], colunas_extras: str = "",
                   valores_extras: tuple = ()) -> int:
    """INSERT em lotes com cast pra jsonb no servidor: o Postgres valida o
    JSON na entrada — linha corrompida = erro imediato e rollback, em vez de
    lixo silencioso no warehouse."""
    total = 0
    template = f"(%s::jsonb{', %s' * len(valores_extras)})"
    for inicio in range(0, len(linhas), TAMANHO_LOTE):
        lote = [(linha, *valores_extras) for linha in linhas[inicio:inicio + TAMANHO_LOTE]]
        execute_values(
            cur,
            f"insert into {tabela} (dado{colunas_extras}) values %s",
            lote,
            template=template,
        )
        total += len(lote)
    return total


def carregar_convenios() -> None:
    """Carga INCREMENTAL de convênios: delete-insert do intervalo informado."""
    # As datas do intervalo vêm da DAG (que as recebe do Airflow —
    # data_interval_start/end). Exigi-las explicitamente, em vez de assumir
    # um default, evita o pior bug de carga incremental: apagar/gravar a
    # janela errada em silêncio.
    try:
        janela_inicio = date.fromisoformat(os.environ["CARGA_JANELA_INICIO"])
        janela_fim = date.fromisoformat(os.environ["CARGA_JANELA_FIM"])
    except KeyError as faltante:
        raise SystemExit(
            f"variável {faltante} não definida — a carga incremental precisa "
            "saber QUAL intervalo está carregando (AAAA-MM-DD)."
        )

    linhas = _ler_jsonl(CONVENIOS_PATH)
    conexao = _conectar()
    try:
        # Transação única: o DELETE da janela e o INSERT novo entram juntos
        # ou nada muda — uma falha no meio faz ROLLBACK e a carga anterior
        # daquela janela continua válida.
        with conexao:
            with conexao.cursor() as cur:
                # Auto-provisionamento + MIGRAÇÃO LEVE: o create cobre a
                # primeira execução; os ALTER ... IF NOT EXISTS cobrem quem
                # já tinha a tabela da era truncar-e-recarregar (viram no-op
                # depois da primeira vez). Pra colunas novas, isso substitui
                # uma ferramenta de migração formal sem perder segurança.
                cur.execute("""
                    create table if not exists raw.convenios (
                        dado          jsonb        not null,
                        carregado_em  timestamptz  not null default now()
                    )
                """)
                cur.execute("alter table raw.convenios add column if not exists janela_inicio date")
                cur.execute("alter table raw.convenios add column if not exists janela_fim date")

                # O coração do delete-insert: some só o que é DESTA janela.
                cur.execute(
                    "delete from raw.convenios where janela_inicio = %s and janela_fim = %s",
                    (janela_inicio, janela_fim),
                )
                apagadas = cur.rowcount

                total = _inserir_lotes(
                    cur, "raw.convenios", linhas,
                    colunas_extras=", janela_inicio, janela_fim",
                    valores_extras=(janela_inicio, janela_fim),
                )

    finally:
        conexao.close()

    print(
        f"raw.convenios [{janela_inicio} a {janela_fim}]: "
        f"{apagadas} linhas antigas da janela removidas, {total} inseridas."
    )
    if total == 0:
        # Janela vazia é NORMAL neste endpoint (descoberto na prática: o
        # índice de "última atualização" da fonte atrasa semanas) — mas fica
        # visível no log pra ninguém confundir com ingestão quebrada.
        print("AVISO: nenhum convênio nesta janela — normal em datas recentes "
              "(atraso de indexação da fonte).", file=sys.stderr)


def carregar_emendas() -> None:
    """Carga SNAPSHOT de emendas: truncar-e-recarregar (ver o cabeçalho —
    a fonte devolve estado acumulado por ano, não eventos)."""
    linhas = _ler_jsonl(EMENDAS_PATH)
    conexao = _conectar()
    try:
        with conexao:
            with conexao.cursor() as cur:
                cur.execute("""
                    create table if not exists raw.emendas (
                        dado          jsonb        not null,
                        carregado_em  timestamptz  not null default now()
                    )
                """)
                # TRUNCATE (não DELETE): zera desalocando páginas inteiras,
                # sem varrer linha a linha — e dentro da transação é
                # reversível: falha no meio = ROLLBACK = snapshot anterior
                # permanece intacto.
                cur.execute("truncate table raw.emendas")
                total = _inserir_lotes(cur, "raw.emendas", linhas)
    finally:
        conexao.close()

    print(f"raw.emendas: snapshot substituído com {total} registros.")
    if total == 0:
        print("AVISO: raw.emendas ficou vazia — confira os anos consultados.",
              file=sys.stderr)


def main() -> None:
    # Subcomando em vez de "carrega tudo sempre": a DAG orquestra cada fonte
    # como task própria (retry, log e gate de execução independentes) — o
    # script só precisa saber carregar UMA coisa bem por invocação.
    if len(sys.argv) != 2 or sys.argv[1] not in ("convenios", "emendas"):
        raise SystemExit("uso: python scripts/carregar_raw_postgres.py {convenios|emendas}")

    if sys.argv[1] == "convenios":
        carregar_convenios()
    else:
        carregar_emendas()


if __name__ == "__main__":
    main()
