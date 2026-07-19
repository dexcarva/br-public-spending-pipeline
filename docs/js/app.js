/**
 * Front-end do painel. Nenhuma conta pesada acontece aqui — o pipeline
 * (ingestion/ + transform/) já entregou tudo pré-agregado em docs/data/*.json.
 * Este arquivo só faz três coisas: busca esses JSON, formata número/data pra
 * leitura humana, e desenha isso em cartões/gráficos/tabelas.
 *
 * Por que nada de framework (React/Vue/etc.): a página inteira é ~4
 * requisições fetch + 3 gráficos. Um bundler resolveria um problema que a
 * gente não tem, e qualquer pessoa consegue ler este arquivo de cima a baixo
 * sem instalar nada — o que também é a proposta didática do projeto.
 */

// ---------------------------------------------------------------------------
// Formatação: centralizamos aqui pra não espalhar `toLocaleString` mágico
// pelo código todo, e pra deixar explícito que os valores financeiros vêm da
// API como STRING (o export em Python serializa Decimal como string, pra não
// perder precisão em somas grandes) — por isso o Number(...) antes de tudo.
// ---------------------------------------------------------------------------

const formatoMoedaCompacta = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  notation: "compact",
  maximumFractionDigits: 1,
});

const formatoMoedaCheia = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const formatoInteiro = new Intl.NumberFormat("pt-BR");

const formatoPercentual = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  maximumFractionDigits: 0,
});

function moeda(valorString, { compacta = true } = {}) {
  const numero = Number(valorString ?? 0);
  return (compacta ? formatoMoedaCompacta : formatoMoedaCheia).format(numero);
}

function percentual(valor) {
  // taxa_execucao vem null quando valor_empenhado é 0 (nada foi destinado,
  // então "quanto disso foi pago" não faz sentido como razão) — mostramos
  // um traço em vez de "0%", que sugeriria falsamente execução zero.
  return valor === null || valor === undefined ? "—" : formatoPercentual.format(valor);
}

function inteiro(valor) {
  return formatoInteiro.format(Number(valor ?? 0));
}

function mesLegivel(mesAAAAMM) {
  // "2024-04" -> "abril de 2024". new Date("2024-04-01") é interpretado em
  // UTC pelo navegador; fixamos o dia 2 (meio-dia relativo) só pra não correr
  // risco de o fuso horário local "empurrar" a data pro mês anterior.
  const data = new Date(`${mesAAAAMM}-02T00:00:00`);
  return new Intl.DateTimeFormat("pt-BR", { month: "long", year: "numeric" }).format(data);
}

// Lê o valor resolvido de uma CSS custom property (variável definida em
// css/style.css) — assim o JS usa exatamente a mesma cor que o resto da
// página, incluindo a troca automática claro/escuro, em vez de duplicar hex
// codes aqui.
function corTema(nomeVariavel) {
  return getComputedStyle(document.documentElement).getPropertyValue(nomeVariavel).trim();
}

// ---------------------------------------------------------------------------
// Configuração global do Chart.js: uma vez só, valendo pra todo gráfico.
// ---------------------------------------------------------------------------

function configurarChartJsGlobal() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = corTema("--texto-secundario");
  Chart.defaults.borderColor = corTema("--linha-grade");
}

// ---------------------------------------------------------------------------
// KPIs
// ---------------------------------------------------------------------------

function renderizarKPIs(kpis) {
  const percentualLiberado = kpis.total_valor
    ? (Number(kpis.total_valor_liberado) / Number(kpis.total_valor)) * 100
    : 0;

  const cartoes = [
    {
      rotulo: "Total repassado em convênios",
      valor: moeda(kpis.total_valor),
      detalhe: `valor integral: ${moeda(kpis.total_valor, { compacta: false })}`,
    },
    {
      rotulo: "Já liberado às pontas",
      valor: moeda(kpis.total_valor_liberado),
      detalhe: `${percentualLiberado.toFixed(0)}% do total repassado`,
    },
    { rotulo: "Convênios monitorados", valor: inteiro(kpis.numero_convenios), detalhe: null },
    { rotulo: "Municípios atendidos", valor: inteiro(kpis.numero_municipios), detalhe: null },
    { rotulo: "Órgãos envolvidos", valor: inteiro(kpis.numero_orgaos), detalhe: null },
    {
      rotulo: "Órgão que mais repassou",
      valor: kpis.orgao_maior_gasto?.nome ?? "—",
      detalhe: kpis.orgao_maior_gasto ? moeda(kpis.orgao_maior_gasto.valor) : null,
    },
    {
      rotulo: "Mês recorde",
      valor: kpis.mes_pico ? mesLegivel(kpis.mes_pico.mes) : "—",
      detalhe: kpis.mes_pico ? moeda(kpis.mes_pico.valor) : null,
    },
  ];

  const grade = document.getElementById("kpi-grade");
  grade.replaceChildren(
    ...cartoes.map((c) => {
      const cartao = document.createElement("article");
      cartao.className = "kpi-cartao";

      const rotulo = document.createElement("p");
      rotulo.className = "kpi-rotulo";
      rotulo.textContent = c.rotulo; // textContent: nomes/labels são "dado não confiável", nunca innerHTML

      const valor = document.createElement("p");
      valor.className = "kpi-valor";
      valor.textContent = c.valor;

      cartao.append(rotulo, valor);

      if (c.detalhe) {
        const detalhe = document.createElement("p");
        detalhe.className = "kpi-detalhe";
        detalhe.textContent = c.detalhe;
        cartao.append(detalhe);
      }
      return cartao;
    })
  );

  const meta = document.getElementById("meta-atualizacao");
  const periodo = `${kpis.periodo_inicio ?? "?"} a ${kpis.periodo_fim ?? "?"}`;
  const geradoEm = new Date(kpis.gerado_em);
  meta.textContent = `Dados de ${periodo} · atualizado automaticamente em ${geradoEm.toLocaleString("pt-BR")}`;
}

// ---------------------------------------------------------------------------
// Gráfico: ranking de órgãos (barra horizontal, uma cor só — magnitude, não
// identidade, então não faz sentido uma cor por barra).
// ---------------------------------------------------------------------------

function renderizarGraficoOrgaos(ranking) {
  const top15 = ranking.slice(0, 15);

  new Chart(document.getElementById("grafico-orgaos"), {
    type: "bar",
    data: {
      labels: top15.map((o) => o.sigla || o.nome),
      datasets: [
        {
          data: top15.map((o) => Number(o.valor_total)),
          backgroundColor: corTema("--serie-azul"),
          borderRadius: 4,
          maxBarThickness: 24,
        },
      ],
    },
    options: {
      indexAxis: "y",
      // Sem legenda: uma série só, o título da seção já diz o que é.
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (itens) => top15[itens[0].dataIndex].nome, // nome completo no tooltip; sigla no eixo
            label: (item) => moeda(item.raw, { compacta: false }),
          },
        },
      },
      scales: {
        x: {
          ticks: { callback: (valor) => moeda(valor) },
          grid: { color: corTema("--linha-grade") },
        },
        y: { grid: { display: false } },
      },
    },
  });

  preencherTabela("tabela-orgaos", ranking, (o) => [
    o.nome,
    moeda(o.valor_total, { compacta: false }),
    inteiro(o.numero_convenios),
  ]);
}

// ---------------------------------------------------------------------------
// Gráfico: série temporal mensal (linha, uma série).
// ---------------------------------------------------------------------------

function renderizarGraficoSerie(serie) {
  new Chart(document.getElementById("grafico-serie"), {
    type: "line",
    data: {
      labels: serie.map((p) => mesLegivel(p.mes)),
      datasets: [
        {
          data: serie.map((p) => Number(p.valor_total)),
          borderColor: corTema("--serie-azul"),
          backgroundColor: corTema("--serie-azul"),
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: corTema("--serie-azul"),
          // Anel na cor da superfície ao redor de cada ponto, pra ele não
          // "grudar" visualmente na linha — mesma ideia do "surface ring".
          pointBorderColor: corTema("--superficie"),
          pointBorderWidth: 2,
          tension: 0.15,
          fill: false,
        },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false }, // crosshair: acerta o X, não o pixel exato da linha
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) => moeda(item.raw, { compacta: false }),
          },
        },
      },
      scales: {
        y: {
          ticks: { callback: (valor) => moeda(valor) },
          grid: { color: corTema("--linha-grade") },
        },
        x: { grid: { display: false } },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Ranking de municípios + filtro em cascata Região -> UF -> Município.
// Tudo client-side: os ~5.500 municípios cabem tranquilamente num único JSON
// pequeno, então filtrar em JavaScript é instantâneo e dispensa qualquer
// backend/API no momento em que o visitante usa o filtro.
// ---------------------------------------------------------------------------

let municipiosTodos = [];
let graficoMunicipios = null;

function popularSelect(select, opcoes, textoPadrao) {
  select.replaceChildren(
    new Option(textoPadrao, ""),
    ...opcoes.map((o) => new Option(o.texto, o.valor))
  );
}

function municipiosFiltrados() {
  const regiao = document.getElementById("filtro-regiao").value;
  const uf = document.getElementById("filtro-uf").value;
  const codigoIbge = document.getElementById("filtro-municipio").value;

  return municipiosTodos.filter(
    (m) =>
      (!regiao || m.regiao === regiao) &&
      (!uf || m.uf_sigla === uf) &&
      (!codigoIbge || m.codigo_ibge === codigoIbge)
  );
}

function atualizarRankingMunicipios() {
  // municipiosTodos já vem ordenado por valor_total desc do export_static_json.py.
  const filtrados = municipiosFiltrados();
  const top20 = filtrados.slice(0, 20);

  const nota = document.getElementById("nota-municipios");
  nota.textContent =
    filtrados.length > top20.length
      ? `Mostrando os 20 maiores de ${inteiro(filtrados.length)} municípios encontrados no filtro atual.`
      : `Mostrando ${inteiro(filtrados.length)} município(s) no filtro atual.`;

  if (graficoMunicipios) {
    graficoMunicipios.data.labels = top20.map((m) => `${m.nome} (${m.uf_sigla})`);
    graficoMunicipios.data.datasets[0].data = top20.map((m) => Number(m.valor_total));
    graficoMunicipios.update();
  } else {
    graficoMunicipios = new Chart(document.getElementById("grafico-municipios"), {
      type: "bar",
      data: {
        labels: top20.map((m) => `${m.nome} (${m.uf_sigla})`),
        datasets: [
          {
            data: top20.map((m) => Number(m.valor_total)),
            backgroundColor: corTema("--serie-azul"),
            borderRadius: 4,
            maxBarThickness: 24,
          },
        ],
      },
      options: {
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (item) => moeda(item.raw, { compacta: false }) },
          },
        },
        scales: {
          x: {
            ticks: { callback: (valor) => moeda(valor) },
            grid: { color: corTema("--linha-grade") },
          },
          y: { grid: { display: false } },
        },
      },
    });
  }

  preencherTabela("tabela-municipios", filtrados, (m) => [
    m.nome,
    m.uf_sigla,
    m.regiao,
    moeda(m.valor_total, { compacta: false }),
    inteiro(m.numero_convenios),
  ]);
}

function configurarFiltrosMunicipio() {
  const selectRegiao = document.getElementById("filtro-regiao");
  const selectUf = document.getElementById("filtro-uf");
  const selectMunicipio = document.getElementById("filtro-municipio");

  const regioes = [...new Set(municipiosTodos.map((m) => m.regiao))].sort();
  popularSelect(
    selectRegiao,
    regioes.map((r) => ({ valor: r, texto: r })),
    "Todas"
  );

  function atualizarOpcoesUf() {
    const regiao = selectRegiao.value;
    const base = regiao ? municipiosTodos.filter((m) => m.regiao === regiao) : municipiosTodos;
    const ufsUnicas = [...new Map(base.map((m) => [m.uf_sigla, m.uf_nome])).entries()]
      .sort((a, b) => a[1].localeCompare(b[1], "pt-BR"));
    popularSelect(
      selectUf,
      ufsUnicas.map(([sigla, nome]) => ({ valor: sigla, texto: `${nome} (${sigla})` })),
      "Todas"
    );
    selectMunicipio.value = "";
  }

  function atualizarOpcoesMunicipio() {
    const uf = selectUf.value;
    if (!uf) {
      // Sem UF selecionada, a lista completa (~5.500 municípios) seria um
      // <select> gigante e pouco usável — preferimos manter esse combo
      // desabilitado até o visitante restringir por UF primeiro.
      popularSelect(selectMunicipio, [], "Selecione uma UF primeiro");
      selectMunicipio.disabled = true;
      return;
    }
    const municipiosDaUf = municipiosTodos
      .filter((m) => m.uf_sigla === uf)
      .sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR"));
    popularSelect(
      selectMunicipio,
      municipiosDaUf.map((m) => ({ valor: m.codigo_ibge, texto: m.nome })),
      "Todos"
    );
    selectMunicipio.disabled = false;
  }

  selectRegiao.addEventListener("change", () => {
    atualizarOpcoesUf();
    atualizarOpcoesMunicipio();
    atualizarRankingMunicipios();
  });
  selectUf.addEventListener("change", () => {
    atualizarOpcoesMunicipio();
    atualizarRankingMunicipios();
  });
  selectMunicipio.addEventListener("change", atualizarRankingMunicipios);

  atualizarOpcoesUf();
  atualizarOpcoesMunicipio();
}

// ---------------------------------------------------------------------------
// Emendas parlamentares: KPIs, gráfico empenhado x pago, filtro individual/
// coletivo e tabela com ordenação por coluna.
// ---------------------------------------------------------------------------

function renderizarKPIsEmendas(kpis) {
  const cartoes = [
    { rotulo: "Total empenhado (proposto)", valor: moeda(kpis.total_empenhado), detalhe: null },
    { rotulo: "Total pago (executado)", valor: moeda(kpis.total_pago), detalhe: null },
    {
      rotulo: "Total cancelado sem execução",
      valor: moeda(kpis.total_cancelado),
      detalhe: "empenhado e depois oficialmente cancelado — nunca virou entrega",
    },
    { rotulo: "Taxa de execução geral", valor: percentual(kpis.taxa_execucao_geral), detalhe: null },
    {
      rotulo: "Parlamentares individuais rastreados",
      valor: inteiro(kpis.numero_parlamentares_individuais),
      detalhe: `emendas de ${kpis.ano_inicio} a ${kpis.ano_fim}`,
    },
  ];

  const grade = document.getElementById("kpi-grade-emendas");
  grade.replaceChildren(
    ...cartoes.map((c) => {
      const cartao = document.createElement("article");
      cartao.className = "kpi-cartao";

      const rotulo = document.createElement("p");
      rotulo.className = "kpi-rotulo";
      rotulo.textContent = c.rotulo;

      const valor = document.createElement("p");
      valor.className = "kpi-valor";
      valor.textContent = c.valor;

      cartao.append(rotulo, valor);

      if (c.detalhe) {
        const detalhe = document.createElement("p");
        detalhe.className = "kpi-detalhe";
        detalhe.textContent = c.detalhe;
        cartao.append(detalhe);
      }
      return cartao;
    })
  );
}

let parlamentaresTodos = [];
let graficoParlamentares = null;
let ordenacaoParlamentares = { chave: "valor_empenhado", direcao: "desc" };

// Colunas de texto ordenam A→Z por padrão; colunas numéricas ordenam do
// maior pro menor por padrão (é o que faz sentido na primeira vez que se
// clica em "Taxa de execução": ver os maiores primeiro, não os menores).
const COLUNAS_TEXTO_PARLAMENTARES = new Set(["nome", "tipo_emenda"]);

function parlamentaresFiltrados() {
  const soIndividual = document.getElementById("filtro-so-individual").checked;
  return soIndividual ? parlamentaresTodos.filter((p) => p.individual) : parlamentaresTodos;
}

function renderizarGraficoParlamentares(lista) {
  const top15 = lista.slice(0, 15);

  const dados = {
    labels: top15.map((p) => p.nome),
    datasets: [
      {
        label: "Empenhado (proposto)",
        data: top15.map((p) => Number(p.valor_empenhado)),
        backgroundColor: corTema("--serie-azul-fraca"),
        borderRadius: 4,
        maxBarThickness: 18,
      },
      {
        label: "Pago (executado)",
        data: top15.map((p) => Number(p.valor_pago)),
        backgroundColor: corTema("--serie-azul"),
        borderRadius: 4,
        maxBarThickness: 18,
      },
    ],
  };

  if (graficoParlamentares) {
    graficoParlamentares.data = dados;
    graficoParlamentares.update();
  } else {
    graficoParlamentares = new Chart(document.getElementById("grafico-parlamentares"), {
      type: "bar",
      data: dados,
      options: {
        indexAxis: "y",
        // Duas séries (empenhado x pago) -> legenda sempre visível, pra
        // identidade não depender só da cor.
        plugins: {
          legend: { position: "top", align: "start" },
          tooltip: {
            callbacks: { label: (item) => `${item.dataset.label}: ${moeda(item.raw, { compacta: false })}` },
          },
        },
        scales: {
          x: {
            ticks: { callback: (valor) => moeda(valor) },
            grid: { color: corTema("--linha-grade") },
          },
          y: { grid: { display: false } },
        },
      },
    });
  }

  const nota = document.getElementById("nota-parlamentares");
  nota.textContent =
    lista.length > top15.length
      ? `Mostrando os 15 maiores de ${inteiro(lista.length)} autores no filtro atual.`
      : `Mostrando ${inteiro(lista.length)} autor(es) no filtro atual.`;
}

// O Brasil tem centenas de autores de emenda ativos por ano (parlamentares +
// bancadas + comissões) — sem limite, essa tabela facilmente passa de mil
// linhas e deixa a página inteira pesada e quilométrica. Mostramos só as
// LIMITE_TABELA_PARLAMENTARES primeiras do critério de ordenação atual; como
// o cabeçalho é clicável, o visitante controla qual "recorte" de linhas vê
// (maior empenhado, pior taxa de execução, etc.) em vez de rolar uma lista
// gigante procurando.
const LIMITE_TABELA_PARLAMENTARES = 50;

function renderizarTabelaParlamentares(lista) {
  const { chave, direcao } = ordenacaoParlamentares;
  const sinal = direcao === "asc" ? 1 : -1;

  const ordenada = [...lista].sort((a, b) => {
    const va = a[chave];
    const vb = b[chave];
    if (va === null || va === undefined) return 1; // nulos (taxa_execucao sem empenho) sempre por último
    if (vb === null || vb === undefined) return -1;
    if (typeof va === "string") return sinal * va.localeCompare(vb, "pt-BR");
    return sinal * (Number(va) - Number(vb));
  });

  const visiveis = ordenada.slice(0, LIMITE_TABELA_PARLAMENTARES);

  preencherTabela("tabela-parlamentares", visiveis, (p) => [
    p.nome,
    p.tipo_emenda,
    moeda(p.valor_empenhado, { compacta: false }),
    moeda(p.valor_pago, { compacta: false }),
    moeda(p.valor_resto_cancelado, { compacta: false }),
    percentual(p.taxa_execucao),
    inteiro(p.numero_emendas),
  ]);

  document.querySelectorAll("#tabela-parlamentares thead th").forEach((th) => {
    if (th.dataset.chave === chave) {
      th.setAttribute("data-ordem", direcao);
    } else {
      th.removeAttribute("data-ordem");
    }
  });

  const nota = document.getElementById("nota-tabela-parlamentares");
  nota.textContent =
    ordenada.length > visiveis.length
      ? `Mostrando os ${visiveis.length} primeiros de ${inteiro(ordenada.length)} autores, ordenado pela coluna selecionada.`
      : `Mostrando ${inteiro(visiveis.length)} autor(es).`;
}

function atualizarEmendas() {
  const lista = parlamentaresFiltrados();
  renderizarGraficoParlamentares(lista);
  renderizarTabelaParlamentares(lista);
}

function configurarInteracoesEmendas() {
  document.getElementById("filtro-so-individual").addEventListener("change", atualizarEmendas);

  document.querySelectorAll("#tabela-parlamentares thead th[data-chave]").forEach((th) => {
    th.addEventListener("click", () => {
      const chave = th.dataset.chave;
      if (ordenacaoParlamentares.chave === chave) {
        ordenacaoParlamentares.direcao = ordenacaoParlamentares.direcao === "asc" ? "desc" : "asc";
      } else {
        ordenacaoParlamentares = {
          chave,
          direcao: COLUNAS_TEXTO_PARLAMENTARES.has(chave) ? "asc" : "desc",
        };
      }
      renderizarTabelaParlamentares(parlamentaresFiltrados());
    });
  });
}

// ---------------------------------------------------------------------------
// Tabelas (a alternativa acessível a cada gráfico) + botões de alternância.
// ---------------------------------------------------------------------------

function preencherTabela(idTabela, linhas, montarCelulas) {
  const corpo = document.querySelector(`#${idTabela} tbody`);
  corpo.replaceChildren(
    ...linhas.map((linha) => {
      const tr = document.createElement("tr");
      tr.append(
        ...montarCelulas(linha).map((valorCelula) => {
          const td = document.createElement("td");
          td.textContent = valorCelula; // nunca innerHTML: dado vem da API, não é confiável
          return td;
        })
      );
      return tr;
    })
  );
}

function configurarBotoesTabela() {
  document.querySelectorAll(".botao-tabela").forEach((botao) => {
    botao.addEventListener("click", () => {
      const wrapper = document.getElementById(`tabela-${botao.dataset.alvo}-wrapper`);
      const estaOculto = wrapper.classList.toggle("oculto");
      botao.textContent = estaOculto ? "ver como tabela" : "ocultar tabela";
    });
  });
}

// ---------------------------------------------------------------------------
// Ponto de entrada.
// ---------------------------------------------------------------------------

async function buscarJson(caminho) {
  const resposta = await fetch(caminho);
  if (!resposta.ok) {
    throw new Error(`Falha ao buscar ${caminho}: HTTP ${resposta.status}`);
  }
  return resposta.json();
}

async function iniciarConvenios() {
  const [kpis, ranking, serie, municipios] = await Promise.all([
    buscarJson("data/kpis.json"),
    buscarJson("data/ranking_orgaos.json"),
    buscarJson("data/serie_temporal.json"),
    buscarJson("data/municipios.json"),
  ]);

  renderizarKPIs(kpis);
  renderizarGraficoOrgaos(ranking);
  renderizarGraficoSerie(serie);

  municipiosTodos = municipios;
  configurarFiltrosMunicipio();
  atualizarRankingMunicipios();
}

async function iniciarEmendas() {
  const [kpis, ranking] = await Promise.all([
    buscarJson("data/kpis_emendas.json"),
    buscarJson("data/ranking_parlamentares.json"),
  ]);

  renderizarKPIsEmendas(kpis);
  parlamentaresTodos = ranking;
  configurarInteracoesEmendas();
  atualizarEmendas();
}

async function iniciar() {
  configurarChartJsGlobal();
  configurarBotoesTabela();

  // Convênios e emendas são duas fontes de dado independentes — cada uma
  // tenta carregar por conta própria, então se uma faltar (ex.: o workflow
  // de emendas ainda não rodou), a outra seção do site continua funcionando
  // normalmente em vez da página inteira ficar em branco por causa de uma
  // falha isolada.
  const resultados = await Promise.allSettled([iniciarConvenios(), iniciarEmendas()]);

  const erros = resultados.filter((r) => r.status === "rejected");
  const meta = document.getElementById("meta-atualizacao");
  if (erros.length > 0) {
    erros.forEach((r) => console.error(r.reason));
    if (erros.length === resultados.length) {
      meta.textContent =
        "Não foi possível carregar os dados agora. Se você abriu este arquivo direto " +
        "(file://), sirva a pasta docs/ com um servidor HTTP local — veja o README.";
    } else {
      meta.textContent += " (uma das seções não carregou — veja o console para detalhes)";
    }
  }
}

iniciar();
