const API = '';
let userJwt = localStorage.getItem('panel_jwt') || null;
let state = {
  providers:[],activeProvider:null,
  activePostoCnpj:null,activePostoNome:null,
  activeSection:'vendas',
  dataIni:daysAgo(15),dataFim:today(),
  sortVendas:{col:'data_abastecimento',dir:'desc'},
  sortReembolsos:{col:'data_pagamento',dir:'desc'},
  reembByPagamento:true,
  _cacheVendas:[],
  _cacheReembolsos:[],
};

// ── AUTH ──────────────────────────────────────────────────────
function applyJwt(){
  const v = document.getElementById('jwtInput').value.trim();
  userJwt = v||null;
  v ? localStorage.setItem('panel_jwt',v) : localStorage.removeItem('panel_jwt');
  updateAuthUI();
  init();
}
function logout(){
  userJwt=null;localStorage.removeItem('panel_jwt');
  document.getElementById('jwtInput').value='';
  updateAuthUI();init();
}
function updateAuthUI(){
  document.getElementById('authArea').style.display = userJwt?'none':'flex';
  document.getElementById('userChip').style.display  = userJwt?'flex':'none';
}
function headers(){
  const h={'Content-Type':'application/json'};
  if(userJwt) h['Authorization']=`Bearer ${userJwt}`;
  return h;
}

// ── DATES ─────────────────────────────────────────────────────
function today(){return new Date().toISOString().slice(0,10);}
function daysAgo(n){const d=new Date();d.setDate(d.getDate()-n);return d.toISOString().slice(0,10);}

// ── NUMBER FORMAT ─────────────────────────────────────────────
function fmtMoney(v){
  const n=parseFloat(v);
  if(v===null||v===undefined||v===''||v==='None'||isNaN(n))return '—';
  return 'R$ '+n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function fmtLitros(v){
  const n=parseFloat(v);
  if(v===null||v===undefined||v===''||v==='None'||isNaN(n))return '—';
  return n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+' L';
}
function fmtData(d) {
  if (!d) return '—';
  // Se por acaso já vier formatado (ex: data_bruta), retorna como está
  if (d.includes('/')) return d;
  
  // Pega apenas a parte da data (YYYY-MM-DD) e inverte para DD/MM/YYYY
  const partes = d.split('T')[0].split('-');
  if (partes.length === 3) {
    return `${partes[2]}/${partes[1]}/${partes[0]}`;
  }
  return d;
}

async function get(url){
  const r=await fetch(API+url,{headers:headers()});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── INIT ──────────────────────────────────────────────────────
async function init(){
  updateAuthUI();
  state.providers=await get('/api/providers');
  renderProviderTabs();
  if(state.providers.length) activateProvider(state.providers[0].slug);
}

// ── PROVIDER TABS ─────────────────────────────────────────────
function renderProviderTabs(){
  const el=document.getElementById('providerTabs');
  el.innerHTML=state.providers.map(p=>`
    <button class="tab-btn ${p.slug===state.activeProvider?'active':''}"
            onclick="activateProvider('${p.slug}')"
            style="${p.slug===state.activeProvider?`border-bottom-color:${p.color};color:${p.color}`:''}">
      <span>${p.icon}</span><span>${p.name}</span>
      ${!p.has_postos?`<span class="coming-soon-tag">EM BREVE</span>`:''}
    </button>`).join('');
}

async function activateProvider(slug){
  state.activeProvider=slug;state.activePostoCnpj=null;
  renderProviderTabs();renderMainContent();
  const provider=state.providers.find(p=>p.slug===slug);
  if(!provider||!provider.has_postos){
    document.getElementById('postoList').innerHTML=`<div class="no-postos">Sem postos disponíveis</div>`;return;
  }
  document.getElementById('postoList').innerHTML=`<div class="loading"><div class="spinner"></div></div>`;
  try{
    const postos=await get(`/api/${slug}/postos`);
    renderPostoList(postos);
  }catch(e){
    document.getElementById('postoList').innerHTML=`<div class="no-postos">Erro ao carregar postos</div>`;
  }
}

// ── SIDEBAR com squads ────────────────────────────────────────
function renderPostoList(postos){
  const el=document.getElementById('postoList');
  if(!postos.length){el.innerHTML=`<div class="no-postos">Nenhum posto visível para o seu perfil</div>`;return;}

  // Agrupa por squad_id
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
        <span class="posto-arrow">▶</span>
      </div>`;
    });
    html+=`</div>`;
  });
  el.innerHTML=html;
}

function selectPosto(cnpj,nome){
  state.activePostoCnpj=cnpj;state.activePostoNome=nome;
  document.querySelectorAll('.posto-item').forEach(el=>{
    el.classList.toggle('active',el.querySelector('.posto-cnpj')?.textContent?.trim()===cnpj);
    const nm=el.querySelector('.posto-name');
    if(nm) nm.style.color=el.classList.contains('active')?'var(--accent)':'';
  });
  renderMainContent();
}

// ── MAIN CONTENT ──────────────────────────────────────────────
function renderMainContent(){
  const el=document.getElementById('mainContent');
  const provider=state.providers.find(p=>p.slug===state.activeProvider);
  if(!provider||!provider.has_postos){
    el.innerHTML=`<div class="coming-soon-block"><div class="icon">${provider?.icon||'🔌'}</div>
      <h3>${provider?.name||'Adquirente'} — Em breve</h3>
      <p>A integração está em desenvolvimento. Postos aparecerão aqui automaticamente.</p></div>`;
    return;
  }
  if(!state.activePostoCnpj){
    el.innerHTML=`<div class="no-selection"><div class="icon">⛽</div><p>Selecione um posto para visualizar os dados</p></div>`;
    return;
  }
  el.innerHTML=`
    <div class="posto-header">
      <h2>${state.activePostoNome}</h2>
      <p>${state.activePostoCnpj} · ${state.activeProvider.toUpperCase()}</p>
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
      
    </div>
    <div class="fetch-indicator" id="fetchIndicator">
      <div class="spinner"></div> Buscando no Profrotas para o período selecionado…
    </div>
    <div id="kpiSection" class="kpi-grid"><div class="loading"><div class="spinner"></div></div></div>
    <div class="section-tabs">
      <button class="section-tab ${state.activeSection==='vendas'?'active':''}" onclick="switchSection('vendas')">Vendas</button>
      <button class="section-tab ${state.activeSection==='reembolsos'?'active':''}" onclick="switchSection('reembolsos')">Reembolsos</button>
    </div>
    <div id="tableSection"><div class="loading"><div class="spinner"></div></div></div>`;
  loadData();
}

function setQuick(n){
  state.dataIni=daysAgo(n);state.dataFim=today();
  const i=document.getElementById('dateIni'),f=document.getElementById('dateFim');
  if(i)i.value=state.dataIni;if(f)f.value=state.dataFim;
  loadData();
}

function switchSection(s){
  state.activeSection=s;
  document.querySelectorAll('.section-tab').forEach(el=>
    el.classList.toggle('active',el.textContent.toLowerCase().startsWith(s.slice(0,5))));
  loadTable();
}

// ── LOAD DATA ─────────────────────────────────────────────────
async function loadData(){
  const i=document.getElementById('dateIni'),f=document.getElementById('dateFim');
  if(i)state.dataIni=i.value;if(f)state.dataFim=f.value;
  // Sequencial: primeiro carrega a tabela ativa (pode fazer on-demand),
  // depois atualiza KPIs — evita race condition com duas chamadas ao on-demand
  await loadTable();
  await loadKPIs();
}

// Controle de versão de fetch para evitar race conditions
let _fetchVersion = 0;

async function loadKPIs(){
  const el=document.getElementById('kpiSection');if(!el)return;
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
    el.innerHTML=`<div style="color:var(--muted);font-family:var(--font-mono);font-size:.76rem;padding:12px;grid-column:1/-1">⚠ Erro ao carregar KPIs: ${e.message}</div>`;
  }
}

async function loadTable(){
  const el=document.getElementById('tableSection');if(!el)return;
  // Controle de versão: descarta respostas de fetches anteriores (evita race condition)
  const myVersion = ++_fetchVersion;
  const mySection = state.activeSection;
  el.innerHTML=`<div class="loading"><div class="spinner"></div> carregando ${state.activeSection}…</div>`;
  try{
    let url=`/api/${state.activeProvider}/${mySection}?cnpj=${enc(state.activePostoCnpj)}&data_ini=${state.dataIni}&data_fim=${state.dataFim}`;
    if(mySection==='reembolsos') url+=`&by_pagamento=${state.reembByPagamento?1:0}`;
    const data=await get(url);
    // Descarta se um fetch mais recente já foi iniciado ou se a seção mudou
    if(myVersion!==_fetchVersion || mySection!==state.activeSection) return;
    if(mySection==='vendas'){state._cacheVendas=data;el.innerHTML=renderVendas(sortRows(data,state.sortVendas));}
    else{state._cacheReembolsos=data;el.innerHTML=renderReembolsos(sortRows(data,state.sortReembolsos));}
  }catch(e){
    if(myVersion!==_fetchVersion) return;
    el.innerHTML=`<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-text">Erro: ${e.message}</div></div>`;
  }
}

// ── SORT ──────────────────────────────────────────────────────
function sortRows(rows, sortState){
  const {col,dir}=sortState;
  return [...rows].sort((a,b)=>{
    let va=a[col],vb=b[col];
    // null/undefined sempre vai para o fim, independente da direção
    const nullA=(va==null||va===''),nullB=(vb==null||vb==='');
    if(nullA&&nullB)return 0;
    if(nullA)return 1;
    if(nullB)return -1;
    // tenta número
    const na=parseFloat(va),nb=parseFloat(vb);
    if(!isNaN(na)&&!isNaN(nb)){va=na;vb=nb;}
    else{va=va.toString().toLowerCase();vb=vb.toString().toLowerCase();}
    if(va<vb)return dir==='asc'?-1:1;
    if(va>vb)return dir==='asc'?1:-1;
    return 0;
  });
}

function toggleSort(section,col){
  const s=section==='vendas'?state.sortVendas:state.sortReembolsos;
  if(s.col===col) s.dir=s.dir==='asc'?'desc':'asc';
  else{s.col=col;s.dir='asc';}
  const cache=section==='vendas'?state._cacheVendas:state._cacheReembolsos;
  const el=document.getElementById('tableSection');if(!el)return;
  el.innerHTML=section==='vendas'?renderVendas(sortRows(cache,state.sortVendas)):renderReembolsos(sortRows(cache,state.sortReembolsos));
}

function toggleReembMode(){
  state.reembByPagamento=!state.reembByPagamento;
  loadTable();
}

function showFetch(v){const el=document.getElementById('fetchIndicator');if(el)el.classList.toggle('active',v);}
function enc(v){return encodeURIComponent(v||'');}

// ── BADGE ──────────────────────────────────────────────────────
function badge(s){
  if(!s)return '<span class="badge badge-blue">—</span>';
  const v=s.toLowerCase();
  if(v.includes('autoriz')||v.includes('pago')||v.includes('emitid'))return `<span class="badge badge-green">${s}</span>`;
  if(v.includes('pendent')||v.includes('process'))return `<span class="badge badge-yellow">${s}</span>`;
  if(v.includes('negad')||v.includes('cancel')||v.includes('erro'))return `<span class="badge badge-red">${s}</span>`;
  return `<span class="badge badge-blue">${s}</span>`;
}

// ── TABLES ────────────────────────────────────────────────────
function thV(col,label){
  const s=state.sortVendas;
  const cls=s.col===col?(s.dir==='asc'?'sort-asc':'sort-desc'):'';
  return `<th class="${cls}" onclick="toggleSort('vendas','${col}')">${label}<span class="sort-arrow"></span></th>`;
}

function renderVendas(rows){
  if(!rows.length) return `<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">Nenhuma venda no período</div></div>`;

  // Processamento seguro dos totais ignorando nulos e strings sujas
  const totais = rows.reduce((acc, r) => {
    const valor = parseFloat(r.valor_total) || 0;
    const litros = parseFloat(r.quantidade_litros) || 0;
    return { valor: acc.valor + valor, litros: acc.litros + litros };
  }, { valor: 0, litros: 0 });

  return `<div class="table-wrap">
    <div class="table-header"><span class="table-title">VENDAS DO PERÍODO</span><span class="table-count">${rows.length} registros</span></div>
    <div class="table-scroll"><table>
      <thead><tr>
        ${thV('data_abastecimento','Data')}${thV('hora_abastecimento','Hora')}
        ${thV('nome_frota','Frota')}${thV('placa_veiculo','Placa')}${thV('nome_motorista','Motorista')}
        ${thV('produto','Produto')}${thV('quantidade_litros','Litros')}
        ${thV('valor_unitario','Vlr Unit.')}${thV('valor_total','Valor Total')}
        ${thV('status_autorizacao','Autorização')}${thV('status_nota_fiscal','NF')}
      </tr></thead>
      <tbody>${rows.map(r=>`<tr>
        <td>${fmtData(r.data_abastecimento)}</td>
        <td>${r.hora_abastecimento||'—'}</td>
        <td>${r.nome_frota||'—'}</td>
        <td>${r.placa_veiculo||'—'}</td>
        <td>${r.nome_motorista||'—'}</td>
        <td>${r.produto||'—'}</td>
        <td class="number">${fmtLitros(r.quantidade_litros)}</td>
        <td class="money">${fmtMoney(r.valor_unitario)}</td>
        <td class="money">${fmtMoney(r.valor_total)}</td>
        <td>${badge(r.status_autorizacao)}</td>
        <td>${badge(r.status_nota_fiscal)}</td>
      </tr>`).join('')}</tbody>
      
      <tfoot>
        <tr style="background: var(--surface2); border-top: 1px solid var(--border);">
          <td colspan="6" style="text-align: right; font-weight: 700; font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;">Total da Seleção:</td>
          <td class="number" style="font-weight: 800;">${fmtLitros(totais.litros)}</td>
          <td></td>
          <td class="money" style="font-weight: 800;">${fmtMoney(totais.valor)}</td>
          <td colspan="2"></td>
        </tr>
      </tfoot>

    </table></div></div>`;
}

function thR(col,label){
  const s=state.sortReembolsos;
  const cls=s.col===col?(s.dir==='asc'?'sort-asc':'sort-desc'):'';
  return `<th class="${cls}" onclick="toggleSort('reembolsos','${col}')">${label}<span class="sort-arrow"></span></th>`;
}

function renderReembolsos(rows){
  if(!rows.length)return `<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">Nenhum reembolso no período</div></div>`;

  let totalItens = 0;
  let totalLitros = 0;
  let totalFaturas = 0;
  const faturasVistas = new Set();

  rows.forEach(r => {
    // 1. Soma dos itens individuais visíveis
    totalItens += parseFloat(r.valor_total) || 0;
    totalLitros += parseFloat(r.litros) || 0;
    
    // 2. Isolamento de Faturas Únicas para calcular o valor real que cai no banco
    if (r.reembolso_total) {
      // Cria uma chave única para não somar a mesma fatura duas vezes
      const chave = `${r.empresa}|${r.data_pagamento}|${r.reembolso_total}`;
      if (!faturasVistas.has(chave)) {
        faturasVistas.add(chave);
        totalFaturas += parseFloat(r.reembolso_total);
      }
    }
  });

  return `<div class="table-wrap">
    <div class="table-header">
      <span class="table-title">REEMBOLSOS DO PERÍODO</span>
      <span class="table-count">${rows.length} registros</span>
    </div>
    <div class="table-scroll"><table>
      <thead><tr>
        ${thR('data','Data Venda')}${thR('hora','Hora')}
        ${thR('empresa','Empresa')}${thR('nota_fiscal','NF')}
        ${thR('placa_motorista','Placa / Motorista')}${thR('combustivel','Combustível')}
        ${thR('litros','Litros')}${thR('valor_total','Valor Total')}
        ${thR('status_pagamento','Status Pag.')}${thR('data_pagamento','Data Pag. ★')}
      </tr></thead>
      <tbody>${rows.map(r=>`<tr>
        <td>${fmtData(r.data)}</td>
        <td>${r.hora||'—'}</td>
        <td>${r.empresa||'—'}</td>
        <td>${r.nota_fiscal||'—'}</td>
        <td style="white-space:pre-line">${r.placa_motorista||'—'}</td>
        <td>${r.combustivel||'—'}</td>
        <td class="number">${fmtLitros(r.litros)}</td>
        <td class="money">${
          r.valor_total != null
            ? fmtMoney(r.valor_total)
            : r.reembolso_total != null
              ? `<span title="Valor individual não disponível — exibindo total da fatura" style="color:var(--muted);cursor:help;font-style:italic">${fmtMoney(r.reembolso_total)} *</span>`
              : '<span style="color:var(--muted)">—</span>'
        }</td>
        <td>${badge(r.status_pagamento)}</td>
        <td style="color:${r.data_pagamento?'var(--accent)':'var(--muted)'}">${fmtData(r.data_pagamento)}</td>
      </tr>`).join('')}</tbody>
      
      <tfoot>
        <tr style="background: var(--surface2); border-top: 1px solid var(--border);">
          <td colspan="6" style="text-align: right; font-weight: 700; font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;">Soma dos Itens Exibidos:</td>
          <td class="number" style="font-weight: 800;">${fmtLitros(totalLitros)}</td>
          <td class="money" style="font-weight: 800;">${fmtMoney(totalItens)}</td>
          <td colspan="2"></td>
        </tr>
        <tr style="background: rgba(0, 200, 150, 0.08);">
          <td colspan="7" style="text-align: right; font-weight: 800; font-size: 0.68rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em;">Depósito Real (Total das Faturas):</td>
          <td class="money" style="font-weight: 800; color: var(--accent);">${fmtMoney(totalFaturas)}</td>
          <td colspan="2"></td>
        </tr>
      </tfoot>
      
    </table></div></div>`;
}

init();
