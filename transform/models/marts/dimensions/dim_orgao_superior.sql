-- DIMENSÃO: um órgão superior por linha (ex.: "Ministério da Educação"),
-- que é quem efetivamente autoriza/libera o dinheiro do convênio. Mesma
-- lógica de dim_municipio: deduplicação com ROW_NUMBER() + surrogate key.

with base as (

    select distinct
        orgao_superior_codigo,
        orgao_superior_nome,
        orgao_superior_sigla
    from {{ ref('stg_convenios') }}

),

deduplicado as (

    select
        *,
        row_number() over (
            partition by orgao_superior_codigo
            order by orgao_superior_nome
        ) as linha_duplicada

    from base

)

select
    md5(orgao_superior_codigo)     as sk_orgao_superior,
    orgao_superior_codigo          as codigo_orgao_superior,
    orgao_superior_nome            as nome_orgao_superior,
    orgao_superior_sigla           as sigla_orgao_superior
from deduplicado
where linha_duplicada = 1
