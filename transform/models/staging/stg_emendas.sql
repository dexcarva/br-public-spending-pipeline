-- Camada de STAGING das emendas parlamentares: achata o JSON e corrige
-- tipos, mesmo papel de stg_convenios.sql. A diferença que importa aqui é
-- que os campos de valor vêm como texto em formato numérico brasileiro
-- ("1.234.567,89") — normalizamos isso com o macro normalizar_valor_brl()
-- (transform/macros/normalizar_valor_brl.sql) em vez de um CAST direto, que
-- falharia nesse formato. O macro é escrito só com SQL padrão, então é o
-- MESMO nos dois ramos abaixo — mais uma fronteira que a portabilidade
-- entre targets não precisou atravessar.
--
-- Assim como stg_convenios, este model roda em DOIS bancos (targets "dev"/
-- DuckDB e "docker"/Postgres — ver a explicação completa no cabeçalho de
-- stg_convenios.sql). O contrato dos dois ramos é idêntico lá e cá: mesmas
-- colunas, mesmos nomes, mesmos tipos, pra tudo rio abaixo não saber qual
-- banco o alimentou.

with renomeado as (

{% if target.type == 'postgres' %}

    -- Ramo POSTGRES: extração do JSONB de raw.emendas (carregada por
    -- scripts/carregar_raw_postgres.py). ->> devolve TEXTO sempre; os campos
    -- que não são texto de verdade ganham CAST explícito (ano). Os campos de
    -- valor ficam sem CAST aqui de propósito: eles são STRINGS em formato BR
    -- na API — quem os converte é o macro, igual no ramo DuckDB.
    select
        dado->>'codigoEmenda'                                as codigo_emenda,
        dado->>'numeroEmenda'                                as numero_emenda,
        cast(dado->>'ano' as int)                            as ano,

        -- tipoEmenda diz se o autor é uma pessoa (Individual) ou uma
        -- entidade coletiva (Bancada estadual, Comissão, Relator-Geral) —
        -- crucial manter esse campo explícito: sem ele, um ranking "por
        -- político" acabaria misturando bancadas inteiras como se fossem
        -- uma pessoa só.
        dado->>'tipoEmenda'                                  as tipo_emenda,
        dado->>'nomeAutor'                                   as nome_autor,

        dado->>'localidadeDoGasto'                           as localidade_gasto,
        dado->>'funcao'                                      as funcao,
        dado->>'subfuncao'                                   as subfuncao,

        -- Ciclo orçamentário (ver o cabeçalho de ingestion/extract_emendas.py
        -- pra explicação de cada estágio): empenhado -> liquidado -> pago.
        {{ normalizar_valor_brl("dado->>'valorEmpenhado'") }}     as valor_empenhado,
        {{ normalizar_valor_brl("dado->>'valorLiquidado'") }}     as valor_liquidado,
        {{ normalizar_valor_brl("dado->>'valorPago'") }}          as valor_pago,
        {{ normalizar_valor_brl("dado->>'valorRestoInscrito'") }} as valor_resto_inscrito,
        {{ normalizar_valor_brl("dado->>'valorRestoCancelado'") }} as valor_resto_cancelado,
        {{ normalizar_valor_brl("dado->>'valorRestoPago'") }}     as valor_resto_pago

    from {{ source('raw', 'emendas') }}

{% else %}

    -- Ramo DUCKDB: lê o JSONL direto do disco com inferência automática de
    -- schema (ver o comentário sobre read_json_auto em stg_convenios.sql).
    select
        codigoEmenda                                        as codigo_emenda,
        numeroEmenda                                        as numero_emenda,
        ano                                                 as ano,

        -- (mesmo comentário do tipoEmenda do ramo Postgres acima)
        tipoEmenda                                          as tipo_emenda,
        nomeAutor                                           as nome_autor,

        localidadeDoGasto                                   as localidade_gasto,
        funcao                                              as funcao,
        subfuncao                                            as subfuncao,

        {{ normalizar_valor_brl('valorEmpenhado') }}         as valor_empenhado,
        {{ normalizar_valor_brl('valorLiquidado') }}         as valor_liquidado,
        {{ normalizar_valor_brl('valorPago') }}              as valor_pago,
        {{ normalizar_valor_brl('valorRestoInscrito') }}     as valor_resto_inscrito,
        {{ normalizar_valor_brl('valorRestoCancelado') }}    as valor_resto_cancelado,
        {{ normalizar_valor_brl('valorRestoPago') }}         as valor_resto_pago

    from read_json_auto('{{ var("raw_emendas_path") }}')

{% endif %}

)

-- Trecho COMPARTILHADO entre os targets (só SQL padrão daqui pra baixo).
select * from renomeado
-- Emendas sem autor identificado não servem pra dim_autor_emenda (não têm
-- com o que preencher a chave natural) — mesma lógica de filtro aplicada em
-- stg_convenios.sql.
where nome_autor is not null
  and valor_empenhado is not null
