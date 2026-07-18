"""
Ingestão de Emendas Parlamentares do Portal da Transparência.

O QUE é uma "emenda parlamentar": é o mecanismo pelo qual um deputado, um
senador, uma bancada estadual ou uma comissão do Congresso destina uma fatia
do orçamento federal pra uma finalidade específica — geralmente um projeto no
município/estado que representam. É o dado que liga "político" a "dinheiro
público" de forma direta.

O CICLO ORÇAMENTÁRIO (importante pra entender os números, e pra não inventar
categoria que a fonte não tem): dinheiro público federal passa por 3 estágios
formais antes de sair do caixa do governo:

  1. EMPENHO   (valorEmpenhado)  -> o parlamentar reserva/destina o valor.
                                     É o mais próximo de "quanto foi proposto".
  2. LIQUIDAÇÃO (valorLiquidado) -> comprovação de que o bem/serviço foi
                                     entregue.
  3. PAGAMENTO (valorPago)       -> o dinheiro efetivamente sai do caixa.
                                     É o mais próximo de "quanto foi executado".

Também existe valorRestoCancelado: dinheiro que foi empenhado e depois
formalmente CANCELADO — ou seja, prometido e nunca virou nada. É o número
mais direto pra responder "quem propõe muito e não entrega".

POR QUE paginar por ANO (diferente de extract_convenios.py, que pagina por
intervalo de datas): este endpoint não tem parâmetro de intervalo de datas,
só um filtro opcional `ano`. Pra manter o escopo dos dados sob controle (sem
baixar o histórico completo desde sempre), iteramos sobre uma lista pequena
de anos recentes, paginando cada um até a resposta vir vazia.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import portal_transparencia as pt

API_BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados/emendas"

# Quantos anos recentes baixar (contando o ano corrente). Emendas são um
# instrumento orçamentário anual, então "ano" é a unidade natural de escopo
# aqui — 3 anos dá margem pra comparação entre exercícios sem explodir o
# número de requisições. Pode ser sobrescrito via env var (ex.: "2023,2024,2025").
DEFAULT_QUANTIDADE_ANOS = 3
ANOS_OVERRIDE = os.environ.get("EMENDAS_ANOS")

OUTPUT_PATH = Path(os.environ.get("EMENDAS_RAW_OUTPUT", "data/raw/emendas.jsonl"))

# Cinto de segurança contra loop infinito, mesmo raciocínio de MAX_PAGINAS em
# extract_convenios.py — por ano, dessa vez, já que a paginação reinicia a
# cada ano consultado.
MAX_PAGINAS_POR_ANO = 2000


def _anos_padrao() -> list[int]:
    ano_atual = date.today().year
    return [ano_atual - offset for offset in range(DEFAULT_QUANTIDADE_ANOS)]


def _anos_configurados() -> list[int]:
    if ANOS_OVERRIDE:
        return [int(ano.strip()) for ano in ANOS_OVERRIDE.split(",") if ano.strip()]
    return _anos_padrao()


def extrair_emendas(anos: list[int]) -> int:
    """Pagina por todas as emendas de cada ano em `anos` e grava cada
    registro (um por linha) em OUTPUT_PATH. Devolve a contagem total."""
    chave_api = pt.obter_chave_api()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = pt.criar_sessao(chave_api)

    total_registros = 0

    with OUTPUT_PATH.open("w", encoding="utf-8") as arquivo_saida:
        for ano in anos:
            pagina = 1
            total_do_ano = 0

            while pagina <= MAX_PAGINAS_POR_ANO:
                params = {"ano": ano, "pagina": pagina}
                registros = pt.requisitar_pagina(session, API_BASE_URL, params)

                if not registros:
                    break

                for registro in registros:
                    arquivo_saida.write(json.dumps(registro, ensure_ascii=False) + "\n")

                total_do_ano += len(registros)
                pagina += 1
                time.sleep(pt.SEGUNDOS_ENTRE_REQUISICOES)

            print(f"  ano {ano}: {total_do_ano} emendas")
            total_registros += total_do_ano

    return total_registros


def main() -> None:
    anos = _anos_configurados()
    print(f"Extraindo emendas parlamentares dos anos {anos}...")
    total = extrair_emendas(anos)
    print(f"Concluído: {total} emendas salvas em {OUTPUT_PATH}")

    if total == 0:
        print("AVISO: nenhuma emenda encontrada nos anos consultados.", file=sys.stderr)


if __name__ == "__main__":
    main()
