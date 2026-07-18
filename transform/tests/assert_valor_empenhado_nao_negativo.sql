-- Mesmo padrão de assert_valor_convenio_nao_negativo.sql: teste singular que
-- deve sempre devolver zero linhas. Regra de negócio: valor empenhado (o
-- que o parlamentar destinou) não pode ser negativo.

select *
from {{ ref('fct_emendas') }}
where valor_empenhado < 0
