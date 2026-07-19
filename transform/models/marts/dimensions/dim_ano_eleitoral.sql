-- DIMENSÃO DE CALENDÁRIO ELEITORAL: um ano por linha, com os atributos que
-- permitem responder "o gasto de emenda se concentra em ano de eleição?" —
-- a hipótese da "safadeza programada": empenhar às vésperas da urna.
--
-- O PULO DO GATO: esta dimensão NÃO precisa de nenhuma API nova. O
-- calendário eleitoral brasileiro é DETERMINÍSTICO por lei — eleições em
-- todo ano par, alternando geral (presidente/governadores/Congresso, anos
-- com resto 2 na divisão por 4: 2022, 2026, 2030...) e municipal
-- (prefeitos/vereadores, anos múltiplos de 4: 2020, 2024, 2028...). Tudo
-- aqui é função aritmética do próprio ano — SQL puro, zero ingestão.
--
-- CHAVE NATURAL SEM SURROGATE, de propósito (e diferente das outras dims):
-- o ano JÁ É a chave perfeita — estável, única, significativa, e é
-- literalmente a coluna `ano` que fct_emendas carrega. Surrogate key existe
-- pra blindar a fato contra identidades de sistemas de origem; um número de
-- calendário não é identidade de sistema nenhum, então um hash só
-- adicionaria uma indireção sem proteger nada.
--
-- CUIDADO METODOLÓGICO (documentado aqui porque o painel deve MOSTRAR o
-- padrão, não carimbar veredito): emendas de deputado federal podem inflar
-- tanto na eleição GERAL (a própria reeleição) quanto na MUNICIPAL (aliados
-- locais que pavimentam a base do deputado) — motivos diferentes, mesmo
-- sintoma. Separar o TIPO de eleição é o que permite ao leitor enxergar os
-- dois mecanismos em vez de um "ano eleitoral" genérico.

with anos as (

    -- Só os anos que existem na fato — dimensão de calendário não precisa
    -- inventar linhas pra anos sem dado (e nasce/cresce sozinha conforme a
    -- ingestão cobrir mais exercícios).
    select distinct ano
    from {{ ref('stg_emendas') }}

)

select
    ano,

    -- Ano par = ano de eleição no Brasil (todas as eleições regulares caem
    -- em ano par desde a unificação do calendário). O resto da divisão por
    -- 2/4 é aritmética portável entre DuckDB e Postgres (operador %).
    (ano % 2 = 0)                                        as eh_ano_eleitoral,

    case
        when ano % 4 = 2 then 'Geral'      -- 2022, 2026: presidente, governadores, Congresso
        when ano % 4 = 0 then 'Municipal'  -- 2020, 2024: prefeitos e vereadores
        else null                          -- ano ímpar: sem eleição regular
    end                                                  as tipo_eleicao,

    -- Posição no MANDATO FEDERAL de 4 anos (deputados/senadores tomam posse
    -- no ano seguinte à eleição geral: eleitos em 2022 -> mandato 2023-2026).
    -- ((ano - 3) % 4) + 1 mapeia: 2023->1, 2024->2, 2025->3, 2026->4 (ano de
    -- tentar a reeleição — se a hipótese da "safadeza programada" vale, é
    -- aqui que o empenho deve inchar).
    ((ano - 3) % 4) + 1                                  as posicao_mandato_federal

from anos
