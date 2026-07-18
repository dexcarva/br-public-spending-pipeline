-- Camada de STAGING: a única responsabilidade dela é pegar o dado bruto
-- (JSON aninhado, tipos todos como texto/genérico) e deixar ele "arrumado":
-- nomes de coluna em snake_case, tipos corretos (INT, DATE, NUMERIC), sem
-- ainda aplicar nenhuma regra de negócio ou agregação. Isso separa "ajustar
-- formato" de "modelar pra análise", que são as próximas camadas
-- (dimensions/ e facts/) — assim, se o schema da API mudar, só esse arquivo
-- precisa mudar, o resto do projeto nem percebe.
--
-- read_json_auto é uma função do próprio DuckDB: ele lê o arquivo JSONL e já
-- infere o schema sozinho (quais campos existem, tipos prováveis) — não
-- precisamos declarar uma tabela e um CREATE TABLE antes de usar o dado.
--
-- IMPORTANTE sobre os nomes abaixo: a API devolve os campos em camelCase
-- (ex.: "numeroProcesso", "municipioConvenente.codigoIBGE"). O DuckDB casa
-- identificadores sem aspas ignorando maiúsculas/minúsculas, então
-- `numeroprocesso` bateria com `numeroProcesso` — mas ele NÃO insere ou
-- remove "_" pra você: `numero_processo` (com underscore) é um nome
-- diferente de `numeroProcesso` e simplesmente não seria encontrado. Por
-- isso referenciamos os campos de origem em camelCase (como vêm da API) e só
-- convertemos pra snake_case no `as` — é aqui, e só aqui, que a "tradução"
-- de convenção de nomes acontece no projeto inteiro.

with convenios_bruto as (

    select *
    from read_json_auto('{{ var("raw_convenios_path") }}')

),

renomeado as (

    select
        id                                    as id_convenio,
        numeroProcesso                        as numero_processo,
        situacao                              as situacao,

        -- Datas: o DuckDB já infere esses campos como DATE ao ler o JSON
        -- (formato "AAAA-MM-DD"), mas mantemos o CAST explícito mesmo assim:
        -- é a garantia documentada no código de que o tipo é DATE, e não uma
        -- suposição do inferidor automático (item do checklist original:
        -- "converter para os tipos corretos: INT, DATE, NUMERIC").
        cast(dataPublicacao as date)          as data_publicacao,
        cast(dataInicioVigencia as date)      as data_inicio_vigencia,
        cast(dataFinalVigencia as date)       as data_final_vigencia,

        cast(valor as decimal(18, 2))             as valor,
        cast(valorLiberado as decimal(18, 2))     as valor_liberado,
        cast(valorContrapartida as decimal(18, 2)) as valor_contrapartida,

        -- "Flattening": o JSON de cada convênio vem com objetos aninhados
        -- (município, órgão...). Aqui a gente extrai só os campos que
        -- interessam pras dimensões, achatando a estrutura em colunas
        -- simples via notação de ponto (`campo.subcampo`) — é o que o
        -- checklist original chamava de "extrair as chaves do JSONB".
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

    from convenios_bruto

)

select * from renomeado
-- Convênios sem município ou sem órgão superior vinculado não servem pras
-- dimensões que vamos construir (não têm o que juntar) — filtramos aqui, na
-- staging, em vez de deixar esse "dado incompleto" vazar pras camadas
-- seguintes.
where municipio_codigo_ibge is not null
  and orgao_superior_codigo is not null
