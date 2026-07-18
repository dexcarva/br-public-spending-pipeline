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
#}
{% macro normalizar_valor_brl(coluna) %}
    case
        when nullif(cast({{ coluna }} as varchar), '') is null then null
        when contains(cast({{ coluna }} as varchar), ',') then
            cast(
                replace(replace(cast({{ coluna }} as varchar), '.', ''), ',', '.')
                as decimal(18, 2)
            )
        else
            cast({{ coluna }} as decimal(18, 2))
    end
{% endmacro %}
