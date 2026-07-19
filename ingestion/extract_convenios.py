"""
Ingestão dos dados de Convênios do Portal da Transparência.

O QUE é um "convênio", pra quem não é da área: é o instrumento jurídico que o
Governo Federal usa pra repassar dinheiro a estados, municípios, ONGs etc. pra
executar um projeto específico (uma obra, um programa de saúde, etc.). É um
dos jeitos mais diretos de ver "quanto o governo mandou de dinheiro pra onde,
e quem autorizou" — por isso escolhemos esse endpoint como fonte principal.

POR QUE esse endpoint e não outro: a API do Portal da Transparência tem
dezenas de endpoints, mas boa parte deles (ex.: Bolsa Família por município)
exige informar o código IBGE de UM município por chamada — ou seja, pra
cobrir o Brasil inteiro seriam ~5.570 chamadas só pra um mês de um programa.
O endpoint /convenios, ao contrário, aceita um intervalo de datas e devolve
convênios de TODOS os municípios e órgãos nesse intervalo, com paginação.

LIMITE DE 1 MÊS POR CONSULTA (descoberto testando contra a API real, não
documentado no OpenAPI spec): pedir um intervalo maior que ~1 mês devolve
HTTP 400 com a mensagem "Para usar filtros em convênios, escolha um período
de até 1 mês [...]". Por isso _dividir_em_janelas_mensais() fatia qualquer
intervalo maior em pedaços de até 28 dias (28, não 30/31, de propósito: dá
uma margem de segurança contra o limite exato da API variar por causa de
meses de tamanhos diferentes) e cada pedaço é consultado (e paginado)
separadamente, tudo acumulado no mesmo arquivo de saída.

COMO funciona a paginação dentro de cada janela: a API não informa quantas
páginas existem no total. A prática (e a que a própria documentação
recomenda) é pedir a página 1, 2, 3... e parar quando uma página vier vazia
(`[]`). Chamamos isso de "paginação por sentinela vazia".

A lógica de autenticação e retry/backoff é compartilhada com
extract_emendas.py — está em portal_transparencia.py.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import portal_transparencia as pt

API_BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/convenios"

# --- Configuração via variáveis de ambiente -------------------------------
# Usamos env vars (não argumentos de linha de comando fixos) porque este
# script roda tanto localmente (você exporta a variável antes) quanto dentro
# do GitHub Actions (o valor vem de um "secret" do repositório). Nenhum dos
# dois ambientes precisa mudar o código, só a variável de ambiente.

# Janela de datas: por padrão, os últimos 3 meses até hoje. Um teste real
# contra a API mostrou um volume de dados bem maior do que o esperado (só 10
# dias de janela já trouxe centenas de convênios — a API parece filtrar por
# "última atualização do registro", não só por data de assinatura, então
# convênios antigos com movimentação recente também aparecem). 3 meses já dá
# dado de sobra pra rankings e série temporal, num tempo de execução
# previsível dentro do limite do job do GitHub Actions. Dá pra aumentar
# depois trocando as env vars abaixo — só lembre que uma janela maior
# significa mais pedaços mensais e, portanto, mais tempo de execução.
DEFAULT_MESES_JANELA = 3
DATA_INICIAL = os.environ.get("CONVENIOS_DATA_INICIAL")
DATA_FINAL = os.environ.get("CONVENIOS_DATA_FINAL")

# Onde salvar o resultado bruto. Formato JSONL (um objeto JSON por linha, em
# vez de um array JSON gigante) de propósito: dá pra ir gravando página por
# página, sem manter tudo em memória, e o DuckDB lê esse formato nativamente.
OUTPUT_PATH = Path(
    os.environ.get("CONVENIOS_RAW_OUTPUT", "data/raw/convenios.jsonl")
)

# Número máximo de páginas POR JANELA MENSAL como "cinto de segurança": se
# algo no critério de parada (página vazia) falhar por qualquer motivo, isso
# evita um loop infinito consumindo a cota da API.
MAX_PAGINAS_POR_JANELA = 3000


def _formatar(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _janela_padrao() -> tuple[str, str]:
    """Calcula a janela de datas padrão (últimos N meses) no formato DD/MM/AAAA
    exigido pela API, caso o usuário não tenha fixado CONVENIOS_DATA_INICIAL/FINAL."""
    hoje = date.today()
    inicio = hoje - timedelta(days=DEFAULT_MESES_JANELA * 30)
    return _formatar(inicio), _formatar(hoje)


def _dividir_em_janelas_mensais(data_inicial: str, data_final: str) -> list[tuple[str, str]]:
    """Fatia [data_inicial, data_final] em pedaços de até 28 dias — a API só
    aceita filtro de data em /convenios com intervalo de até ~1 mês por
    consulta (ver o cabeçalho deste arquivo)."""
    inicio = datetime.strptime(data_inicial, "%d/%m/%Y").date()
    fim = datetime.strptime(data_final, "%d/%m/%Y").date()

    janelas: list[tuple[str, str]] = []
    cursor = inicio
    while cursor <= fim:
        fim_da_janela = min(cursor + timedelta(days=27), fim)
        janelas.append((_formatar(cursor), _formatar(fim_da_janela)))
        cursor = fim_da_janela + timedelta(days=1)
    return janelas


def extrair_convenios(data_inicial: str, data_final: str) -> int:
    """Pagina por todos os convênios no intervalo [data_inicial, data_final],
    fatiado em janelas mensais, e grava cada registro (um por linha) em
    OUTPUT_PATH. Devolve a contagem total de registros gravados."""
    chave_api = pt.obter_chave_api()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = pt.criar_sessao(chave_api)

    total_registros = 0
    janelas = _dividir_em_janelas_mensais(data_inicial, data_final)

    with OUTPUT_PATH.open("w", encoding="utf-8") as arquivo_saida:
        for inicio_janela, fim_janela in janelas:
            print(f"janela {inicio_janela} a {fim_janela}:")
            pagina = 1

            while pagina <= MAX_PAGINAS_POR_JANELA:
                params = {
                    "dataInicial": inicio_janela,
                    "dataFinal": fim_janela,
                    "pagina": pagina,
                }
                registros = pt.requisitar_pagina(session, API_BASE_URL, params)

                if not registros:
                    # Página vazia = sentinela de "acabaram os dados dessa
                    # janela". É assim que essa API sinaliza o fim da
                    # paginação (não existe um campo "totalPaginas" na resposta).
                    break

                for registro in registros:
                    arquivo_saida.write(json.dumps(registro, ensure_ascii=False) + "\n")

                total_registros += len(registros)
                print(f"  página {pagina}: +{len(registros)} convênios (total: {total_registros})")

                pagina += 1
                time.sleep(pt.SEGUNDOS_ENTRE_REQUISICOES)

    return total_registros


def main() -> None:
    data_inicial = DATA_INICIAL
    data_final = DATA_FINAL
    if not data_inicial or not data_final:
        data_inicial, data_final = _janela_padrao()

    print(f"Extraindo convênios de {data_inicial} até {data_final}...")
    total = extrair_convenios(data_inicial, data_final)
    print(f"Concluído: {total} convênios salvos em {OUTPUT_PATH}")

    if total == 0:
        # Não tratamos isso como erro fatal (o pipeline pode legitimamente
        # rodar numa janela sem convênios novos), mas é importante que apareça
        # bem visível no log do Actions, e não escondido entre outras linhas.
        print("AVISO: nenhum convênio encontrado nessa janela de datas.", file=sys.stderr)


if __name__ == "__main__":
    main()
