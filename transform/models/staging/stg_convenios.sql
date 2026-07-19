-- Camada de STAGING: a única responsabilidade dela é pegar o dado bruto
-- (JSON aninhado, tipos todos como texto/genérico) e deixar ele "arrumado":
-- nomes de coluna em snake_case, tipos corretos (INT, DATE, NUMERIC), sem
-- ainda aplicar nenhuma regra de negócio ou agregação. Isso separa "ajustar
-- formato" de "modelar pra análise", que são as próximas camadas
-- (dimensions/ e facts/) — assim, se o schema da API mudar, só esse arquivo
-- precisa mudar, o resto do projeto nem percebe.
--
-- ESTE MODEL RODA EM DOIS BANCOS DIFERENTES — e é o ÚNICO lugar (junto com
-- stg_emendas) que sabe disso. O projeto tem dois targets (profiles.yml):
--
--   dev    (DuckDB)  -> o bruto é um ARQUIVO JSONL, lido com read_json_auto.
--   docker (Postgres) -> o bruto é a TABELA raw.convenios (coluna JSONB),
--                        carregada por scripts/carregar_raw_postgres.py.
--
-- O bloco condicional "if target.type == 'postgres'" abaixo é Jinja: o dbt
-- escolhe o ramo NA COMPILAÇÃO, conforme o target — o SQL que chega ao banco
-- só tem o ramo certo. (Curiosidade que já mordeu este arquivo: comentário
-- SQL NÃO esconde nada do Jinja — o arquivo inteiro é template, então
-- escrever os delimitadores de bloco dentro de um comentário abre um bloco
-- de verdade e quebra a compilação. Por isso esta explicação os evita.)
-- O contrato é que os DOIS ramos produzam exatamente as mesmas
-- colunas, com os mesmos nomes e tipos: tudo daqui pra frente (dimensões,
-- fatos, testes, filtros no fim deste arquivo) é idêntico nos dois mundos.
--
-- IMPORTANTE sobre os nomes: a API devolve os campos em camelCase (ex.:
-- "numeroProcesso", "municipioConvenente.codigoIBGE"). É aqui, e só aqui,
-- que a "tradução" pra snake_case acontece no projeto inteiro.

with renomeado as (

{% if target.type == 'postgres' %}

    -- Ramo POSTGRES: extração campo a campo do JSONB com os operadores
    -- nativos: -> desce um nível DEVOLVENDO JSON (pra continuar navegando no
    -- aninhamento), ->> desce DEVOLVENDO TEXTO (pra folha que vamos usar).
    -- Todo ->> devolve texto, sempre — por isso os CASTs aqui são
    -- obrigatórios, não decorativos: sem eles, valor viraria coluna de texto
    -- e um sum() lá na frente falharia.
    select
        cast(dado->>'id' as bigint)                        as id_convenio,
        dado->>'numeroProcesso'                            as numero_processo,
        dado->>'situacao'                                  as situacao,

        cast(dado->>'dataPublicacao' as date)              as data_publicacao,
        cast(dado->>'dataInicioVigencia' as date)          as data_inicio_vigencia,
        cast(dado->>'dataFinalVigencia' as date)           as data_final_vigencia,

        cast(dado->>'valor' as decimal(18, 2))             as valor,
        cast(dado->>'valorLiberado' as decimal(18, 2))     as valor_liberado,
        cast(dado->>'valorContrapartida' as decimal(18, 2)) as valor_contrapartida,

        -- Quando esta versão do registro entrou no warehouse — é o critério
        -- do desempate no dedupe lá embaixo (carga incremental: o mesmo
        -- convênio reaparece a cada movimentação, e a versão mais recente
        -- ganha).
        carregado_em,

        -- Campos aninhados: -> até o penúltimo nível, ->> na folha.
        -- codigoIBGE fica como TEXTO de propósito: a API o manda como string
        -- JSON ("5001102"), então o read_json_auto do DuckDB também o infere
        -- como VARCHAR — manter texto aqui preserva o contrato "mesmas
        -- colunas, mesmos tipos nos dois targets" (e código IBGE é
        -- identificador, não número: ninguém soma código de município).
        dado->'municipioConvenente'->>'codigoIBGE'         as municipio_codigo_ibge,
        dado->'municipioConvenente'->>'nomeIBGE'           as municipio_nome,
        dado->'municipioConvenente'->>'codigoRegiao'       as municipio_codigo_regiao,
        dado->'municipioConvenente'->>'nomeRegiao'         as municipio_nome_regiao,
        dado->'municipioConvenente'->'uf'->>'sigla'        as municipio_uf_sigla,
        dado->'municipioConvenente'->'uf'->>'nome'         as municipio_uf_nome,

        dado->'orgao'->>'codigoSIAFI'                      as orgao_codigo_siafi,
        dado->'orgao'->>'nome'                             as orgao_nome,
        dado->'orgao'->>'sigla'                            as orgao_sigla,
        dado->'orgao'->'orgaoMaximo'->>'codigo'            as orgao_superior_codigo,
        dado->'orgao'->'orgaoMaximo'->>'nome'              as orgao_superior_nome,
        dado->'orgao'->'orgaoMaximo'->>'sigla'             as orgao_superior_sigla

    -- source() em vez de "raw.convenios" escrito na mão: declara a
    -- dependência do dado bruto no grafo do dbt (ver sources.yml).
    from {{ source('raw', 'convenios') }}

{% else %}

    -- Ramo DUCKDB: read_json_auto é uma função do próprio DuckDB — lê o
    -- arquivo JSONL e já infere o schema sozinho (quais campos existem,
    -- tipos prováveis), sem precisar de CREATE TABLE nem de passo de carga.
    --
    -- Sobre os nomes de origem: o DuckDB casa identificadores sem aspas
    -- ignorando maiúsculas/minúsculas (`numeroprocesso` bate com
    -- `numeroProcesso`), mas NÃO insere/remove "_" — `numero_processo` é
    -- outro nome e não seria encontrado. Por isso referenciamos os campos
    -- em camelCase, como vêm da API.
    select
        id                                    as id_convenio,
        numeroProcesso                        as numero_processo,
        situacao                              as situacao,

        -- Datas: o DuckDB já infere esses campos como DATE ao ler o JSON
        -- (formato "AAAA-MM-DD"), mas mantemos o CAST explícito mesmo assim:
        -- é a garantia documentada no código de que o tipo é DATE, e não uma
        -- suposição do inferidor automático.
        cast(dataPublicacao as date)          as data_publicacao,
        cast(dataInicioVigencia as date)      as data_inicio_vigencia,
        cast(dataFinalVigencia as date)       as data_final_vigencia,

        cast(valor as decimal(18, 2))             as valor,
        cast(valorLiberado as decimal(18, 2))     as valor_liberado,
        cast(valorContrapartida as decimal(18, 2)) as valor_contrapartida,

        -- No mundo DuckDB o bruto é UM arquivo-snapshot (não há cargas
        -- acumuladas) — carregado_em vira uma constante só pra honrar o
        -- contrato de colunas com o ramo Postgres; o dedupe compartilhado
        -- funciona igual (qualquer versão é "a mais recente").
        current_timestamp                          as carregado_em,

        -- "Flattening": o JSON de cada convênio vem com objetos aninhados
        -- (município, órgão...). No DuckDB isso vira struct, e a notação de
        -- ponto (`campo.subcampo`) achata em colunas simples — o equivalente
        -- exato do ->/->>' do ramo Postgres acima.
        municipioConvenente.codigoIBGE        as municipio_codigo_ibge,
        municipioConvenente.nomeIBGE          as municipio_nome,
        municipioConvenente.codigoRegiao      as municipio_codigo_regiao,
        municipioConvenente.nomeRegiao        as municipio_nome_regiao,
        municipioConvenente.uf.sigla          as municipio_uf_sigla,
        municipioConvenente.uf.nome           as municipio_uf_nome,

        orgao.codigoSIAFI                     as orgao_codigo_siafi,
        orgao.nome                            as orgao_nome,
        orgao.sigla                           as orgao_sigla,
        orgao.orgaoMaximo.codigo              as orgao_superior_codigo,
        orgao.orgaoMaximo.nome                as orgao_superior_nome,
        orgao.orgaoMaximo.sigla               as orgao_superior_sigla

    from read_json_auto('{{ var("raw_convenios_path") }}')

{% endif %}

),

-- Daqui pra baixo é COMPARTILHADO entre os dois targets — só SQL padrão, sem
-- dialeto (é o mesmo requisito do macro normalizar_valor_brl: código que
-- roda em dois bancos não pode ter "sotaque" de nenhum deles).
--
-- DEDUPLICAÇÃO POR CONVÊNIO (a contrapartida da carga incremental): o filtro
-- da API é por "última atualização do registro", então o MESMO convênio (id)
-- reaparece no raw a cada movimentação — uma liberação de parcela ontem traz
-- de volta um convênio de 2024, agora com valores atualizados. Isso é ótimo
-- (é um CDC de pobre: recebemos as versões novas de graça), DESDE que
-- alguém escolha uma versão só — senão cada movimentação viraria uma linha
-- duplicada na fato, somando o mesmo convênio N vezes. row_number() por id,
-- mais recente primeiro, e ficamos com a versão 1: o estado atual de cada
-- convênio, que é o que um snapshot analítico deve mostrar.
deduplicado as (

    select
        *,
        row_number() over (
            partition by id_convenio
            order by carregado_em desc
        ) as versao_do_registro

    from renomeado

)

select * from deduplicado
where versao_do_registro = 1
  -- Convênios sem município ou sem órgão superior vinculado não servem pras
  -- dimensões que vamos construir (não têm o que juntar) — filtramos aqui,
  -- na staging, em vez de deixar esse "dado incompleto" vazar pras camadas
  -- seguintes.
  and municipio_codigo_ibge is not null
  and orgao_superior_codigo is not null
  -- DESCOBERTA testando contra a API real: o filtro dataInicial/dataFinal do
  -- endpoint /convenios parece valer sobre a data de ÚLTIMA ATUALIZAÇÃO do
  -- registro, não sobre a data de publicação — uma janela de ingestão de
  -- poucos dias trouxe convênios com data_publicacao de 1996 (que só tiveram
  -- alguma movimentação recente, tipo uma liberação de parcela). Isso faz
  -- sentido pro caso de uso "acompanhar liberações recentes de convênios
  -- antigos", mas quebraria o gráfico de evolução mensal (décadas de meses
  -- quase vazios) e distorceria os rankings com ruído histórico. Por isso
  -- restringimos aqui à publicação nos últimos ~3 anos — gera folga
  -- confortável acima de qualquer janela de ingestão configurada em
  -- extract_convenios.py, mas descarta o ruído claramente antigo.
  and data_publicacao >= current_date - interval '3 years'
