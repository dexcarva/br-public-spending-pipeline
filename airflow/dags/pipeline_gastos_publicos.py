"""
DAG do pipeline de gastos públicos — versão INCREMENTAL (Fase 7.5).

A mudança de filosofia em relação à primeira versão desta DAG: antes, cada
run baixava a janela INTEIRA de 3 meses e recarregava o warehouse do zero —
regime permanente de horas, custo O(janela). Agora cada run cobre uma janela
ROLANTE de 7 dias (o porquê de "rolante" está no comentário de
JANELA_INICIO_API abaixo — resumo: o índice temporal da fonte é MÓVEL, e
partições fixas por dia se esvaziam com o tempo), e o histórico acumula no
Postgres via delete-insert idempotente + dedupe por id no staging. Regime
permanente proporcional ao que a fonte re-tocou recentemente, não à janela
histórica inteira.

O mecanismo que torna isso natural é o modelo de INTERVALOS do Airflow, que
a versão anterior desligava de propósito: uma run @daily não significa
"rodou no dia X", significa "processa o intervalo de dados [X, X+1)". O
Airflow injeta esse intervalo em cada task (data_interval_start/end via
templates Jinja) e, com catchup=True, sabe criar sozinho as runs de
intervalos passados que nunca rodaram — backfill vira um comando, não um
script improvisado:

    airflow backfill create --dag-id pipeline_gastos_publicos \
        --from-date 2026-04-20 --to-date 2026-04-27

(foi exatamente assim que a amostra inicial deste warehouse foi carregada.)

ESTRATÉGIA POR FONTE (o porquê completo está em scripts/carregar_raw_postgres.py):
  - Convênios: incremental por intervalo — o filtro da API é por "última
    atualização", então a janela de 1 dia captura o que mudou naquele dia.
  - Emendas: snapshot (a API devolve o acumulado do ano, não eventos) —
    refresh completo, mas SÓ na run do intervalo mais recente (gate
    LatestOnlyOperator abaixo): re-baixar o mesmo snapshot em cada run de
    backfill seria pagar minutos de API por dado idêntico.

POR QUE BashOperator em tudo (e não PythonOperator): os passos JÁ SÃO
programas de linha de comando prontos. Chamar como subprocesso mantém os
scripts 100% utilizáveis fora do Airflow — o orquestrador é uma camada em
cima, não uma dependência deles.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum

# Imports do jeito do AIRFLOW 3: o DAG vem do Task SDK (airflow.sdk) — a
# interface pública pra quem escreve DAGs — e TODO operador vem de um
# provider (os básicos, do provider "standard").
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.latest_only import LatestOnlyOperator
from airflow.sdk import DAG

# Onde o repositório está montado dentro dos containers do Airflow (ver o
# volume "./:/opt/pipeline" no docker-compose.yml).
RAIZ_PIPELINE = "/opt/pipeline"

# O dbt vive num virtualenv próprio, isolado do Python do Airflow (motivo
# detalhado em docker/airflow/Dockerfile).
DBT = "/opt/dbt-venv/bin/dbt"

# JANELA ROLANTE de 7 dias terminando no dia do intervalo — e o porquê é a
# descoberta mais importante do projeto sobre esta fonte: o filtro de data de
# /convenios enxerga um carimbo de "última atualização" que a própria fonte
# RE-CARIMBA em lote (medido na prática em 2026-07-19: janelas de julho
# devolviam 0 registros de manhã e páginas cheias à tarde; janelas de abril
# que renderam 8.000+ registros esvaziaram horas depois). Janelas do passado
# NÃO são partições estáveis — o dado "mora" sempre perto do último
# reprocessamento da fonte. Fatiar por dia fixo perderia quase tudo; a
# janela rolante de 7 dias colhe onde o índice móvel está, e a dupla
# delete-insert por janela (carregar_raw) + dedupe por id (staging) torna a
# sobreposição entre runs consecutivas inofensiva POR DESENHO — recapturar o
# mesmo convênio é atualização, não duplicata.
# Templates preenchidos POR RUN pelo Airflow. DUAS pegadinhas do Airflow 3
# pagas com runs quebradas (documentadas pra ninguém repetir):
#   1. RUN MANUAL não tem logical_date nem data interval (ficam None) —
#      qualquer template com data_interval_start/ds explode com "undefined".
#      A âncora universal é dag_run.run_after: existe em TODO tipo de run
#      (manual = agora; agendado/backfill = o dia do intervalo).
#   2. run_after chega ao template como datetime PURO do Python, não
#      pendulum — .subtract() não existe nele. A aritmética portátil é
#      "data - macros.timedelta(...)", o utilitário que o próprio Airflow
#      injeta no contexto Jinja pra exatamente isso.
JANELA_INICIO_API = "{{ (dag_run.run_after - macros.timedelta(days=6)).strftime('%d/%m/%Y') }}"
JANELA_FIM_API = "{{ dag_run.run_after.strftime('%d/%m/%Y') }}"
JANELA_INICIO_ISO = "{{ (dag_run.run_after - macros.timedelta(days=6)).strftime('%Y-%m-%d') }}"
JANELA_FIM_ISO = "{{ dag_run.run_after.strftime('%Y-%m-%d') }}"

with DAG(
    dag_id="pipeline_gastos_publicos",
    description="Ingestão incremental Portal da Transparência -> Postgres -> dbt -> JSON do site",
    schedule="@daily",
    # catchup=True AGORA FAZ SENTIDO (a versão full-reload o desligava): se a
    # DAG ficar 3 dias pausada/fora do ar, ao voltar o Airflow cria as 3 runs
    # perdidas e cada uma carrega o seu dia — o warehouse se completa sozinho,
    # sem intervenção. É o mecanismo padrão de recuperação de atraso.
    start_date=pendulum.datetime(2026, 7, 12, tz="America/Sao_Paulo"),
    catchup=True,
    # Runs de intervalos diferentes NÃO podem rodar em paralelo aqui, por dois
    # motivos práticos: o rate limit da API é por chave (paralelizar não
    # acelera, só arrisca a suspensão de 8h), e o dbt reconstruiria as mesmas
    # tabelas concorrentemente. Backfill de N dias = N runs em série, rápidas.
    max_active_runs=1,
    default_args={
        # Retry por task: instabilidade de API/rede se resolve sozinha com
        # frequência (segunda linha de defesa além do backoff do cliente
        # HTTP). Com a carga delete-insert idempotente, re-executar é seguro.
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["portal-transparencia", "dbt", "postgres", "incremental"],
) as dag:

    # --- 1a. Convênios do intervalo (1 dia) ---------------------------------
    # O intervalo entra por env vars — as MESMAS que o script já aceitava pra
    # uso manual, agora preenchidas pelo template. append_env=True preserva o
    # ambiente do container (chave da API etc.) em vez de substituí-lo.
    # Arquivo de saída POR INTERVALO (data/raw/incremental/): runs diferentes
    # nunca pisam no arquivo umas das outras, e o disco vira um espelho
    # navegável do que cada intervalo trouxe.
    # pool: LIÇÃO APRENDIDA NA PRÁTICA — o backfill do Airflow 3 roda vários
    # intervalos EM PARALELO (ele não respeita o max_active_runs da DAG), e
    # na primeira semana de backfill deste projeto 8 extrações dispararam
    # juntas contra a mesma chave de API. Um pool com 1 slot é a trava
    # global certa: qualquer task no pool "api_portal_transparencia" espera
    # o slot vagar, não importa de qual run ela seja. (Os pools são criados
    # pelo airflow-init no docker-compose.yml.)
    extract_convenios = BashOperator(
        task_id="extract_convenios",
        bash_command=f"cd {RAIZ_PIPELINE} && python ingestion/extract_convenios.py",
        env={
            "CONVENIOS_DATA_INICIAL": JANELA_INICIO_API,
            "CONVENIOS_DATA_FINAL": JANELA_FIM_API,
            "CONVENIOS_RAW_OUTPUT": f"data/raw/incremental/convenios_{JANELA_FIM_ISO}.jsonl",
        },
        append_env=True,
        pool="api_portal_transparencia",
    )

    # --- 2a. Carga incremental (delete-insert da janela) --------------------
    carregar_convenios = BashOperator(
        task_id="carregar_convenios",
        bash_command=f"cd {RAIZ_PIPELINE} && python scripts/carregar_raw_postgres.py convenios",
        env={
            "CARGA_JANELA_INICIO": JANELA_INICIO_ISO,
            "CARGA_JANELA_FIM": JANELA_FIM_ISO,
            "CONVENIOS_RAW_OUTPUT": f"data/raw/incremental/convenios_{JANELA_FIM_ISO}.jsonl",
        },
        append_env=True,
    )

    # --- Gate: partes que só fazem sentido na run MAIS RECENTE --------------
    # LatestOnlyOperator: em runs de backfill/catchup (intervalos passados),
    # ele PULA tudo que está diretamente abaixo dele; na run do intervalo
    # corrente (e em disparos manuais), deixa passar. É o padrão pra misturar,
    # numa mesma DAG, trabalho por-intervalo (convênios) com trabalho
    # só-no-presente (snapshot de emendas, publicação do site).
    somente_run_mais_recente = LatestOnlyOperator(task_id="somente_run_mais_recente")

    # --- 1b/2b. Emendas: snapshot completo, só na run mais recente ----------
    # Sem override de EMENDAS_ANOS: vale o default do script (3 anos), porque
    # a carga é truncar-e-recarregar — recarregar SÓ o ano corrente apagaria
    # os anteriores. Snapshot se substitui inteiro ou não se substitui.
    extract_emendas = BashOperator(
        task_id="extract_emendas",
        bash_command=f"cd {RAIZ_PIPELINE} && python ingestion/extract_emendas.py",
        # Mesma chave de API dos convênios -> mesmo pool, mesma fila.
        pool="api_portal_transparencia",
    )

    # (carregar_* fica FORA dos pools de propósito: são escritas de
    # milissegundos em linhas distintas — janelas diferentes no delete-insert
    # — e o MVCC do Postgres isola leitores de escritores sem trava nossa.)
    carregar_emendas = BashOperator(
        task_id="carregar_emendas",
        bash_command=f"cd {RAIZ_PIPELINE} && python scripts/carregar_raw_postgres.py emendas",
    )

    # --- 3. Transformação (dbt) ---------------------------------------------
    # Roda em TODA run (inclusive backfill): reconstruir os marts a partir do
    # raw acumulado leva segundos nesse volume, e cada intervalo carregado já
    # sai testado e refletido no warehouse.
    #
    # trigger_rule="none_failed": o default (all_success) PULARIA o dbt nas
    # runs de backfill, porque o ramo de emendas chega SKIPPED (gate acima) —
    # e skip se propaga por default. "none_failed" diz o que queremos de
    # verdade: rode se nenhum pai FALHOU (sucesso ou pulado, tanto faz).
    # pool="dbt_warehouse" (1 slot): TODO dbt run reconstrói as MESMAS
    # tabelas (fct_convenios etc.) — dois simultâneos colidem no DROP/CREATE
    # com "Database Error", exatamente o que aconteceu quando o backfill
    # paralelo desta semana rodou sem o pool. Serializar o dbt entre runs não
    # atrasa quase nada (segundos por run) e elimina a classe inteira de erro.
    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd {RAIZ_PIPELINE}/transform && {DBT} run --profiles-dir . --target postgres",
        trigger_rule="none_failed",
        pool="dbt_warehouse",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {RAIZ_PIPELINE}/transform && {DBT} test --profiles-dir . --target postgres",
        pool="dbt_warehouse",
    )

    # --- 4. Publicação dos JSON do site — só na run mais recente ------------
    # Em backfill, publicar estados intermediários do passado não faz sentido
    # (o site mostra o presente) — por isso o gate também aponta pra cá: numa
    # run de intervalo antigo, esta task chega SKIPPED via LatestOnly. O push
    # continua manual e fora da DAG: commit é publicação pública — decisão de
    # gente, não de cron.
    publicar_site = BashOperator(
        task_id="publicar_site",
        bash_command=f"cd {RAIZ_PIPELINE} && python scripts/publicar_site.py",
    )

    # O grafo completo (>> declara "roda depois de"):
    #
    #   extract_convenios >> carregar_convenios >------------+
    #                                                        v
    #   somente_run_mais_recente >> extract_emendas          |
    #                               >> carregar_emendas >> dbt_run >> dbt_test >> publicar_site
    #   somente_run_mais_recente >-----------------------------------------------^
    #
    extract_convenios >> carregar_convenios >> dbt_run
    somente_run_mais_recente >> extract_emendas >> carregar_emendas >> dbt_run
    dbt_run >> dbt_test >> publicar_site
    somente_run_mais_recente >> publicar_site
