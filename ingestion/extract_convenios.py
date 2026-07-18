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
O endpoint /convenios, ao contrário, aceita só um intervalo de datas e devolve
convênios de TODOS os municípios e órgãos nesse intervalo, com paginação — é
o que permite manter esse pipeline rápido e dentro do limite de requisições
da API rodando de graça no GitHub Actions.

COMO funciona a paginação aqui: a API não informa quantas páginas existem no
total. A prática (e a que a própria documentação recomenda) é pedir a página
1, 2, 3... e parar quando uma página vier vazia (`[]`). Chamamos isso de
"paginação por sentinela vazia".

COMO lidamos com instabilidade da API (HTTP 429 / 5xx): serviços públicos
como esse têm limite de requisições por minuto (o token é suspenso por 8h se
você passar do limite) e volta e meia caem. Em vez de deixar o pipeline
inteiro quebrar, cada requisição tem retry com "backoff exponencial": se
falhar, esperamos 2s, tenta de novo; se falhar de novo, esperamos 4s, depois
8s, etc. Isso dá tempo do problema (rate limit temporário, instabilidade do
servidor) se resolver sozinho, sem martelar a API com mais requisições logo
em seguida (o que só pioraria as coisas).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

# --- Configuração via variáveis de ambiente -------------------------------
# Usamos env vars (não argumentos de linha de comando fixos) porque este
# script roda tanto localmente (você exporta a variável antes) quanto dentro
# do GitHub Actions (o valor vem de um "secret" do repositório). Nenhum dos
# dois ambientes precisa mudar o código, só a variável de ambiente.

API_BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/convenios"
API_KEY = os.environ.get("PORTAL_TRANSPARENCIA_API_KEY")

# Janela de datas: por padrão, os últimos 24 meses até hoje. Escolhemos uma
# janela recente (em vez de baixar o histórico completo desde sempre) de
# propósito: mantém o job rápido, dentro do limite de requisições da API, e
# ainda assim dá dado suficiente pra rankings e série temporal mensal fazerem
# sentido. Dá pra aumentar essa janela depois trocando as env vars abaixo.
DEFAULT_MESES_JANELA = 24
DATA_INICIAL = os.environ.get("CONVENIOS_DATA_INICIAL")
DATA_FINAL = os.environ.get("CONVENIOS_DATA_FINAL")

# Onde salvar o resultado bruto. Formato JSONL (um objeto JSON por linha, em
# vez de um array JSON gigante) de propósito: dá pra ir gravando página por
# página, sem manter tudo em memória, e o DuckDB lê esse formato nativamente.
OUTPUT_PATH = Path(
    os.environ.get("CONVENIOS_RAW_OUTPUT", "data/raw/convenios.jsonl")
)

# Intervalo mínimo entre requisições. A API aceita até 90 requisições/min em
# horário comercial (6h-23:59) — usamos uma margem de segurança (75/min) em
# vez de tentar encostar no limite exato, porque a suspensão por excesso dura
# 8 horas: o custo de ser um pouco mais lento é muito menor que o custo de
# tomar um bloqueio no meio da execução.
SEGUNDOS_ENTRE_REQUISICOES = 60 / 75

# Número máximo de páginas como "cinto de segurança": se algo no critério de
# parada (página vazia) falhar por qualquer motivo, isso evita um loop infinito
# consumindo a cota da API.
MAX_PAGINAS = 3000

# Tentativas de retry por requisição e backoff inicial (em segundos). A cada
# falha, o tempo de espera dobra (2s, 4s, 8s, 16s...) — esse é o "exponencial"
# do backoff exponencial.
MAX_TENTATIVAS = 5
BACKOFF_INICIAL_SEGUNDOS = 2


def _janela_padrao() -> tuple[str, str]:
    """Calcula a janela de datas padrão (últimos N meses) no formato DD/MM/AAAA
    exigido pela API, caso o usuário não tenha fixado CONVENIOS_DATA_INICIAL/FINAL."""
    hoje = date.today()
    inicio = hoje - timedelta(days=DEFAULT_MESES_JANELA * 30)
    return inicio.strftime("%d/%m/%Y"), hoje.strftime("%d/%m/%Y")


def _requisitar_pagina(session: requests.Session, params: dict[str, Any]) -> list[dict]:
    """Busca uma página da API, com retry/backoff exponencial.

    Devolve a lista de convênios da página (pode ser vazia = fim da paginação).
    Lança a exceção original se todas as tentativas se esgotarem — nesse ponto
    não é um problema "esperado" (rate limit, instabilidade passageira), é algo
    que precisa aparecer no log do GitHub Actions pra alguém olhar.
    """
    espera = BACKOFF_INICIAL_SEGUNDOS
    ultimo_erro: Exception | None = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resposta = session.get(API_BASE_URL, params=params, timeout=30)
        except requests.RequestException as erro:
            # Problema de rede (timeout, DNS, conexão recusada) — também vale
            # a pena tentar de novo, o portal do governo cai com frequência.
            ultimo_erro = erro
        else:
            if resposta.status_code == 200:
                return resposta.json()

            if resposta.status_code == 429:
                # Estourou o limite de requisições por minuto. Respeitamos o
                # header Retry-After se ele vier; senão, usamos nosso backoff.
                espera = int(resposta.headers.get("Retry-After", espera))
                print(
                    f"  [429] rate limit atingido na página {params.get('pagina')}, "
                    f"aguardando {espera}s antes de tentar de novo...",
                    file=sys.stderr,
                )
            elif 500 <= resposta.status_code < 600:
                # Erro do lado do servidor do governo (instabilidade conhecida
                # do portal) — vale tentar de novo, provavelmente é passageiro.
                print(
                    f"  [{resposta.status_code}] erro do servidor na página "
                    f"{params.get('pagina')}, tentativa {tentativa}/{MAX_TENTATIVAS}...",
                    file=sys.stderr,
                )
            else:
                # Erro "definitivo" (ex.: 401 chave inválida, 400 parâmetro
                # errado) — tentar de novo não vai adiantar, falha na hora.
                resposta.raise_for_status()

            ultimo_erro = requests.HTTPError(
                f"HTTP {resposta.status_code} na página {params.get('pagina')}"
            )

        time.sleep(espera)
        espera *= 2  # backoff exponencial: dobra o tempo de espera a cada falha

    raise RuntimeError(
        f"Falhou após {MAX_TENTATIVAS} tentativas na página {params.get('pagina')}"
    ) from ultimo_erro


def extrair_convenios(data_inicial: str, data_final: str) -> int:
    """Pagina por todos os convênios no intervalo [data_inicial, data_final]
    e grava cada registro (um por linha) em OUTPUT_PATH. Devolve a contagem
    total de registros gravados."""
    if not API_KEY:
        raise SystemExit(
            "PORTAL_TRANSPARENCIA_API_KEY não definida. Cadastre-se em "
            "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email "
            "e exporte a chave recebida por e-mail nessa variável de ambiente."
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    # O header de autenticação da API tem esse nome específico (não é o
    # convencional "Authorization: Bearer ..."), definido no OpenAPI spec dela.
    session.headers.update({"chave-api-dados": API_KEY, "Accept": "application/json"})

    total_registros = 0
    pagina = 1

    with OUTPUT_PATH.open("w", encoding="utf-8") as arquivo_saida:
        while pagina <= MAX_PAGINAS:
            params = {
                "dataInicial": data_inicial,
                "dataFinal": data_final,
                "pagina": pagina,
            }
            registros = _requisitar_pagina(session, params)

            if not registros:
                # Página vazia = sentinela de "acabaram os dados". É assim
                # que essa API sinaliza o fim da paginação (não existe um
                # campo "totalPaginas" na resposta).
                break

            for registro in registros:
                arquivo_saida.write(json.dumps(registro, ensure_ascii=False) + "\n")

            total_registros += len(registros)
            print(f"  página {pagina}: +{len(registros)} convênios (total: {total_registros})")

            pagina += 1
            time.sleep(SEGUNDOS_ENTRE_REQUISICOES)

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
