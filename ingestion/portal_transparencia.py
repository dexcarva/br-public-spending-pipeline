"""
Cliente HTTP compartilhado pelos scripts de ingestão do Portal da
Transparência (extract_convenios.py e extract_emendas.py).

Por que isso é um módulo à parte: os dois scripts falam com a mesma API, com
a mesma autenticação e os mesmos limites de requisição — só o endpoint e os
parâmetros de cada consulta mudam. Sem esse módulo, a lógica de retry com
backoff exponencial (a parte mais delicada de acertar) ficaria duplicada nos
dois arquivos, e um bug corrigido num lugar facilmente seria esquecido no
outro.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import requests

# Intervalo mínimo entre requisições. A API aceita até 90 requisições/min em
# horário comercial (6h-23:59) — usamos uma margem de segurança (75/min) em
# vez de tentar encostar no limite exato, porque a suspensão por excesso dura
# 8 horas: o custo de ser um pouco mais lento é muito menor que o custo de
# tomar um bloqueio no meio da execução.
SEGUNDOS_ENTRE_REQUISICOES = 60 / 75

# Tentativas de retry por requisição e backoff inicial (em segundos). A cada
# falha, o tempo de espera dobra (2s, 4s, 8s, 16s...) — esse é o "exponencial"
# do backoff exponencial.
MAX_TENTATIVAS = 5
BACKOFF_INICIAL_SEGUNDOS = 2


def obter_chave_api(variavel_ambiente: str = "PORTAL_TRANSPARENCIA_API_KEY") -> str:
    """Lê a chave da API do ambiente, ou encerra o script com uma mensagem que
    já diz como resolver — em vez de deixar estourar um erro confuso lá na
    frente, na primeira chamada HTTP sem autenticação."""
    chave = os.environ.get(variavel_ambiente)
    if not chave:
        raise SystemExit(
            f"{variavel_ambiente} não definida. Cadastre-se em "
            "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email "
            "e exporte a chave recebida por e-mail nessa variável de ambiente."
        )
    return chave


def criar_sessao(chave_api: str) -> requests.Session:
    session = requests.Session()
    # O header de autenticação da API tem esse nome específico (não é o
    # convencional "Authorization: Bearer ..."), definido no OpenAPI spec dela.
    session.headers.update({"chave-api-dados": chave_api, "Accept": "application/json"})
    return session


def requisitar_pagina(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    *,
    max_tentativas: int = MAX_TENTATIVAS,
    backoff_inicial_segundos: float = BACKOFF_INICIAL_SEGUNDOS,
) -> list[dict]:
    """Busca uma página da API, com retry/backoff exponencial.

    Devolve a lista de registros da página (pode ser vazia = fim da
    paginação). Lança a exceção original se todas as tentativas se
    esgotarem — nesse ponto não é mais um problema "esperado" (rate limit,
    instabilidade passageira), é algo que precisa aparecer no log do GitHub
    Actions pra alguém olhar.

    COMO lidamos com instabilidade da API (HTTP 429 / 5xx): serviços
    públicos como esse têm limite de requisições por minuto (o token é
    suspenso por 8h se você passar do limite) e volta e meia caem. Em vez de
    deixar o pipeline inteiro quebrar, cada requisição tenta de novo com
    espera crescente: se falhar, esperamos 2s, tenta de novo; se falhar de
    novo, esperamos 4s, depois 8s, etc. Isso dá tempo do problema se resolver
    sozinho, sem martelar a API com mais requisições logo em seguida (o que
    só pioraria as coisas).
    """
    espera = backoff_inicial_segundos
    ultimo_erro: Exception | None = None

    for tentativa in range(1, max_tentativas + 1):
        try:
            resposta = session.get(url, params=params, timeout=30)
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
                    f"{params.get('pagina')}, tentativa {tentativa}/{max_tentativas}...",
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
        f"Falhou após {max_tentativas} tentativas na página {params.get('pagina')}"
    ) from ultimo_erro
