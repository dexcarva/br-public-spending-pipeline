-- TABELA FATO: uma linha por convênio, com as métricas financeiras (o
-- "quanto") e chaves estrangeiras (sk_*) pra cada dimensão (o "quem/onde").
-- É essa tabela que o export_static_json.py consulta pra gerar os JSON que
-- alimentam o site.

select
    stg.id_convenio,
    stg.numero_processo,
    stg.situacao,

    stg.data_publicacao,
    stg.data_inicio_vigencia,
    stg.data_final_vigencia,
    -- Pré-calculamos o mês de referência aqui (em vez de deixar pro
    -- frontend derivar de uma data completa) porque é exatamente o grão que
    -- o gráfico de série temporal do site precisa — mais uma transformação
    -- feita uma vez no pipeline do que repetida em JavaScript no navegador
    -- de cada visitante.
    date_trunc('month', stg.data_publicacao) as mes_referencia,

    dim_municipio.sk_municipio,
    dim_orgao_superior.sk_orgao_superior,

    stg.valor,
    stg.valor_liberado,
    stg.valor_contrapartida

from {{ ref('stg_convenios') }} as stg
-- LEFT JOIN de propósito, e não INNER JOIN: se um dia a lógica de dedup das
-- dimensões mudar e algum município/órgão "sumir" da dimensão, queremos que
-- o teste not_null do schema.yml GRITE (falhe visivelmente) em vez de o
-- convênio simplesmente desaparecer em silêncio de um INNER JOIN.
left join {{ ref('dim_municipio') }} as dim_municipio
    on stg.municipio_codigo_ibge = dim_municipio.codigo_ibge
left join {{ ref('dim_orgao_superior') }} as dim_orgao_superior
    on stg.orgao_superior_codigo = dim_orgao_superior.codigo_orgao_superior
