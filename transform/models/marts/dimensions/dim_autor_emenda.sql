-- DIMENSÃO: um autor de emenda por linha. "Autor" nem sempre é uma pessoa —
-- pode ser um deputado/senador individual, ou uma bancada estadual/comissão
-- (entidade coletiva). Por isso a chave natural é o PAR (nome, tipo_emenda),
-- não só o nome: em tese, os nomes já não deveriam colidir entre um
-- indivíduo e uma bancada, mas usar o par é mais seguro do que assumir isso.
--
-- LIMITAÇÃO HONESTA (vale registrar, porque isso é dado sobre gente real):
-- esse endpoint da API não traz um identificador estável (tipo CPF ou ID de
-- parlamentar) pro autor, só o nome em texto. Na prática, nomes de
-- deputados/senadores federais são suficientemente únicos pra esse painel,
-- mas isso não é uma garantia matemática — é a limitação da fonte de dados,
-- não uma escolha nossa.

with base as (

    select distinct
        nome_autor,
        tipo_emenda
    from {{ ref('stg_emendas') }}

),

deduplicado as (

    select
        *,
        row_number() over (
            partition by nome_autor, tipo_emenda
            order by nome_autor
        ) as linha_duplicada

    from base

)

select
    md5(nome_autor || '|' || tipo_emenda)  as sk_autor_emenda,
    nome_autor,
    tipo_emenda,
    -- Facilita filtrar "só pessoas físicas" no frontend sem precisar
    -- conhecer os valores exatos de tipo_emenda vindos da API. Usamos ILIKE
    -- (não igualdade exata) de propósito: não temos como validar contra a
    -- API real ainda qual é a grafia exata desse valor (ex.: "Individual"
    -- vs "Emenda Individual"), e um `= 'Individual'` que não bater
    -- silenciosamente classificaria todo mundo como "não individual".
    (tipo_emenda ilike '%individual%')      as eh_parlamentar_individual
from deduplicado
where linha_duplicada = 1
