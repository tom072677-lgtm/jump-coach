const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=process.argv[2]||'C:/Users/tom07/dev/jumpcoach';
const src=fs.readFileSync(path.join(root,'static/personal_history.js'),'utf8');
const part=src.slice(src.indexOf('  async function loadHistory()'),src.indexOf('  function metricChart()'));
const nodes=new Map(),responses=[];
const ctx={person:()=> 'person',historyRequest:0,history:null,selected:new Set(['deleted']),
  $:id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',disabled:false});return nodes.get(id);},
  api:()=>new Promise(resolve=>responses.push(resolve)),chart:()=>'',metricChart(){},
  memoDrafts:new Map(),memoKey:()=>'',esc:String,recordText:()=> '240 cm',stateNames:{},watch(){}};
vm.runInNewContext(part,ctx);
const oldHistory={counts:{attempts:1,days:1},record_points:[{value:240}],record_dates:[],record_summaries:[],
  metrics:{},sessions:[],attempts:[{id:'deleted',performed_on:'2026-11-04',attempt_no:1}]};
const currentHistory={...oldHistory,counts:{attempts:0,days:0},record_points:[],attempts:[]};
(async()=>{
  const oldRequest=ctx.loadHistory();
  ctx.historyRequest++; // Deletion invalidates requests already in flight.
  const newRequest=ctx.loadHistory();
  responses[1]({history:currentHistory});await newRequest;
  responses[0]({history:oldHistory});await oldRequest;
  assert.equal(ctx.history,currentHistory);
  assert.equal(nodes.get('attemptList').innerHTML.includes('data-open="deleted"'),false);
  assert.equal(ctx.selected.size,0);
  assert.equal(nodes.get('compareSaved').disabled,true);
  console.log('삭제 전 늦은 응답이 목록·통계·비교 선택을 복원하지 않음');
})().catch(error=>{console.error(error);process.exitCode=1;});
