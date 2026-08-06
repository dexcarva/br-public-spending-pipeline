{#
    A API do Portal da Transparência devolve alguns valores monetários (nas
    emendas parlamentares) como STRING no formato numérico brasileiro —
    "1.234.567,89" (ponto como separador de milhar, vírgula como decimal) —
    em vez de um número JSON puro. Um CAST direto pra DECIMAL nesse texto
    falha (ou, pior, interpreta errado), então isolamos essa conversão aqui:
    um macro só, reaproveitado em todo campo de valor de emendas, em vez de
    repetir essa lógica de "detectar formato e converter" 6 vezes no mesmo
    model.

    A lógica: se o texto tem vírgula, é formato BR — remove os pontos de
    milhar e troca a vírgula decimal por ponto antes de converter. Se não tem
    vírgula (nem tem valor nenhum), trata como já numérico ou nulo.

    ESPAÇO ANTES DO SINAL: alguns valores negativos vêm como "- 721,68" (com
    espaço entre o "-" e o número) em vez de "-721,68" — encontrado em
    valorRestoInscrito, ao investigar por que o dbt run quebrava todo dia no
    Neon/Postgres com "invalid input syntax for type numeric" enquanto
    passava limpo no DuckDB local (CAST do DuckDB tolera o espaço, o do
    Postgres não). Removemos todo espaço antes do CAST — nenhum desses
    campos tem espaço legítimo, então é seguro tirar sempre.

    PORTABILIDADE ENTRE BANCOS: este macro roda nos DOIS targets do projeto
    (DuckDB no "dev", Postgres no "docker") — por isso só usamos funções que
    existem identicamente nos dois dialetos. O teste "tem vírgula?" era feito
    com contains(), que é específica do DuckDB (o Postgres não a tem) —
    trocamos por position(',' in ...) > 0, que é SQL padrão (ANSI) e se
    comporta igual nos dois. Mesma razão de cast/replace/nullif aqui serem
    todos SQL "sem sotaque": macro compartilhado não pode ter dialeto.
#}
{% macro normalizar_valor_brl(coluna) %}
    case
        when nullif(cast({{ coluna }} as varchar), '') is null then null
        when position(',' in cast({{ coluna }} as varchar)) > 0 then
            cast(
                replace(replace(replace(cast({{ coluna }} as varchar), '.', ''), ',', '.'), ' ', '')
                as decimal(18, 2)
            )
        else
            cast(replace(cast({{ coluna }} as varchar), ' ', '') as decimal(18, 2))
    end
{% endmacro %}
