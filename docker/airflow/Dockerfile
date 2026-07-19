# Imagem customizada do Airflow: a oficial + as dependências do NOSSO
# pipeline. Usada pelos serviços airflow-* do docker-compose.yml.
#
# Versão PINADA (não "latest") pelo mesmo motivo do requirements.txt na raiz:
# uma build hoje e outra daqui a 6 meses têm que produzir o mesmo ambiente —
# "funciona na minha máquina" geralmente é falta disso.
#
# Airflow 3 (série atual — 3.3.0 saiu em 06/07/2026): arquitetura nova em
# relação ao 2.x — api-server no lugar do webserver, dag-processor como
# processo obrigatório separado do scheduler, e tasks falando com o
# api-server por HTTP em vez de acessarem o banco de metadados direto. O
# docker-compose.yml explica cada peça; aqui só importa que a MESMA imagem
# serve todos os papéis (api-server, scheduler, dag-processor, init) — quem
# define o papel é o `command` de cada serviço no compose.
FROM apache/airflow:3.3.0-python3.11

# --- Dependências da INGESTÃO e dos scripts de carga/publicação -------------
# Instaladas no ambiente Python do próprio Airflow (as tasks BashOperator
# rodam "python ingestion/..." nesse ambiente):
#   requests        -> chamar a API do Portal da Transparência (mesma versão
#                      do requirements.txt da raiz, pra manter paridade).
#   psycopg2-binary -> driver Postgres dos scripts carregar_raw_postgres.py e
#                      publicar_site.py. A variante "-binary" evita compilar C
#                      dentro da imagem (a recomendação de compilar do fonte
#                      vale pra produção de alta escala, não pra um lab local).
RUN pip install --no-cache-dir \
    requests==2.32.3 \
    psycopg2-binary==2.9.9

# --- dbt num VIRTUALENV SEPARADO ---------------------------------------------
# Este é um problema clássico (e real) de quem opera Airflow: tanto o Airflow
# quanto o dbt são aplicações Python grandes, com dezenas de dependências cada
# — e as versões que um exige conflitam com as do outro com frequência
# (protobuf, jinja2, click...). Instalar os dois no mesmo ambiente funciona
# até o dia em que um upgrade de um quebra o outro silenciosamente.
#
# A solução padrão da comunidade: o dbt vive num virtualenv próprio, isolado,
# e a DAG o invoca pelo caminho completo (/opt/dbt-venv/bin/dbt) em vez de
# "dbt" no PATH. Cada um evolui sem pisar no outro.
USER root
RUN mkdir -p /opt/dbt-venv && chown -R airflow: /opt/dbt-venv
USER airflow
RUN python -m venv /opt/dbt-venv && \
    /opt/dbt-venv/bin/pip install --no-cache-dir \
        dbt-core==1.8.9 \
        dbt-postgres==1.8.2
# dbt-core na MESMA versão do requirements.txt da raiz (1.8.9): os models são
# os mesmos arquivos .sql — rodar versões diferentes do motor dbt dentro e
# fora do Docker seria pedir pra caçar diferença de comportamento um dia.
# dbt-postgres é o adapter que ensina o dbt a falar com Postgres, papel que o
# dbt-duckdb cumpre no mundo do site estático.
