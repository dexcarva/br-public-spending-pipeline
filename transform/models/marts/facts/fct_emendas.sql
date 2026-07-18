-- TABELA FATO: uma linha por emenda parlamentar, com o ciclo orçamentário
-- completo (empenhado -> liquidado -> pago) e a taxa de execução — a
-- métrica central pra responder "quem propõe muito e entrega pouco".

select
    stg.codigo_emenda,
    stg.numero_emenda,
    stg.ano,
    stg.localidade_gasto,
    stg.funcao,
    stg.subfuncao,

    dim_autor.sk_autor_emenda,

    stg.valor_empenhado,
    stg.valor_liquidado,
    stg.valor_pago,
    stg.valor_resto_inscrito,
    stg.valor_resto_cancelado,
    stg.valor_resto_pago,

    -- taxa_execucao = quanto do que foi empenhado (destinado) realmente
    -- virou pagamento. NULLIF evita divisão por zero quando valor_empenhado
    -- é 0 (a taxa fica NULL nesse caso, em vez de erro ou um falso "0%").
    -- Pode passar de 1.0 legitimamente: "restos a pagar" de anos anteriores
    -- às vezes são pagos no ano corrente, então valor_pago não é sempre
    -- <= valor_empenhado do mesmo ano — por isso não criamos um teste de
    -- qualidade travando essa relação.
    stg.valor_pago / nullif(stg.valor_empenhado, 0) as taxa_execucao

from {{ ref('stg_emendas') }} as stg
-- LEFT JOIN de propósito (mesmo raciocínio do fct_convenios.sql): se a
-- dimensão um dia não cobrir um autor que a staging tem, queremos que o
-- teste not_null do schema.yml denuncie isso, não que a linha suma calada
-- num INNER JOIN.
left join {{ ref('dim_autor_emenda') }} as dim_autor
    on stg.nome_autor = dim_autor.nome_autor
    and stg.tipo_emenda = dim_autor.tipo_emenda
