-- Teste "singular" do dbt: diferente dos testes genéricos do schema.yml
-- (not_null, unique), aqui a gente escreve uma query normal que deveria
-- SEMPRE devolver zero linhas. Se devolver alguma linha, é porque a regra de
-- negócio foi violada e o dbt marca o teste como FALHOU — nesse caso, a
-- regra é "gasto público não pode ser negativo" (item explícito do checklist
-- original: "garantir que a métrica de gasto nunca seja negativa").

select *
from {{ ref('fct_convenios') }}
where valor < 0
