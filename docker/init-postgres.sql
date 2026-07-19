-- Executado UMA ÚNICA VEZ pela imagem oficial do Postgres, na primeira
-- inicialização do volume de dados (convenção /docker-entrypoint-initdb.d/ —
-- ver docker-compose.yml). Se você apagar o volume (docker compose down -v),
-- este script roda de novo na próxima subida.
--
-- Ele roda conectado ao banco "gastos" (o POSTGRES_DB do compose), com o
-- usuário "gastos", que dentro deste container é superusuário.

-- Schema pro dado BRUTO: scripts/carregar_raw_postgres.py despeja aqui o
-- JSONL baixado da API, um registro por linha, em coluna JSONB — sem nenhuma
-- transformação. Separar "bruto" de "modelado" em schemas diferentes é o
-- mesmo padrão de camadas do mundo DuckDB (lá: arquivos JSONL vs schemas do
-- dbt), só que agora dentro de um servidor de banco.
CREATE SCHEMA IF NOT EXISTS raw;

-- Os schemas ANALÍTICOS ("analytics_staging" e "analytics_marts") NÃO são
-- criados aqui de propósito: quem cria é o próprio dbt na primeira execução
-- (`dbt run --target postgres`). O nome vem da convenção generate_schema_name
-- do dbt — schema base do profile ("analytics", ver transform/profiles.yml)
-- + o +schema declarado em dbt_project.yml ("staging"/"marts") — exatamente
-- como no DuckDB os schemas viram "main_staging"/"main_marts".

-- Banco de METADADOS do Airflow (execuções de DAG, estado de task, usuários
-- da UI). Banco SEPARADO no mesmo servidor: os dados do pipeline e o estado
-- interno do orquestrador não devem se misturar — dropar/recarregar o
-- warehouse não pode ter risco nenhum de apagar o histórico do Airflow.
CREATE DATABASE airflow OWNER gastos;
