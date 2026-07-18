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

function moeda(valorString, { compacta = true } = {}) {
  const numero = Number(valorString ?? 0);
  return (compacta ? formatoMoedaCompacta : formatoMoedaCheia).format(numero);
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

async function iniciar() {
  configurarChartJsGlobal();
  configurarBotoesTabela();

  try {
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
  } catch (erro) {
    console.error(erro);
    document.getElementById("meta-atualizacao").textContent =
      "Não foi possível carregar os dados agora. Se você abriu este arquivo direto " +
      "(file://), sirva a pasta docs/ com um servidor HTTP local — veja o README.";
  }
}

iniciar();
