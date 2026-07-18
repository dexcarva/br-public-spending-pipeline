-- Camada de STAGING das emendas parlamentares: achata o JSON e corrige
-- tipos, mesmo papel de stg_convenios.sql. A diferença que importa aqui é
-- que os campos de valor vêm como texto em formato numérico brasileiro
-- ("1.234.567,89") — normalizamos isso com o macro normalizar_valor_brl()
-- (transform/macros/normalizar_valor_brl.sql) em vez de um CAST direto, que
-- falharia nesse formato.

with emendas_bruto as (

    select *
    from read_json_auto('{{ var("raw_emendas_path") }}')

),

renomeado as (

    select
        codigoEmenda                                       as codigo_emenda,
        numeroEmenda                                        as numero_emenda,
        ano                                                 as ano,

        -- tipoEmenda diz se o autor é uma pessoa (Individual) ou uma
        -- entidade coletiva (Bancada estadual, Comissão, Relator-Geral) —
        -- crucial manter esse campo explícito: sem ele, um ranking "por
        -- político" acabaria misturando bancadas inteiras como se fossem
        -- uma pessoa só.
        tipoEmenda                                          as tipo_emenda,
        nomeAutor                                           as nome_autor,

        localidadeDoGasto                                   as localidade_gasto,
        funcao                                              as funcao,
        subfuncao                                            as subfuncao,

        -- Ciclo orçamentário (ver o cabeçalho de ingestion/extract_emendas.py
        -- pra explicação de cada estágio): empenhado -> liquidado -> pago.
        {{ normalizar_valor_brl('valorEmpenhado') }}         as valor_empenhado,
        {{ normalizar_valor_brl('valorLiquidado') }}         as valor_liquidado,
        {{ normalizar_valor_brl('valorPago') }}              as valor_pago,
        {{ normalizar_valor_brl('valorRestoInscrito') }}     as valor_resto_inscrito,
        {{ normalizar_valor_brl('valorRestoCancelado') }}    as valor_resto_cancelado,
        {{ normalizar_valor_brl('valorRestoPago') }}         as valor_resto_pago

    from emendas_bruto

)

select * from renomeado
-- Emendas sem autor identificado não servem pra dim_autor_emenda (não têm
-- com o que preencher a chave natural) — mesma lógica de filtro aplicada em
-- stg_convenios.sql.
where nome_autor is not null
  and valor_empenhado is not null
