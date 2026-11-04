(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let files = [], busy = false, history = null, detail = null;
  let currentView = 'record', selected = new Set(), polling = new Set();
  let manualKey = crypto.randomUUID(), calibration = null;
  let detailRequest = 0, managementRequest = 0, historyRequest = 0, feedbackRequest = 0, detailDirty = false;
  const memoDrafts = new Map();
  const memoKey = () => person() + '/' + $('trainingDate').value;
  const stateNames = {queued:'분석 대기',running:'분석 중',failed:'분석 실패',interrupted:'분석 중단',succeeded:'분석 완료'};
  function analysisTime(run){
    const value=run?.finished_at||run?.created_at;if(!value)return '';
    const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';
    return ' · '+new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date);
  }
  const recordText = a => a.validity === 'foul' ? '파울' : (a.validity==='unknown'?'미확인 · ':'')+(a.measured_distance_cm == null ? (a.record_unknown?'기록 알 수 없음':'기록 미입력') : `${a.measured_distance_cm} cm`);
  function notice(text, error=false){ $('notice').textContent=text; $('notice').className=error?'error':''; }
  async function api(path, options={}){
    const response = await fetch(path, options.body instanceof FormData ? options : {...options,headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
    const data = await response.json().catch(()=>({error:'서버 응답을 읽지 못했습니다.'}));
    if(!response.ok || data.ok===false) throw new Error(data.error || '요청을 처리하지 못했습니다.');
    return data;
  }
  function action(id, fn){ const button=$(id);button.addEventListener('click',async e=>{
    if(button.dataset.pending==='1')return;button.dataset.pending='1';button.disabled=true;
    try{await fn(e);}catch(error){notice(error.message,true);}finally{delete button.dataset.pending;button.disabled=false;if(id==='go')updateGo();}
  }); }
  function today(){const d=new Date(); return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);}
  $('trainingDate').value=today();
  function person(){return $('person').value;}
  function personName(){return $('person').selectedOptions[0]?.textContent || '';}
  function requirePerson(){if(!person()) throw new Error('이름을 입력해 저장하거나 기존 이름을 선택하세요.');}
  function recordReady(f,i){return f.recordUnknown || (f.record!=='' && document.querySelector(`[data-record="${i}"]`).validity.valid);}
  function updateGo(){
    const ready=files.every(recordReady);
    $('go').disabled=busy || !person() || !files.length || !$('ownerConfirm').checked || !ready;
    $('uploadStatus').textContent=files.length&&!ready?'각 영상의 기록을 입력하거나 알 수 없습니다를 선택하세요.':'';
    $('ownerText').textContent=person()?`${personName()} 님이 뛴 영상입니다`:'선택한 사람이 뛴 영상입니다';
  }
  async function people(selectId){
    const data=await api('/api/athletes');
    $('person').innerHTML='<option value="">이름을 선택하세요</option>'+data.athletes.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');
    const id=selectId || localStorage.getItem('jumpcoach.person');
    if(data.athletes.some(p=>p.id===id)) $('person').value=id;
    updateGo();
  }
  function clearFiles(){files.forEach(f=>URL.revokeObjectURL(f.url)); files=[]; $('uploadCards').innerHTML=''; $('file').value=''; $('ownerConfirm').checked=false; updateGo();}
  async function choosePerson(){
    notice('');
    localStorage.setItem('jumpcoach.person',person());
    clearFiles(); detail=null; detailRequest++; $('detail').classList.add('hidden'); result.classList.add('hidden'); selected.clear();
    managementRequest++;historyRequest++;history=null;
    for(const id of ['management','historySummary','recordChart','metricChart','attemptList','metricSelect'])$(id).innerHTML='';
    $('focus').value='';$('practiceNote').value='';$('compareSaved').disabled=true;
    location.hash=''; updateGo(); await loadHistory();
  }
  $('person').addEventListener('change',()=>choosePerson().catch(e=>notice(e.message,true)));
  $('ownerConfirm').addEventListener('change',updateGo);
  $('trainingDate').addEventListener('change',()=>loadHistory().catch(e=>notice(e.message,true)));
  action('savePerson',async()=>{
    const data=await api('/api/athletes',{method:'POST',body:{name:$('personName').value}});
    await people(data.athlete.id); $('personName').value=''; await choosePerson(); notice(`${data.athlete.name} 님의 기록을 저장합니다.`);
  });
  $('personName').addEventListener('keydown',e=>{if(e.key==='Enter') $('savePerson').click();});
  function tab(view){currentView=view; $('recordView').classList.toggle('hidden',view!=='record'); $('historyView').classList.toggle('hidden',view!=='history'); $('recordTab').classList.toggle('active',view==='record'); $('historyTab').classList.toggle('active',view==='history');}
  action('recordTab',()=>tab('record'));
  action('historyTab',async()=>{tab('history');await loadHistory();});
  function setFiles(list){
    if(busy) return;
    clearFiles();
    const all=[...list];
    if(all.reduce((n,f)=>n+f.size,0)>300*1024*1024){notice('한 번에 300MB 이하로 선택하세요.',true);return;}
    files=all.slice(0,5).map(f=>({file:f,url:URL.createObjectURL(f),key:crypto.randomUUID(),runKey:crypto.randomUUID(),record:'',recordUnknown:false,validity:'valid'}));
    renderUploadCards();
    notice(all.length>5?'처음 5개 영상을 선택했습니다.':'');updateGo();
  }
  function renderUploadCards(){
    $('uploadCards').innerHTML=files.map((f,i)=>`<article class="upload-card"><video src="${f.url}" controls muted preload="metadata"></video><div><div class="upload-heading"><div class="filename">${esc(f.file.name)}</div><button class="danger" data-remove="${i}" aria-label="${i+1}번 영상 삭제">삭제</button></div><div class="fields"><input data-record="${i}" type="number" step="0.1" min="0.1" required value="${esc(f.record)}" placeholder="실측 기록 (cm)" aria-label="${i+1}번 영상 실측 기록"><select data-validity="${i}" aria-label="${i+1}번 영상 유효 여부"><option value="valid"${f.validity==='valid'?' selected':''}>유효</option><option value="foul"${f.validity==='foul'?' selected':''}>파울</option></select></div><label class="check record-unknown"><input data-unknown="${i}" type="checkbox"${f.recordUnknown?' checked':''}>알 수 없습니다</label><small class="small-note" data-progress="${i}"></small></div></article>`).join('');
    lockUploads();
  }
  function lockUploads(){
    $('file').disabled=busy;
    files.forEach((f,i)=>{
      document.querySelector(`[data-record="${i}"]`).disabled=busy||!!f.attempt||f.recordUnknown;
      for(const key of ['unknown','validity'])document.querySelector(`[data-${key}="${i}"]`).disabled=busy||!!f.attempt;
      document.querySelector(`[data-remove="${i}"]`).disabled=busy;
    });
  }
  $('uploadCards').addEventListener('input',e=>{
    const i=e.target.dataset.record;
    if(i!==undefined&&!busy&&!files[i].attempt){files[i].record=e.target.value;updateGo();}
  });
  $('uploadCards').addEventListener('change',e=>{
    const u=e.target.dataset.unknown,v=e.target.dataset.validity;
    if(u!==undefined&&!busy&&!files[u].attempt){files[u].recordUnknown=e.target.checked;
      if(e.target.checked){files[u].record='';document.querySelector(`[data-record="${u}"]`).value='';}
      lockUploads();updateGo();}
    if(v!==undefined&&!busy&&!files[v].attempt)files[v].validity=e.target.value;
  });
  $('uploadCards').addEventListener('click',async e=>{
    const button=e.target.closest('[data-remove]');if(!button||busy)return;
    const f=files[Number(button.dataset.remove)];busy=true;lockUploads();updateGo();
    try{
      if(f.attempt&&!await deleteSaved(f.attempt))return;
      const index=files.indexOf(f);if(index>=0){URL.revokeObjectURL(f.url);files.splice(index,1);}
      $('file').value='';renderUploadCards();
    }catch(error){console.error(error);notice(error.message,true);}
    finally{busy=false;lockUploads();updateGo();}
  });
  $('file').addEventListener('change',e=>{const list=[...e.target.files];setFiles(list);});
  $('drop').addEventListener('click',e=>{if(e.target!==$('file')&&!busy) $('file').click();});
  for(const type of ['dragenter','dragover']) $('drop').addEventListener(type,e=>{e.preventDefault();$('drop').classList.add('over');});
  for(const type of ['dragleave','drop']) $('drop').addEventListener(type,e=>{e.preventDefault();$('drop').classList.remove('over');});
  $('drop').addEventListener('drop',e=>setFiles(e.dataTransfer.files));
  function config(){return {time_mode:$('timeMode').value,slowdown:Number($('slowdown').value),camera_setup:$('cameraSetup').value.trim()};}
  action('go',async()=>{
    requirePerson(); if(!$('ownerConfirm').checked||busy) return;
    if(!files.length||!files.every(recordReady))throw new Error('각 영상의 실측 기록을 입력하거나 알 수 없습니다를 선택하세요.');
    const owner=person(), date=$('trainingDate').value, settings=config();
    if(!date||!settings.camera_setup) throw new Error('훈련일과 촬영 조건을 확인하세요.');
    busy=true;lockUploads();updateGo(); $('person').disabled=true; $('trainingDate').disabled=true; $('savePerson').disabled=true;
    let first=null;
    try{
      for(let i=0;i<files.length;i++){
        const f=files[i], progress=document.querySelector(`[data-progress="${i}"]`);
        progress.textContent='기록 저장 중';
        if(!f.attempt){
          f.attempt=(await api('/api/attempts',{method:'POST',body:{athlete_id:owner,performed_on:date,request_key:f.key,
            measured_distance_cm:f.record,record_unknown:f.recordUnknown,validity:f.validity,commit:true,
            measurement_setup:$('measurementSetup').value,capture_setup:settings.camera_setup}})).attempt;
        }
        if(!f.attempt.source_path){
          progress.textContent='영상 저장 중';
          const form=new FormData();form.append('video',f.file);form.append('expected_revision',f.attempt.revision);
          f.attempt=(await api(`/api/attempts/${f.attempt.id}/source`,{method:'PUT',body:form})).attempt;
        }
        progress.textContent='분석 대기';
        const run=(await api(`/api/attempts/${f.attempt.id}/analysis`,{method:'POST',body:{request_key:f.runKey,expected_analysis_input_revision:f.attempt.analysis_input_revision,config:settings}})).run;
        progress.textContent='저장 완료 · '+(stateNames[run.status]||'분석 대기');
        if(!first) first=f.attempt.id;
        watch(run.id,owner,f.attempt.id);
      }
      notice(`${personName()} 님의 영상과 기록을 저장했습니다.`);
      clearFiles();
      await loadHistory(); if(first) await openAttempt(first);
    } finally {busy=false;lockUploads();$('person').disabled=false;$('trainingDate').disabled=false;$('savePerson').disabled=false;updateGo();}
  });
  action('saveRecord',async()=>{
    requirePerson();
    if(!$('manualRecord').value && $('manualValidity').value!=='foul') throw new Error('실측 기록을 입력하세요.');
    await api('/api/attempts',{method:'POST',body:{athlete_id:person(),performed_on:$('trainingDate').value,request_key:manualKey,
      measured_distance_cm:$('manualRecord').value,validity:$('manualValidity').value,measurement_setup:$('measurementSetup').value}});
    manualKey=crypto.randomUUID();$('manualRecord').value='';notice('기록을 저장했습니다.');await loadHistory();tab('history');
  });
  async function watch(rid,owner,aid){
    if(polling.has(rid)) return;polling.add(rid);
    const tick=async()=>{
      try{
        const run=(await api(`/api/runs/${rid}`)).run;
        if(person()===owner){
          if(detail?.id===aid){const status=$('runStatus');if(status) status.textContent=stateNames[run.status]||run.status;}
        }
        if(['queued','running'].includes(run.status)){setTimeout(tick,1800);return;}
        polling.delete(rid);
        if(person()===owner){await loadHistory(); if(detail?.id===aid&&!detailDirty) await openAttempt(aid,false);else await refreshFeedback();}
      }catch(e){polling.delete(rid);notice('상태를 확인하지 못했습니다. 기록 모아보기에서 다시 열어 주세요.',true);}
    };tick();
  }
  const RECORD_TREND_WINDOW = 10;
  const RECORD_TREND_SLOPE_UP = 0.3;
  const RECORD_TREND_SLOPE_DOWN = -0.3;
  const RECORD_GROUP_COLORS = ['var(--accent)','var(--accent2)','#e0a84b','#c77dff','#ff9c9c'];
  function recordTrend(values){
    if(values.length<2) return {kind:'hold',text:'기록이 2개 이상 쌓이면 추세를 분석합니다.'};
    const recent=values.slice(-RECORD_TREND_WINDOW), n=recent.length;
    const meanX=(n-1)/2, meanY=recent.reduce((a,b)=>a+b,0)/n;
    let num=0,den=0;
    recent.forEach((v,i)=>{num+=(i-meanX)*(v-meanY);den+=(i-meanX)**2;});
    const slope=den?num/den:0, change=slope*(n-1);
    const kind=slope>=RECORD_TREND_SLOPE_UP?'up':slope<=RECORD_TREND_SLOPE_DOWN?'down':'flat';
    const emoji=kind==='up'?'📈':kind==='down'?'📉':'➡️';
    const label=kind==='up'?'성장기':kind==='down'?'하락세':'정체기';
    const sign=change>=0?'+':'';
    return {kind,text:`${emoji} ${label}`,detail:`최근 ${n}회 ${sign}${change.toFixed(1)}cm`};
  }
  function recordChart(points){
    if(!points||!points.length) return '<p class="empty">아직 비교할 기록이 없습니다.</p>';
    const rows=[...points].sort((a,b)=>a.date.localeCompare(b.date)||(a.attempt_no-b.attempt_no));
    const values=rows.map(p=>p.value);
    const w=Math.max(260,Math.min(760,document.documentElement.clientWidth-80)),h=240,left=55,right=58,top=25,bottom=45;
    let low=Math.min(...values),high=Math.max(...values);
    const mean=values.reduce((a,b)=>a+b,0)/values.length;
    let maxIdx=0,minIdx=0;
    values.forEach((v,i)=>{if(v>values[maxIdx])maxIdx=i;if(v<values[minIdx])minIdx=i;});
    const margin=Math.max((high-low)*.2,1);low-=margin;high+=margin;
    const span=high-low||1;
    const x=i=>left+(rows.length<2?(w-left-right)/2:i*(w-left-right)/(rows.length-1));
    const y=v=>h-bottom-(v-low)/span*(h-top-bottom);
    const groups=[...new Set(rows.map(p=>p.group))];
    let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="시간순 실측 기록 그래프">`;
    for(let i=0;i<4;i++){const v=low+span*i/3;svg+=`<line class="chart-grid" x1="${left}" x2="${w-right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-label" x="${left-8}" y="${y(v)+4}" text-anchor="end">${v.toFixed(1)}</text>`;}
    for(const ref of [{v:mean,cls:'chart-ref-mean',tag:'평균'},{v:values[maxIdx],cls:'chart-ref-max',tag:'최고'},{v:values[minIdx],cls:'chart-ref-min',tag:'최저'}])
      svg+=`<line class="chart-ref ${ref.cls}" x1="${left}" x2="${w-right}" y1="${y(ref.v)}" y2="${y(ref.v)}"/><text class="chart-ref-label ${ref.cls}" x="${w-right+4}" y="${y(ref.v)+4}" text-anchor="start">${ref.tag} ${ref.v.toFixed(1)}</text>`;
    groups.forEach(g=>{
      const color=RECORD_GROUP_COLORS[groups.indexOf(g)%RECORD_GROUP_COLORS.length];
      const pts=rows.map((p,i)=>({p,i})).filter(o=>o.p.group===g);
      if(pts.length>1)svg+=`<polyline class="chart-line" style="stroke:${color}" points="${pts.map(o=>`${x(o.i)},${y(o.p.value)}`).join(' ')}"/>`;
    });
    rows.forEach((p,i)=>{
      const color=RECORD_GROUP_COLORS[groups.indexOf(p.group)%RECORD_GROUP_COLORS.length];
      svg+=`<circle class="chart-point" style="fill:${color}" cx="${x(i)}" cy="${y(p.value)}" r="4"><title>${esc(p.date)} · ${p.attempt_no}번째 · ${p.value.toFixed(1)}cm</title></circle>`;
    });
    const labels=w<400?4:8;
    rows.forEach((p,i)=>{if(rows.length<=labels||i%Math.ceil(rows.length/labels)===0)svg+=`<text class="chart-label" x="${x(i)}" y="${h-14}" text-anchor="middle">${p.date.slice(5)}</text>`;});
    svg+='</svg>';
    const trend=recordTrend(values);
    const badge=`<span class="trend-badge trend-${trend.kind}">${esc(trend.text)}${trend.detail?` <small>${esc(trend.detail)}</small>`:''}</span>`;
    const summary=`<div class="record-summary">`+
      `<div>평균<strong>${mean.toFixed(1)} cm</strong></div>`+
      `<div>최고<strong>${values[maxIdx].toFixed(1)} cm</strong><small>${esc(rows[maxIdx].date)}</small></div>`+
      `<div>최저<strong>${values[minIdx].toFixed(1)} cm</strong><small>${esc(rows[minIdx].date)}</small></div>`+
      `<div>기록 수<strong>${rows.length}개</strong></div></div>`;
    const legend=groups.length>1?`<div class="chart-legend">`+groups.map(g=>`<span><i style="background:${RECORD_GROUP_COLORS[groups.indexOf(g)%RECORD_GROUP_COLORS.length]}"></i>${esc(g||'기본')}</span>`).join('')+`</div>`:'';
    return `<div class="record-head">${badge}</div>`+summary+legend+svg;
  }
  function chart(points,unit,showBest=false){
    if(!points.length) return '<p class="empty">아직 비교할 기록이 없습니다.</p>';
    const dates=[...new Set(points.map(p=>p.date))].sort(), w=Math.max(260,Math.min(760,document.documentElement.clientWidth-80)),h=240,left=55,right=15,top=25,bottom=45;
    let low=Math.min(...points.map(p=>p.median)),high=Math.max(...points.map(p=>showBest?p.best:p.median));
    const margin=Math.max((high-low)*.2,unit==='cm'?1:unit==='s'?.01:1); low-=margin;high+=margin;
    const x=d=>left+(dates.indexOf(d)+.5)*(w-left-right)/dates.length;
    const y=v=>h-bottom-(v-low)/(high-low)*(h-top-bottom);
    let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="날짜별 변화 그래프">`;
    for(let i=0;i<4;i++){const v=low+(high-low)*i/3;svg+=`<line class="chart-grid" x1="${left}" x2="${w-right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-label" x="${left-8}" y="${y(v)+4}" text-anchor="end">${v.toFixed(unit==='s'?2:1)}</text>`;}
    for(const group of new Set(points.map(p=>p.group))){const rows=points.filter(p=>p.group===group).sort((a,b)=>a.date.localeCompare(b.date));svg+=`<polyline class="chart-line" points="${rows.map(p=>`${x(p.date)},${y(p.median)}`).join(' ')}"/>`;}
    for(const p of points) svg+=(showBest?`<circle class="chart-best" cx="${x(p.date)}" cy="${y(p.best)}" r="3"><title>${esc(p.date)} 최고 ${p.best.toFixed(2)}${unit}</title></circle>`:'')+`<circle class="chart-point" cx="${x(p.date)}" cy="${y(p.median)}" r="5"><title>${esc(p.date)} 중앙값 ${p.median.toFixed(2)}${unit} · ${p.count}회</title></circle>`;
    const labels=w<400?4:8;
    dates.forEach((d,i)=>{if(dates.length<=labels||i%Math.ceil(dates.length/labels)===0)svg+=`<text class="chart-label" x="${x(d)}" y="${h-14}" text-anchor="middle">${d.slice(5)}</text>`;});
    return svg+'</svg>';
  }
  async function loadHistory(){
    const owner=person(),request=++historyRequest;
    if(!owner){$('historySummary').innerHTML='<p class="empty">이름을 저장하면 이곳에 기록이 쌓입니다.</p>';return;}
    const h=(await api(`/api/athletes/${owner}/history`)).history;if(owner!==person()||request!==historyRequest)return;history=h;
    const best=h.record_points.length?Math.max(...h.record_points.map(p=>p.value))+' cm':'—';
    $('historySummary').innerHTML=`<div class="summary-grid"><div>최고 실측 기록<strong>${best}</strong></div><div>저장된 시도<strong>${h.counts.attempts}회</strong></div><div>기록 있는 훈련일<strong>${h.counts.days}일</strong></div></div>`;
    $('recordChart').innerHTML=recordChart(h.record_points)+h.record_summaries.map(s=>`<p class="insight">${esc(s.text)}</p>`).join('');
    const old=$('metricSelect').value;
    const available=Object.entries(h.metrics).filter(([k,m])=>m.points.length);
    $('metricSelect').innerHTML=available.length?available.map(([k,m])=>`<option value="${k}">${esc(m.label)}</option>`).join(''):'<option value="">비교 가능한 지표 없음</option>';
    if(available.some(([k])=>k===old))$('metricSelect').value=old;
    metricChart();
    const session=h.sessions.find(s=>s.performed_on===$('trainingDate').value);
    const draft=memoDrafts.get(memoKey());
    $('focus').value=draft?.focus??session?.focus??'';$('practiceNote').value=draft?.practice_note??session?.practice_note??'';
    const validIds=new Set(h.attempts.map(a=>a.id));selected=new Set([...selected].filter(id=>validIds.has(id)));
    $('attemptList').innerHTML=h.attempts.length?h.attempts.map(a=>`<div class="history-row saved-attempt"><input type="checkbox" data-select="${a.id}" ${selected.has(a.id)?'checked':''} aria-label="${esc(a.performed_on)} ${a.attempt_no}번 비교 선택"><button data-open="${a.id}">${esc(a.performed_on)} · ${a.attempt_no}번째 점프<small>${esc(a.latest_run?stateNames[a.latest_run.status]:a.source_path?'영상 저장됨':'기록만 저장')}${analysisTime(a.latest_run)}${a.analysis_stale?' · 이전 분석':''}</small></button><strong>${recordText(a)}</strong><button class="danger" data-delete="${a.id}" aria-label="${esc(a.performed_on)} ${a.attempt_no}번째 점프 삭제">삭제</button></div>`).join(''):'<p class="empty">첫 점프의 영상이나 실측 기록을 저장해 보세요.</p>';
    $('compareSaved').disabled=selected.size<2||selected.size>5;
    h.attempts.forEach(a=>{if(a.latest_run&&['queued','running'].includes(a.latest_run.status))watch(a.latest_run.id,owner,a.id);});
  }
  function metricChart(){
    const m=history?.metrics[$('metricSelect').value];
    if(!m){$('metricChart').innerHTML='<p class="empty">저장된 분석에서 점프 장면을 확인하면 비교 가능한 지표가 표시됩니다.</p>';return;}
    const unit=m.unit==='m'?'cm':m.unit, factor=m.unit==='m'?100:1;
    $('metricChart').innerHTML=chart(m.dates.map(p=>({...p,median:p.median*factor,best:p.best*factor})),unit)+m.summaries.map(s=>`<p class="insight">${esc(s.text)}</p>`).join('')+
      `<details class="data"><summary>포함된 자료 ${m.points.length}개</summary><p class="hint">같은 촬영·분석 조건끼리 연결했습니다. 제외된 분석 ${m.excluded}개.</p></details>`;
  }
  $('metricSelect').addEventListener('change',metricChart);
  window.addEventListener('resize',()=>{if(history){$('recordChart').innerHTML=recordChart(history.record_points)+history.record_summaries.map(s=>`<p class="insight">${esc(s.text)}</p>`).join('');metricChart();}});
  $('attemptList').addEventListener('change',e=>{const id=e.target.dataset.select;if(id){e.target.checked?selected.add(id):selected.delete(id);$('compareSaved').disabled=selected.size<2||selected.size>5;}});
  $('attemptList').addEventListener('click',async e=>{
    const remove=e.target.closest('[data-delete]');
    if(remove){if(busy||remove.disabled)return;remove.disabled=true;
      try{const a=history.attempts.find(a=>a.id===remove.dataset.delete);if(a)await deleteSaved(a);}
      catch(error){console.error(error);notice(error.message,true);}finally{remove.disabled=false;}return;}
    const id=e.target.closest('[data-open]')?.dataset.open;if(id)openAttempt(id).catch(e=>notice(e.message,true));
  });
  async function deleteSaved(a){
    if(!confirm(`${a.name} · ${a.performed_on} · ${a.attempt_no}번째 점프를 삭제할까요?\n목록과 누적 분석에서 제외되며, 휴지통에서 복원할 수 있습니다.`))return false;
    const owner=person();detailRequest++;historyRequest++;invalidateFeedback();
    const fresh=(await api(`/api/attempts/${a.id}`)).attempt;
    await api(`/api/attempts/${a.id}`,{method:'DELETE',body:{expected_revision:fresh.revision}});
    if(person()===owner){
      detailRequest++;historyRequest++;selected.delete(a.id);result.classList.add('hidden');result.innerHTML='';
      files=files.filter(f=>{if(f.attempt?.id!==a.id)return true;URL.revokeObjectURL(f.url);return false;});renderUploadCards();updateGo();
      if(detail?.id===a.id){detail=null;$('detail').classList.add('hidden');$('detail').innerHTML='';}
      if(location.hash==='#attempt='+a.id)location.hash='';
      await loadHistory();await refreshFeedback();notice('삭제했습니다. 누적 분석에서 제외되며 휴지통에서 복원할 수 있습니다.');
    }
    return true;
  }
  function invalidateFeedback(){
    feedbackRequest++;
    const box=$('personalFeedback');if(box)box.innerHTML='';
    if(detail)detail.personal_feedback=null;
  }
  async function refreshFeedback(){
    const id=detail?.id,owner=person(),request=detailRequest;
    if(!id)return;
    invalidateFeedback();
    const feedback=feedbackRequest;
    const a=(await api(`/api/attempts/${id}`)).attempt;
    if(feedback!==feedbackRequest||owner!==person()||request!==detailRequest||detail?.id!==id||a.deleted_at)return;
    detail.personal_feedback=a.personal_feedback;
    const box=$('personalFeedback');if(box)box.innerHTML=coachingSummary(a.analysis_stale?null:a.selected_run?.result,a.personal_feedback)+personalFeedback(a.personal_feedback);
  }
  for(const id of ['focus','practiceNote'])$(id).addEventListener('input',()=>memoDrafts.set(memoKey(),{focus:$('focus').value,practice_note:$('practiceNote').value}));
  action('savePractice',async()=>{requirePerson();const key=memoKey(),draft=memoDrafts.get(key);await api(`/api/athletes/${person()}/session`,{method:'POST',body:{performed_on:$('trainingDate').value,focus:$('focus').value,practice_note:$('practiceNote').value}});if(memoDrafts.get(key)===draft)memoDrafts.delete(key);notice('연습 메모를 저장했습니다.');});

  async function openAttempt(id,scroll=true,followOwner=false){
    const owner=person(),request=++detailRequest;
    const a=(await api(`/api/attempts/${id}`)).attempt;
    if(request!==detailRequest||person()!==owner)return;
    if(a.deleted_at)throw new Error('휴지통에 있거나 정정된 시도입니다.');
    if(person()!==a.athlete_id){if(!followOwner)return;$('person').value=a.athlete_id;localStorage.setItem('jumpcoach.person',a.athlete_id);selected.clear();clearFiles();await loadHistory();}
    if(request!==detailRequest||person()!==a.athlete_id)return;
    detail=a;detailDirty=false;calibration=a.analysis_config.calibration||null;location.hash='attempt='+id;
    const run=a.selected_run, data=run?.result, latest=a.latest_run;
    $('detail').classList.remove('hidden');result.classList.add('hidden');
    const config=a.analysis_config;
    $('detail').innerHTML=`<div class="topbar"><h2>${esc(a.name)} · ${esc(a.performed_on)} · ${a.attempt_no}번째</h2><span id="runStatus" class="badge">${esc(latest?stateNames[latest.status]:'기록 저장됨')}</span><button id="deleteAttempt" class="danger">이 시도 삭제</button></div>
      <div class="detail-actions"><input id="editRecord" type="number" step="0.1" min="0.1" required aria-label="실측 기록 수정" placeholder="실측 기록 (cm)" value="${a.measured_distance_cm??''}"${a.record_unknown?' disabled':''}><label class="check record-unknown"><input id="editUnknown" type="checkbox"${a.record_unknown?' checked':''}>알 수 없습니다</label><select id="editValidity"><option value="valid"${a.validity==='valid'?' selected':''}>유효</option><option value="foul"${a.validity==='foul'?' selected':''}>파울</option><option value="unknown"${a.validity==='unknown'?' selected':''}>미확인</option></select><button id="editSave" class="secondary">기록 수정</button></div>
      ${a.source_url?`<details class="data"${data?'':' open'}><summary>원본 영상</summary><video class="source-video" controls muted src="${a.source_url}"></video><img class="source-thumb" src="/api/attempts/${a.id}/frame?index=0" alt="원본 영상 첫 장면"></details>`:''}
      ${latest?.error_message?`<p class="err">${esc(latest.error_message)}</p>`:''}
      ${data?`${coreMetrics(data.metrics)}${a.analysis_stale?'<p class="hint">이전 분석입니다. 현재 설정으로 다시 분석하세요.</p>':!a.clip_confirmed?'<div class="row"><button id="confirmClip">이 점프가 맞습니다 · 이력에 반영</button></div>':''}<div id="personalFeedback">${coachingSummary(a.analysis_stale?null:data,a.personal_feedback)}${personalFeedback(a.personal_feedback)}</div>${phaseDetails(data.shots,data.metrics)}${metricDetails(data,a)}${media(data,'savedVideo',data.metrics)}`:''}
      ${a.source_path?`<details class="data"><summary>분석 설정·기준 길이</summary><div class="field-grid"><label>촬영 위치·방식<input id="editCamera" value="${esc(config.camera_setup||a.capture_context.setup)}" maxlength="100"></label><label>영상 속도<select id="editTime"><option value="normal">원래 속도</option><option value="slow">일정한 슬로모션</option><option value="unknown">모르겠음</option></select></label><label>슬로모션 배율<input id="editSlow" type="number" min="1" max="32" value="${config.slowdown||4}"></label></div>
      <details class="data"><summary>기준 길이 지정${calibration?' · 저장됨':''}</summary><p class="hint">점프와 같은 평면의 알려진 길이를 선택하세요. 기준의 양 끝을 누른 뒤 실제 cm를 입력하세요.</p><div class="identity-row"><label>프레임</label><input id="calFrame" type="number" min="0" value="${calibration?.frame||0}"><button id="loadFrame" class="secondary">장면 열기</button></div><canvas id="calCanvas" class="calibration" width="760" height="428"></canvas><div class="identity-row"><input id="calLength" type="number" min="0.1" step="0.1" value="${calibration?.length_cm||''}" placeholder="선택한 길이 (cm)"><button id="saveCalibration" class="secondary">두 점 적용</button><button id="clearCalibration" class="ghost">기준 제거</button></div><p id="calStatus" class="hint">${calibration?'저장된 기준을 사용합니다.':'기준이 없어도 가능한 지표는 분석합니다.'}</p></details></details>
      <div class="row"><button id="rerun" class="secondary"${latest&&['queued','running'].includes(latest.status)?' disabled':''}>다시 분석</button></div>`:''}
      <details class="data"${a.source_path?'':' open'}><summary>${a.source_path?'영상 잘못 올림':'영상 추가'}</summary><input id="replacementFile" type="file" accept="video/*"><button id="attachVideo" class="secondary">${a.source_path?'영상 정정':'영상 저장'}</button></details>
      <details class="data"><summary>더보기</summary><label class="check"><input id="includeAnalysis" type="checkbox"${a.analysis_included?' checked':''}>이 분석을 동작 변화에 포함</label><div class="field-grid"><label>이름 변경<select id="movePerson">${$('person').innerHTML}</select></label><label>훈련일 수정<input id="moveDate" type="date" value="${a.performed_on}"></label></div><div class="row"><button id="moveAttempt" class="secondary">소속·날짜 수정</button></div></details>`;
    $('detail').addEventListener('input',()=>{detailDirty=true;},{once:true});
    $('movePerson').value=a.athlete_id;
    $('editUnknown').addEventListener('change',()=>{const unknown=$('editUnknown').checked;$('editRecord').disabled=unknown;if(unknown)$('editRecord').value='';});
    async function saveDetailRecord(){
      const input=$('editRecord'),unknown=$('editUnknown').checked;
      if(!unknown&&(!input.value||!input.validity.valid))throw new Error('실측 기록을 입력하거나 알 수 없습니다를 선택하세요.');
      const record=unknown?'':input.value,validity=$('editValidity').value;
      if(record===(detail.measured_distance_cm==null?'':String(detail.measured_distance_cm))&&unknown===detail.record_unknown&&validity===detail.validity)return;
      detail=(await api(`/api/attempts/${id}`,{method:'PATCH',body:{expected_revision:detail.revision,measured_distance_cm:record,record_unknown:unknown,validity,commit:true}})).attempt;
    }
    action('editSave',async()=>{await saveDetailRecord();await openAttempt(id,false);await loadHistory();notice('실측 기록을 수정했습니다.');});
    if($('confirmClip'))action('confirmClip',async()=>{await api(`/api/attempts/${id}/clip-confirmation`,{method:'POST',body:{run_id:run.id,expected_revision:detail.revision}});await openAttempt(id,false);await loadHistory();notice('확인한 점프를 이력에 반영했습니다.');});
    $('includeAnalysis').addEventListener('change',async e=>{try{invalidateFeedback();const owner=person(),request=detailRequest;const updated=(await api(`/api/attempts/${id}`,{method:'PATCH',body:{expected_revision:detail.revision,analysis_included:e.target.checked}})).attempt;if(owner!==person()||request!==detailRequest||detail?.id!==id)return;detail=updated;await loadHistory();await refreshFeedback();}catch(err){console.error(err);notice(err.message,true);}});
    action('moveAttempt',async()=>{await api(`/api/attempts/${id}`,{method:'PATCH',body:{expected_revision:detail.revision,athlete_id:$('movePerson').value,performed_on:$('moveDate').value}});await openAttempt(id,false,true);await loadHistory();notice('기록의 이름·날짜를 수정했습니다.');});
    const correctionKey=crypto.randomUUID();
    action('attachVideo',async()=>{
      const file=$('replacementFile').files[0];if(!file)throw new Error('저장할 영상을 선택하세요.');
      if(file.size>300*1024*1024)throw new Error('300MB 이하의 영상을 선택하세요.');
      let target=detail;
      const form=new FormData();form.append('video',file);form.append('expected_revision',target.revision);
      if(a.source_path){if(!confirm('실측 기록은 유지하고 이 시도의 영상을 정정할까요? 기존 영상과 분석은 휴지통에 보관됩니다.'))return;
        form.append('request_key',correctionKey);
        target=(await api(`/api/attempts/${id}/correction`,{method:'POST',body:form})).attempt;
      }else{await api(`/api/attempts/${target.id}/source`,{method:'PUT',body:form});}
      await openAttempt(target.id,false);await loadHistory();notice('영상을 저장했습니다. 분석 설정을 확인하고 다시 분석을 누르세요.');
    });
    action('deleteAttempt',async()=>{if(busy)throw new Error('영상 저장이 끝난 뒤 삭제하세요.');await deleteSaved(a);});
    if(a.source_path){
      $('editTime').value=config.time_mode||'normal';
      setupCalibration(a);
      action('rerun',async()=>{const c={time_mode:$('editTime').value,slowdown:Number($('editSlow').value),camera_setup:$('editCamera').value.trim(),calibration};
        await saveDetailRecord();
        const r=(await api(`/api/attempts/${id}/analysis`,{method:'POST',body:{request_key:crypto.randomUUID(),expected_analysis_input_revision:detail.analysis_input_revision,config:c}})).run;
        await openAttempt(id,false);watch(r.id,a.athlete_id,id);notice('같은 시도를 다시 분석합니다.');});
    }
    if(latest&&['queued','running'].includes(latest.status))watch(latest.id,a.athlete_id,id);
    if(scroll)$('detail').scrollIntoView({behavior:'smooth',block:'start'});
  }
  function metricDetails(data,a){
    const items=Object.values(data.observations||{});
    if(!items.length)return '';
    return '<details class="data"><summary>누적 비교 기준</summary><table><tr><th>지표</th><th>값</th><th>이력 반영</th></tr>'+items.map(m=>{
      const reason=!m.eligible?(m.reason_codes||[]).join(' · '):a.analysis_stale?'설정 변경':!a.clip_confirmed?'점프 확인 전':!a.analysis_included?'수동 제외':a.validity!=='valid'?'파울·미확인':'포함';
      const value=(m.displayable??m.eligible)&&Number.isFinite(m.value)?(!m.eligible?'약 ':'')+(m.value*(m.unit==='m'?100:1)).toFixed(2)+' '+(m.unit==='m'?'cm':m.unit):'—';
      return `<tr><td>${esc(m.label)}</td><td>${esc(value)}</td><td>${esc(reason)}</td></tr>`;
    }).join('')+'</table></details>';
  }
  function setupCalibration(a){
    const canvas=$('calCanvas'), ctx=canvas.getContext('2d');let image=null,points=[],frame=0;
    function draw(){if(!image)return;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);ctx.strokeStyle='#39d98a';ctx.fillStyle='#39d98a';ctx.lineWidth=3;
      const converted=points.map(p=>[p[0]*canvas.width/image.naturalWidth,p[1]*canvas.height/image.naturalHeight]);
      converted.forEach(([x,y])=>{ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();});if(converted.length===2){ctx.beginPath();ctx.moveTo(...converted[0]);ctx.lineTo(...converted[1]);ctx.stroke();}}
    async function load(){frame=Number($('calFrame').value);points=[];const img=new Image();await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(new Error('해당 프레임을 열지 못했습니다.'));img.src=`/api/attempts/${a.id}/frame?index=${frame}`;});image=img;canvas.width=Math.min(900,img.naturalWidth);canvas.height=Math.round(canvas.width*img.naturalHeight/img.naturalWidth);draw();}
    action('loadFrame',load);
    canvas.addEventListener('click',e=>{if(!image)return;const box=canvas.getBoundingClientRect();if(points.length===2)points=[];points.push([(e.clientX-box.left)/box.width*image.naturalWidth,(e.clientY-box.top)/box.height*image.naturalHeight]);draw();$('calStatus').textContent=`기준점 ${points.length}/2개 선택`;});
    action('saveCalibration',()=>{const length=Number($('calLength').value);if(points.length!==2||!Number.isFinite(length)||length<=0)throw new Error('두 점과 실제 길이를 입력하세요.');calibration={points,frame,length_cm:length};$('calStatus').textContent='기준을 적용했습니다. 다시 분석을 누르면 저장됩니다.';});
    action('clearCalibration',()=>{calibration=null;points=[];draw();$('calStatus').textContent='기준을 제거했습니다. 다시 분석하면 반영됩니다.';});
  }
  action('compareSaved',async()=>{
    requirePerson();const owner=person(),request=++detailRequest;
    const data=await api(`/api/athletes/${owner}/comparison`,{method:'POST',body:{attempt_ids:[...selected]}});
    if(person()!==owner||request!==detailRequest)return;
    const attempts=data.attempts;result.classList.remove('hidden');$('detail').classList.add('hidden');detail=null;
    let html='<h2>저장된 점프 비교</h2><p class="hint">실측 기록과 같은 순간의 사진을 비교하세요.</p>';
    html+='<div class="compare-grid">'+attempts.map(a=>`<div><h3>${esc(a.performed_on)} · ${a.attempt_no}번째 · ${recordText(a)}</h3>${coreMetrics(a.selected_run?.result?.metrics)}${coachingSummary(a.analysis_stale?null:a.selected_run?.result,a.personal_feedback)}${personalFeedback(a.personal_feedback)}</div>`).join('')+'</div>';
    const historyUsable=(a,k)=>a.selected_run?.result?.observations?.[k]?.eligible&&!a.analysis_stale&&a.clip_confirmed&&a.analysis_included&&a.validity==='valid';
    const keys=Object.keys(history?.metrics||{}).filter(k=>attempts.some(a=>historyUsable(a,k)));
    const matching=Object.fromEntries(keys.map(k=>[k,new Set(attempts.map(a=>a.selected_run?.result?.observations?.[k]?.comparison_group).filter(Boolean)).size<=1]));
    if(keys.length)html+='<details class="data"><summary>수치 비교</summary><div style="overflow-x:auto"><table><tr><th>점프</th>'+keys.map(k=>`<th>${esc(history.metrics[k].label)}</th>`).join('')+'</tr>'+attempts.map(a=>`<tr><td>${esc(a.performed_on)} · ${a.attempt_no}</td>`+keys.map(k=>{
      const m=a.selected_run?.result?.observations?.[k],ok=historyUsable(a,k);
      return `<td>${!matching[k]?'조건 다름':ok?esc((m.value*(m.unit==='m'?100:1)).toFixed(2))+' '+esc(m.unit==='m'?'cm':m.unit):'—'}</td>`;
    }).join('')+'</tr>').join('')+'</table></div></details>';
    for(const key of ['load','drive','takeoff','mid','landing'])html+='<div class="compare-grid">'+attempts.map(a=>{const d=a.selected_run?.result,s=d?.shots.find(s=>s.key===key);return `<div><h3>${esc(a.performed_on)} · ${a.attempt_no}번째 · ${recordText(a)}</h3>${s?shotCard(s,d.metrics):'<p class="empty">분석 장면 없음</p>'}</div>`;}).join('')+'</div>';
    result.innerHTML=html;result.scrollIntoView({behavior:'smooth'});
  });
  action('backup',async()=>{notice('기록과 영상을 백업하고 있습니다.');const data=await api('/api/backups',{method:'POST'});$('management').innerHTML=`<p><a href="/api/backups/${esc(data.backup.filename)}" download>백업 파일 내려받기</a> · ${data.backup.files}개 파일</p>`;notice('백업 파일을 만들었습니다.');});
  action('trash',async()=>{requirePerson();const owner=person(),request=++managementRequest;const data=await api(`/api/athletes/${owner}/history?deleted=1`);if(person()!==owner||request!==managementRequest)return;$('management').innerHTML=data.attempts.length?data.attempts.map(a=>`<div class="history-row"><span></span><span>${esc(a.performed_on)} · ${recordText(a)}</span>${a.superseded_by_attempt_id?'<span class="hint">영상 정정됨</span>':`<button data-restore="${a.id}" data-revision="${a.revision}">복원</button>`}</div>`).join(''):'<p class="empty">휴지통이 비어 있습니다.</p>';});
  $('management').addEventListener('click',async e=>{const b=e.target.closest('[data-restore]');if(!b)return;try{await api(`/api/attempts/${b.dataset.restore}/restore`,{method:'POST',body:{expected_revision:Number(b.dataset.revision)}});b.closest('.history-row').remove();await loadHistory();notice('기록을 복원했습니다.');}catch(err){notice(err.message,true);}});
  action('quit',async()=>{if(!confirm('분석기를 종료할까요? 진행 중인 분석은 중단됩니다.'))return;await api('/shutdown',{method:'POST'});notice('분석기를 종료했습니다. 다시 사용하려면 JumpCoach 실행 아이콘을 누르세요.');});
  window.personalReset=()=>{clearFiles();$('detail').classList.add('hidden');result.classList.add('hidden');detail=null;location.hash='';tab('record');window.scrollTo({top:0,behavior:'smooth'});};
  (async()=>{await people();await loadHistory();const id=location.hash.match(/^#attempt=([a-f0-9]{32})$/)?.[1];if(id)await openAttempt(id,false,true);})().catch(e=>notice(e.message,true));
})();
