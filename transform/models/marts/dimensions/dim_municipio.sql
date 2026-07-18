-- DIMENSÃO: um município por linha. Dimensões guardam "quem/o quê/onde"
-- (atributos descritivos), separadas da tabela fato (que guarda "quanto" e
-- "quando"). Isso é modelagem dimensional clássica (esquema estrela):
-- várias linhas de fato apontam pra uma linha de dimensão, em vez de repetir
-- nome do município, UF e região em cada convênio.

with base as (

    select distinct
        municipio_codigo_ibge,
        municipio_nome,
        municipio_uf_sigla,
        municipio_uf_nome,
        municipio_codigo_regiao,
        municipio_nome_regiao
    from {{ ref('stg_convenios') }}

),

-- Por que ROW_NUMBER() além do DISTINCT: o código IBGE já é, na teoria, uma
-- chave única por município — mas dado do mundo real às vezes tem
-- inconsistência (ex.: o mesmo código aparecendo com grafias de nome
-- ligeiramente diferentes entre registros). ROW_NUMBER() particionado pelo
-- código garante 1 linha por município mesmo se isso acontecer, escolhendo
-- de forma determinística qual versão manter (aqui, a primeira em ordem
-- alfabética do nome).
deduplicado as (

    select
        *,
        row_number() over (
            partition by municipio_codigo_ibge
            order by municipio_nome
        ) as linha_duplicada

    from base

)

select
    -- Surrogate key: um hash do código IBGE, em vez de usar o próprio código
    -- IBGE como chave primária da dimensão. Nesse caso específico o código
    -- IBGE já seria suficiente sozinho (é estável e único) — geramos a
    -- surrogate key mesmo assim pra seguir a prática padrão de modelagem
    -- dimensional: a tabela fato nunca deveria depender diretamente da
    -- identidade de um sistema de origem, porque se um dia trocarmos de
    -- fonte de dados (ou juntarmos com outra fonte que também tem
    -- "código"), só essa dimensão muda — a fato continua igual.
    md5(municipio_codigo_ibge)     as sk_municipio,
    municipio_codigo_ibge          as codigo_ibge,
    municipio_nome                 as nome_municipio,
    municipio_uf_sigla             as uf_sigla,
    municipio_uf_nome              as uf_nome,
    municipio_codigo_regiao        as codigo_regiao,
    municipio_nome_regiao          as nome_regiao
from deduplicado
where linha_duplicada = 1
