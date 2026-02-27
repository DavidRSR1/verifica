// =============================
// ARQUIVO PRINCIPAL DE FUNÇÕES DO FRONTEND
// Comentário orientado para a equipe: 
// Todas as funções e variáveis principais da SPA estão aqui. 
// Estruture as funções por blocos comentados e mantenha a organização para facilitar manutenção!
// =============================

// Endpoint base para as APIs (ajuste se necessário)
const API = '';

// Token JWT do usuário, recuperado do localStorage para persistência automática entre sessões
let userJwt = localStorage.getItem('panel_jwt') || null;

// ── TEMA (Modo Claro/Escuro) ──
// Controla o tema (dark/light) usando localStorage e atributo data-theme (CSS)
// currentTheme guarda o tema ativo
let currentTheme = localStorage.getItem('panel_theme') || 'dark';

// Aplica o tema (visual do app) e muda ícone/botão de alternância
function applyTheme(theme) {
  currentTheme = theme;
  localStorage.setItem('panel_theme', theme);
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('themeToggle');
  if (btn) btn.innerHTML = theme === 'dark' ? 'Claro' : 'Escuro';
}

// Alterna entre claro/escuro
function toggleTheme() {
  applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

// ── EMPRESAS ISENTAS DE NOTA FISCAL ──
// Lista com nomes (ou fragmentos) de empresas isentas de NF (NF == Nota Fiscal)
// Quando uma empresa está nesta lista, a coluna/viz de NF será tratada de forma especial
const EMPRESAS_ISENTAS_NF = [
  'Sr Transportadora E Locadora De Caminhoes Sa.',
  // Adicione novas aqui caso haja outras empresas isentas
];

// Função para checar se uma empresa é isenta conforme a lista acima
function isIsentaNF(nomeEmpresa) {
  if (!nomeEmpresa) return false;
  const n = nomeEmpresa.toLowerCase();
  // Considera se algum fragmento listado está incluso (busca case-insensitive)
  return EMPRESAS_ISENTAS_NF.some(e => n.includes(e));
}

// ── STATE PRINCIPAL ──
// Estrutura central de estado do App SPA, todas as variáveis globais que mudam a tela estão aqui
let state = {
  providers:[],              // lista de fornecedores/adquirentes disponíveis
  activeProvider:null,       // slug do fornecedor selecionado
  activePostoCnpj:null,      // cnpj do posto selecionado
  activePostoNome:null,      // nome do posto selecionado (para cabeçalho)
  activeSection:'vendas',    // seção ativa: vendas ou reembolsos
  dataIni:daysAgo(15),       // data inicial, padrão: últimos 15 dias
  dataFim:today(),           // data final de consulta, padrão: hoje
  sortVendas:{col:'data_abastecimento',dir:'desc'},      // ordenação default vendas
  sortReembolsos:{col:'data_pagamento',dir:'desc'},      // ordenação default reembolsos
  reembByPagamento:true,     // modo de exibição do reembolso por pagamento ou por venda
  _cacheVendas:[],           // cache local das vendas filtradas
  _cacheReembolsos:[],       // cache local dos reembolsos filtrados
};

// ── AUTENTICAÇÃO/JWT ──
// Aplica o token JWT salvo na interface e atualiza o localStorage. 
function applyJwt(){
  const v = document.getElementById('jwtInput').value.trim();
  userJwt = v||null;
  v ? localStorage.setItem('panel_jwt',v) : localStorage.removeItem('panel_jwt');
  updateAuthUI();
  init();
}

// Faz logout - limpa JWT do localStorage e UI
function logout(){
  userJwt=null;
  localStorage.removeItem('panel_jwt');
  document.getElementById('jwtInput').value='';
  updateAuthUI();
  init();
}

// Atualiza interface de login/logout (exibe/esconde áreas do topo)
function updateAuthUI(){
  document.getElementById('authArea').style.display = userJwt?'none':'flex';
  document.getElementById('userChip').style.display  = userJwt?'flex':'none';
}

// Monta headers com JWT (caso disponível) para requisições protegidas
function headers(){
  const h={'Content-Type':'application/json'};
  if(userJwt) h['Authorization']=`Bearer ${userJwt}`;
  return h;
}

// ── DATAS ÚTEIS ──
// Retorna data de hoje em yyyy-mm-dd
function today(){return new Date().toISOString().slice(0,10);}

// Retorna data de N dias atrás em yyyy-mm-dd
function daysAgo(n){
  const d=new Date();
  d.setDate(d.getDate()-n);
  return d.toISOString().slice(0,10);
}

// ── FORMATAÇÃO DE NÚMEROS E DATAS ──

// Formatação de moeda brasileira (pt-BR), retorna "—" se inválido
function fmtMoney(v){
  const n=parseFloat(v);
  if(v===null||v===undefined||v===''||v==='None'||isNaN(n))return '—';
  return 'R$ '+n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
}

// Formatação de litros, retorna "—" se inválido
function fmtLitros(v){
  const n=parseFloat(v);
  if(v===null||v===undefined||v===''||v==='None'||isNaN(n))return '—';
  return n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+' L';
}

// Formatação de data para dd/mm/yyyy, aceita ISO
function fmtData(d) {
  if (!d) return '—';
  if (d.includes('/')) return d;
  const partes = d.split('T')[0].split('-');
  if (partes.length === 3) {
    return `${partes[2]}/${partes[1]}/${partes[0]}`;
  }
  return d;
}

// ── AJAX (Fetch simplificado com tratamento de erro) ──
async function get(url){
  const r=await fetch(API+url,{headers:headers()});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── INIT ──
// Função de inicialização. Sempre rodar após qualquer mudança de autenticação ou reset de tela.
async function init(){
  applyTheme(currentTheme);
  updateAuthUI();
  state.providers=await get('/api/providers');
  renderProviderTabs();
  if(state.providers.length) activateProvider(state.providers[0].slug);
}

// ── RENDERIZAÇÃO DE CELULAS EBRAS (COMBUSTÍVEL & ARLA) ──
// Exibe quebras de valores em células compostas (diesel/arla)
// Uso: 
//    renderBreakdownCell(valorComb, valorArla, total, funcFormat)
function renderBreakdownCell(valComb, valArla, valTotal, formatFn) {
  const c = parseFloat(valComb) || 0;
  const a = parseFloat(valArla) || 0;
  const t = parseFloat(valTotal) || (c + a);

  if (c > 0 && a > 0) {
    return `<div style="display:flex;flex-direction:column;gap:3px;font-size:0.68rem;line-height:1.1;text-align:right;">
      <span style="color:var(--muted);" title="Referente ao Combustível"> ${formatFn(c)}</span>
      <span style="color:#00bcd4;" title="Referente ao Arla 32"> ${formatFn(a)}</span>
      <strong style="color:inherit;border-top:1px solid var(--border);padding-top:3px;margin-top:1px;" title="Total da Transação">${formatFn(t)}</strong>
    </div>`;
  }
  return `<div style="text-align:right;">${formatFn(t)}</div>`;
}

// Exibe célula de produto: tags para diesel, arla ou misto
function renderProdutoCell(r) {
  const lComb = parseFloat(r.litros_combustivel) || 0;
  const lArla = parseFloat(r.litros_arla) || 0;
  const strComb = (r.combustivel || r.produto || '').toLowerCase();
  const strServ = (r.servico || '').toLowerCase();
  const hasArlaLegacy = strComb.includes('arla') || strServ.includes('arla');
  const hasDieselLegacy = strComb.includes('diesel') || strServ.includes('diesel');

  if ((lComb > 0 && lArla > 0) || (hasArlaLegacy && hasDieselLegacy)) {
    return `<span class="produto-misto" title="Venda Conjunta">
      <span class="tag-diesel">${(r.combustivel || r.produto || 'Diesel').split(',')[0]}</span>
      <span class="tag-arla">Arla</span>
    </span>`;
  }
  if (lArla > 0 || hasArlaLegacy) return `<span class="tag-arla">Arla 32</span>`;
  return `<span class="tag-diesel">${r.combustivel || r.produto || 'Combustível'}</span>`;
}

// ── PROVIDER TABS ──
// Renderiza as abas dos fornecedores/adquirentes acima da sidebar
function renderProviderTabs(){
  const el=document.getElementById('providerTabs');
  el.innerHTML=state.providers.map(p=>`
    <button class="tab-btn ${p.slug===state.activeProvider?'active':''}"
            onclick="activateProvider('${p.slug}')"
            style="${p.slug===state.activeProvider?`border-bottom-color:${p.color};color:${p.color}`:''}">
      <span class="provider-icon">${p.icon}</span>
      <span class="provider-name">${p.name}</span>
      ${p.slug===state.activeProvider?`<span class="provider-active-dot" style="background:${p.color}"></span>`:''}
      ${!p.has_postos?`<span class="coming-soon-tag">EM BREVE</span>`:''}
    </button>`).join('');
}

// Ativa um provider, recarrega tela principal, busca postos daquele provider
async function activateProvider(slug){
  state.activeProvider=slug;
  state.activePostoCnpj=null;
  renderProviderTabs();
  renderMainContent();
  const provider=state.providers.find(p=>p.slug===slug);
  if(!provider||!provider.has_postos){
    document.getElementById('postoList').innerHTML=`<div class="no-postos">Sem postos disponíveis</div>`;
    return;
  }
  document.getElementById('postoList').innerHTML=`<div class="loading"><div class="spinner"></div></div>`;
  try{
    const postos=await get(`/api/${slug}/postos`);
    renderPostoList(postos);
  }catch(e){
    document.getElementById('postoList').innerHTML=`<div class="no-postos">Erro ao carregar postos</div>`;
  }
}

// ── SIDEBAR DE POSTOS (com grupos/squads) ──
// Organiza e mostra os postos por "squad" se houver, senão um bloco único
function renderPostoList(postos){
  const el=document.getElementById('postoList');
  if(!postos.length){
    el.innerHTML=`<div class="no-postos">Nenhum posto visível para o seu perfil</div>`;
    return;
  }

  // Agrupamento por squad (se houver)
  const grupos={};
  postos.forEach(p=>{
    const k = p.squad_id || '__sem_squad__';
    if(!grupos[k]) grupos[k] = { nome: p.squads?.nome || 'Sem squad', itens: [] };
    grupos[k].itens.push(p);
  });

  const keys=Object.keys(grupos);
  const multi=keys.length>1;
  let html='';

  keys.forEach(k=>{
    const grupo = grupos[k];
    if(multi){
      html+=`<div class="squad-label">${grupo.nome}</div>`;
    }
    html+=`<div class="posto-list-inner">`;
    grupo.itens.forEach(p=>{
      const ativo=p.cnpj===state.activePostoCnpj;
      html+=`<div class="posto-item ${ativo?'active':''}"
              onclick="selectPosto('${p.cnpj}','${(p.nome||'').replace(/'/g,"\\'")}')">
        <div class="posto-info">
          <div class="posto-name">${p.nome_curto||p.nome}</div>
          <div class="posto-cnpj">${p.cnpj}</div>
        </div>
        <span class="posto-arrow">&#9654;</span>
      </div>`;
    });
    html+=`</div>`;
  });
  el.innerHTML=html;
}

// Seta o posto selecionado e recarrega tela principal (ajusta destaque visual)
function selectPosto(cnpj,nome){
  state.activePostoCnpj=cnpj;
  state.activePostoNome=nome;
  document.querySelectorAll('.posto-item').forEach(el=>{
    el.classList.toggle('active',el.querySelector('.posto-cnpj')?.textContent?.trim()===cnpj);
    const nm=el.querySelector('.posto-name');
    if(nm) nm.style.color=el.classList.contains('active')?'var(--accent)':'';
  });
  renderMainContent();
}



// ── CONTEÚDO PRINCIPAL DO APP ──
// Renderiza o topo e grids/tabelas dinâmicas de acordo com seleções
function renderMainContent(){
  const el=document.getElementById('mainContent');
  const provider=state.providers.find(p=>p.slug===state.activeProvider);
  if(!provider||!provider.has_postos){
    el.innerHTML=`<div class="coming-soon-block"><div class="icon">${provider?.icon||''}</div>
      <h3>${provider?.name||'Adquirente'} — Em breve</h3>
      <p>A integração está em desenvolvimento. Postos aparecerão aqui automaticamente.</p></div>`;
    return;
  }
  if(!state.activePostoCnpj){
    el.innerHTML=`<div class="no-selection"><div class="icon"></div><p>Selecione um posto para visualizar os dados</p></div>`;
    return;
  }

  const providerColor = provider?.color || 'var(--accent)';
  const providerIcon = provider?.icon || '';
  const providerName = provider?.name || state.activeProvider.toUpperCase();

  el.innerHTML=`
    <div class="posto-header">
      <h2>${state.activePostoNome}</h2>
      <p class="posto-meta">
        <span>${state.activePostoCnpj}</span>
        <span class="provider-badge" style="background:${providerColor}22;color:${providerColor};border-color:${providerColor}44">
          ${providerIcon} ${providerName}
        </span>
      </p>
    </div>
    <div class="filters">
      <div class="filter-group">
        <label>De</label>
        <input type="date" id="dateIni" value="${state.dataIni}" onchange="state.dataIni=this.value"/>
      </div>
      <div class="filter-group">
        <label>Até</label>
        <input type="date" id="dateFim" value="${state.dataFim}" onchange="state.dataFim=this.value"/>
      </div>
      <button class="filter-btn" onclick="loadData()">Filtrar</button>
      <div id="filterBadge" class="filter-badge" style="display:none"></div>
    </div>
    <div class="fetch-indicator" id="fetchIndicator">
      <div class="spinner"></div> Buscando no ${providerName} para o período selecionado…
    </div>
    <div id="kpiSection" class="kpi-grid"><div class="loading"><div class="spinner"></div></div></div>
    <div class="section-tabs">
      <button class="section-tab ${state.activeSection==='vendas'?'active':''}" onclick="switchSection('vendas')">Vendas</button>
      <button class="section-tab ${state.activeSection==='reembolsos'?'active':''}" onclick="switchSection('reembolsos')">Reembolsos</button>
    </div>
    <div id="tableSection"><div class="loading"><div class="spinner"></div></div></div>`;
  loadData();
}

// Atalho para set de datas rápidas
function setQuick(n){
  state.dataIni=daysAgo(n);
  state.dataFim=today();
  const i=document.getElementById('dateIni'),f=document.getElementById('dateFim');
  if(i)i.value=state.dataIni;
  if(f)f.value=state.dataFim;
  loadData();
}

// Alterna tab de vendas/reembolsos (mudando state.activeSection)
function switchSection(s){
  state.activeSection=s;
  document.querySelectorAll('.section-tab').forEach(el=>
    el.classList.toggle('active',el.textContent.toLowerCase().startsWith(s.slice(0,5))));
  loadTable();
}

// ── FUNÇÕES DE CARREGAMENTO DE DADOS (API) ──

// Carrega dados principais (tabela + KPIs) após qualquer filtro ou seleção
async function loadData(){
  const i=document.getElementById('dateIni'),f=document.getElementById('dateFim');
  if(i)state.dataIni=i.value;
  if(f)state.dataFim=f.value;
  await loadTable();
  await loadKPIs();
}

// Versão de requisições, usado para evitar "race condition" em requests assíncronos
let _fetchVersion = 0;

// Carrega/atualiza KPIs do topo para o posto/período selecionado
async function loadKPIs(){
  const el=document.getElementById('kpiSection');
  if(!el) return;
  el.innerHTML=`<div class="loading"><div class="spinner"></div></div>`;
  showFetch(true);
  try{
    const d=await get(`/api/${state.activeProvider}/resumo?cnpj=${enc(state.activePostoCnpj)}&data_ini=${state.dataIni}&data_fim=${state.dataFim}`);
    showFetch(false);
    el.innerHTML=`
      <div class="kpi" style="--kpi-color:#00C896">
        <div class="kpi-label">Total Vendas</div>
        <div class="kpi-value money">${fmtMoney(d.total_vendas)}</div>
        <div class="kpi-sub">${d.qtd_vendas} transações</div>
      </div>
      <div class="kpi" style="--kpi-color:#5082FF">
        <div class="kpi-label">Litros Vendidos</div>
        <div class="kpi-value number">${fmtLitros(d.total_litros)}</div>
        <div class="kpi-sub">no período</div>
      </div>
      <div class="kpi" style="--kpi-color:#FFB700">
        <div class="kpi-label">Reembolsos</div>
        <div class="kpi-value" style="color:#FFB700">${fmtMoney(d.total_reembolso)}</div>
        <div class="kpi-sub">${d.qtd_reembolsos} registros</div>
      </div>`;
  }catch(e){
    showFetch(false);
    el.innerHTML=`<div style="color:var(--muted);font-family:var(--font-mono);font-size:.76rem;padding:12px;grid-column:1/-1">Erro ao carregar KPIs: ${e.message}</div>`;
  }
}

// Carrega dados da tabela principal (vendas ou reembolsos), respeitando filtros atuais
async function loadTable(){
  const el=document.getElementById('tableSection');
  if(!el) return;
  const myVersion = ++_fetchVersion;
  const mySection = state.activeSection;
  el.innerHTML=`<div class="loading"><div class="spinner"></div> carregando ${state.activeSection}…</div>`;
  try{
    let url=`/api/${state.activeProvider}/${mySection}?cnpj=${enc(state.activePostoCnpj)}&data_ini=${state.dataIni}&data_fim=${state.dataFim}`;
    if(mySection==='reembolsos') url+=`&by_pagamento=${state.reembByPagamento?1:0}`;
    const data=await get(url);
    // Só atualiza se não teve nova requisição depois desta (evita sobrescrever outro filtro)
    if(myVersion!==_fetchVersion || mySection!==state.activeSection) return;
    if(mySection==='vendas'){
      state._cacheVendas=data;
      el.innerHTML=renderVendas(sortRows(data,state.sortVendas));
    }else{
      state._cacheReembolsos=data;
      el.innerHTML=renderReembolsos(sortRows(data,state.sortReembolsos));
    }
  }catch(e){
    if(myVersion!==_fetchVersion) return;
    el.innerHTML=`<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">Erro: ${e.message}</div></div>`;
  }
}

// ── ORDENAÇÃO GENÉRICA PARA TABELAS ──
// Ordena array de rows usando sortState {col,dir}
function sortRows(rows, sortState){
  const {col,dir}=sortState;
  return [...rows].sort((a,b)=>{
    let va=a[col],vb=b[col];
    const nullA=(va==null||va===''),nullB=(vb==null||vb==='');
    if(nullA&&nullB)return 0;
    if(nullA)return 1;
    if(nullB)return -1;
    const na=parseFloat(va),nb=parseFloat(vb);
    if(!isNaN(na)&&!isNaN(nb)){va=na;vb=nb;}
    else{va=va.toString().toLowerCase();vb=vb.toString().toLowerCase();}
    if(va<vb)return dir==='asc'?-1:1;
    if(va>vb)return dir==='asc'?1:-1;
    return 0;
  });
}

// Alterna a coluna e direção do sort da tabela (vendas/reembolsos)
function toggleSort(section,col){
  const s=section==='vendas'?state.sortVendas:state.sortReembolsos;
  if(s.col===col) s.dir=s.dir==='asc'?'desc':'asc';
  else{s.col=col;s.dir='asc';}
  const cache=section==='vendas'?state._cacheVendas:state._cacheReembolsos;
  const el=document.getElementById('tableSection');if(!el)return;
  el.innerHTML=section==='vendas'
    ? renderVendas(sortRows(cache,state.sortVendas))
    : renderReembolsos(sortRows(cache,state.sortReembolsos));
}

// Alterna o modo de agrupamento dos reembolsos: por pagamento ou por venda
function toggleReembMode(){
  state.reembByPagamento=!state.reembByPagamento;
  loadTable();
}

// Ativa/desativa indicador de carregamento global
function showFetch(v){
  const el=document.getElementById('fetchIndicator');
  if(el)el.classList.toggle('active',v);
}

// Função utilitária: faz encodeURIComponent (útil para montar URLs com campos livres)
function enc(v){return encodeURIComponent(v||'');}

// ── BADGES DE STATUS ──
// Gera badge colorido para status, usado nas tabelas
// Cores base: verde (ok), amarelo (pendente), vermelho (erro), azul (outros/info)
function badge(s, overrideIsenta){
  if(!s) return '<span class="badge badge-blue">—</span>';
  if(overrideIsenta && s.toLowerCase().includes('pendent')) {
    return `<span class="badge badge-blue" title="Empresa isenta de emissão de NF">Isento</span>`;
  }
  const v=s.toLowerCase();
  if(v.includes('autoriz')||v.includes('pago')||v.includes('emitid'))
    return `<span class="badge badge-green">${s}</span>`;
  if(v.includes('pendent')||v.includes('process'))
    return `<span class="badge badge-yellow">${s}</span>`;
  if(v.includes('negad')||v.includes('cancel')||v.includes('erro'))
    return `<span class="badge badge-red">${s}</span>`;
  return `<span class="badge badge-blue">${s}</span>`;
}

// ── GERADORES DE TABELA VENDAS/REEMBOLSOS ──
// (Estrutura padrão: thX,coluna→thead; renderX→tbody+tfoot;)
// Cores, agrupamentos, somatórios e legibilidade visual devem ser mantidos conforme padrão produto!

// Gera <th> para tabela de vendas, com ícone de ordenação/destacar, col = chave do campo
function thV(col,label){
  const s=state.sortVendas;
  const cls=s.col===col?(s.dir==='asc'?'sort-asc':'sort-desc'):'';
  return `<th class="${cls}" onclick="toggleSort('vendas','${col}')">${label}<span class="sort-arrow"></span></th>`;
}

// Tabela de VENDAS do período selecionado
function renderVendas(rows){
  if(!rows.length) return `<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">Nenhuma venda no período</div></div>`;

  // Acumuladores para resumo no <tfoot>
  let totalValor = 0, totalLitros = 0;
  let totalDieselLitros = 0, totalArlaLitros = 0;
  let totalDieselValor = 0, totalArlaValor = 0;
  let temArla = false;

  // Laço central: acumula totais para <tfoot> e detecta se tem arla para separar nas somas
  rows.forEach(r => {
    const tValor = parseFloat(r.valor_total) || 0;
    const tLitros = parseFloat(r.quantidade_litros) || 0;
    totalValor += tValor;
    totalLitros += tLitros;

    const lComb = parseFloat(r.litros_combustivel) || 0;
    const vComb = parseFloat(r.valor_combustivel) || 0;
    const lArla = parseFloat(r.litros_arla) || 0;
    const vArla = parseFloat(r.valor_arla) || 0;

    if (lComb > 0 || lArla > 0) {
      totalDieselLitros += lComb;
      totalDieselValor += vComb;
      if (lArla > 0) {
        temArla = true;
        totalArlaLitros += lArla;
        totalArlaValor += vArla;
      }
    } else {
      const prod = (r.produto || '').toLowerCase();
      if (prod.includes('arla')) {
        temArla = true;
        totalArlaLitros += tLitros;
        totalArlaValor += tValor;
      } else {
        totalDieselLitros += tLitros;
        totalDieselValor += tValor;
      }
    }
  });

  // Linhas <tfoot> extras se achou arla
  const footerSeparacao = temArla ? `
    <tr style="background: rgba(80,130,255,0.06);">
      <td colspan="6" style="text-align:right;font-size:.62rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em">Diesel</td>
      <td class="number" style="font-weight:700;color:#5082FF;text-align:right">${fmtLitros(totalDieselLitros)}</td>
      <td></td>
      <td class="money" style="font-weight:700;color:#5082FF;text-align:right">${fmtMoney(totalDieselValor)}</td>
      <td colspan="2"></td>
    </tr>
    <tr style="background: rgba(0,188,212,0.06);">
      <td colspan="6" style="text-align:right;font-size:.62rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em">Arla 32</td>
      <td class="number" style="font-weight:700;color:#00bcd4;text-align:right">${fmtLitros(totalArlaLitros)}</td>
      <td></td>
      <td class="money" style="font-weight:700;color:#00bcd4;text-align:right">${fmtMoney(totalArlaValor)}</td>
      <td colspan="2"></td>
    </tr>` : '';

  // Monta tabela principal de vendas do período, com somatório e agrupamento visual
  return `<div class="table-wrap">
    <div class="table-header">
      <span class="table-title">VENDAS DO PERÍODO</span>
      <span class="table-count">${rows.length} registros</span>
    </div>
    <div class="table-scroll"><table>
      <thead><tr>
        ${thV('data_abastecimento','Data')}
        ${thV('hora_abastecimento','Hora')}
        ${thV('nome_frota','Frota')}
        ${thV('placa_veiculo','Placa')}
        ${thV('nome_motorista','Motorista')}
        ${thV('produto','Produto')}
        ${thV('quantidade_litros','Litros')}
        ${thV('valor_unitario','Vlr Unit.')}
        ${thV('valor_total','Valor Total')}
        ${thV('status_autorizacao','Autorização')}
        ${thV('status_nota_fiscal','NF')}
      </tr></thead>
      <tbody>${rows.map(r=>`<tr>
        <td>${fmtData(r.data_abastecimento)}</td>
        <td>${r.hora_abastecimento||'—'}</td>
        <td>${r.nome_frota||'—'}</td>
        <td>${r.placa_veiculo||'—'}</td>
        <td>${r.nome_motorista||'—'}</td>
        <td>${renderProdutoCell(r)}</td>
        <td class="number">${renderBreakdownCell(r.litros_combustivel, r.litros_arla, r.quantidade_litros, fmtLitros)}</td>
        <td class="money" style="text-align:right">${fmtMoney(r.valor_unitario)}</td>
        <td class="money">${renderBreakdownCell(r.valor_combustivel, r.valor_arla, r.valor_total, fmtMoney)}</td>
        <td>${badge(r.status_autorizacao)}</td>
        <td>${badge(r.status_nota_fiscal)}</td>
      </tr>`).join('')}</tbody>
      <tfoot>
        ${footerSeparacao}
        <tr style="background: var(--surface2); border-top: 1px solid var(--border);">
          <td colspan="6" style="text-align: right; font-weight: 700; font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;">Total da Seleção:</td>
          <td class="number" style="font-weight: 800; text-align:right">${fmtLitros(totalLitros)}</td>
          <td></td>
          <td class="money" style="font-weight: 800; text-align:right">${fmtMoney(totalValor)}</td>
          <td colspan="2"></td>
        </tr>
      </tfoot>
    </table></div></div>`;
}

// Gera <th> para tabela de reembolsos, com sort
function thR(col,label){
  const s=state.sortReembolsos;
  const cls=s.col===col?(s.dir==='asc'?'sort-asc':'sort-desc'):'';
  return `<th class="${cls}" onclick="toggleSort('reembolsos','${col}')">${label}<span class="sort-arrow"></span></th>`;
}

// Tabela de REEMBOLSOS do período selecionado (funciona para modos por pagamento ou por venda)
function renderReembolsos(rows){
  if(!rows.length) return `<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">Nenhum reembolso no período</div></div>`;

  // Totais de itens, litros, valor das faturas/depósitos, com separação diesel/arla se houver
  let totalItens = 0, totalLitros = 0, totalFaturas = 0;
  let totalDieselLitros = 0, totalArlaLitros = 0;
  let totalDieselValor = 0, totalArlaValor = 0;
  const faturasVistas = new Set();
  let temArla = false;

  rows.forEach(r => {
    const tValor = parseFloat(r.valor_total) || 0;
    const tLitros = parseFloat(r.litros) || 0;
    totalItens += tValor;
    totalLitros += tLitros;

    const lComb = parseFloat(r.litros_combustivel) || 0;
    const vComb = parseFloat(r.valor_combustivel) || 0;
    const lArla = parseFloat(r.litros_arla) || 0;
    const vArla = parseFloat(r.valor_arla) || 0;

    if (lComb > 0 || lArla > 0) {
      totalDieselLitros += lComb;
      totalDieselValor += vComb;
      if (lArla > 0) {
        temArla = true;
        totalArlaLitros += lArla;
        totalArlaValor += vArla;
      }
    } else {
      const comb = (r.combustivel || r.servico || '').toLowerCase();
      if (comb.includes('arla')) {
        temArla = true;
        totalArlaLitros += tLitros;
        totalArlaValor += tValor;
      } else {
        totalDieselLitros += tLitros;
        totalDieselValor += tValor;
      }
    }

    // Filtro fatura/depósito: considera apenas valores únicos por empresa+data_pagamento+valor
    if (r.reembolso_total) {
      const chave = `${r.empresa}|${r.data_pagamento}|${r.reembolso_total}`;
      if (!faturasVistas.has(chave)) {
        faturasVistas.add(chave);
        totalFaturas += parseFloat(r.reembolso_total);
      }
    }
  });

  const footerSeparacao = temArla ? `
    <tr style="background: rgba(80,130,255,0.06);">
      <td colspan="6" style="text-align:right;font-size:.62rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em">Diesel</td>
      <td class="number" style="font-weight:700;color:#5082FF;text-align:right">${fmtLitros(totalDieselLitros)}</td>
      <td class="money" style="font-weight:700;color:#5082FF;text-align:right">${fmtMoney(totalDieselValor)}</td>
      <td colspan="2"></td>
    </tr>
    <tr style="background: rgba(0,188,212,0.06);">
      <td colspan="6" style="text-align:right;font-size:.62rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em">Arla 32</td>
      <td class="number" style="font-weight:700;color:#00bcd4;text-align:right">${fmtLitros(totalArlaLitros)}</td>
      <td class="money" style="font-weight:700;color:#00bcd4;text-align:right">${fmtMoney(totalArlaValor)}</td>
      <td colspan="2"></td>
    </tr>` : '';

  // Label exibido no toggle de modo reembolso
  const modeLabel = state.reembByPagamento
    ? '<span class="mode-active">Por Pagamento</span> / Por Venda'
    : 'Por Pagamento / <span class="mode-active">Por Venda</span>';

  return `<div class="table-wrap">
    <div class="table-header">
      <span class="table-title">REEMBOLSOS DO PERÍODO</span>
      <div style="display:flex;align-items:center;gap:12px">
        <span class="table-count">${rows.length} registros</span>
        <div class="date-mode-toggle">
          <label class="toggle-switch">
            <input type="checkbox" ${state.reembByPagamento?'checked':''} onchange="toggleReembMode()">
            <span class="toggle-slider"></span>
          </label>
          <span style="font-size:.62rem">${modeLabel}</span>
          <div id="filterBadge" class="filter-badge" style="display:none"></div>
        </div>
      </div>
    </div>
    <div class="table-scroll"><table>
      <thead><tr>
        ${thR('data','Data Venda')}
        ${thR('hora','Hora')}
        ${thR('empresa','Empresa')}
        ${thR('nota_fiscal','NF')}
        ${thR('placa_motorista','Placa / Motorista')}
        ${thR('combustivel','Combustível')}
        ${thR('litros','Litros')}
        ${thR('valor_total','Valor Total')}
        ${thR('status_pagamento','Status Pag.')}
        ${thR('data_pagamento','Data Pag. *')}
      </tr></thead>
      <tbody>${
        rows.map(r=>{
          const isenta = isIsentaNF(r.empresa);
          return `<tr>
            <td>${fmtData(r.data)}</td>
            <td>${r.hora||'—'}</td>
            <td>${r.empresa||'—'}${isenta?` <span class="badge-isenta" title="Isenta de NF">Isenta NF</span>`:''}</td>
            <td>${r.nota_fiscal||'—'}</td>
            <td style="white-space:pre-line">${r.placa_motorista||'—'}</td>
            <td>${renderProdutoCell(r)}</td>
            <td class="number" style="padding-right:15px">${renderBreakdownCell(r.litros_combustivel, r.litros_arla, null, fmtLitros)}</td>
            <td class="money" style="padding-right:15px">${
              r.valor_total != null
                ? renderBreakdownCell(r.valor_combustivel, r.valor_arla, r.valor_total, fmtMoney)
                : r.reembolso_total != null
                  ? `<span title="Exibindo total da fatura" style="color:var(--muted);font-style:italic;float:right">${fmtMoney(r.reembolso_total)} *</span>`
                  : '<span style="color:var(--muted);float:right">—</span>'
            }</td>
            <td>${badge(r.status_pagamento)}</td>
            <td style="color:${r.data_pagamento?'var(--accent)':'var(--muted)'}">${fmtData(r.data_pagamento)}</td>
          </tr>`;
        }).join('')
      }</tbody>
      <tfoot>
        ${footerSeparacao}
        <tr style="background: var(--surface2); border-top: 1px solid var(--border);">
          <td colspan="6" style="text-align: right; font-weight: 700; font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;">Soma dos Itens Exibidos:</td>
          <td class="number" style="font-weight: 800; text-align:right">${fmtLitros(totalLitros)}</td>
          <td class="money" style="font-weight: 800; text-align:right">${fmtMoney(totalItens)}</td>
          <td colspan="2"></td>
        </tr>
        <tr style="background: rgba(0, 200, 150, 0.08);">
          <td colspan="7" style="text-align: right; font-weight: 800; font-size: 0.68rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em;">Depósito Real (Total das Faturas):</td>
          <td class="money" style="font-weight: 800; color: var(--accent); text-align:right">${fmtMoney(totalFaturas)}</td>
          <td colspan="2"></td>
        </tr>
      </tfoot>
    </table></div></div>`;
}

// Chama o init ao carregar o script para habilitar toda a lógica (não remova)
init();
