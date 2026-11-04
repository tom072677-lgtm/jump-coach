const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=process.argv[2] || 'C:/Users/tom07/dev/jumpcoach';
const src=fs.readFileSync(path.join(root,'static/personal_history.js'),'utf8');
const part=src.slice(src.indexOf("action('compareSaved'"),src.indexOf("action('backup'"));
async function check(mode) {
  let handler,owner='A',respond;
  const pending=new Promise(resolve=>respond=resolve);
  const classes=new Set(['hidden']);
  const result={innerHTML:'',classList:{remove:x=>classes.delete(x),add:x=>classes.add(x)},scrollIntoView(){}};
  const box={classList:{add(){}}};
  const ctx={action:(_,fn)=>{handler=fn},requirePerson(){},api:()=>pending,person:()=>owner,selected:new Set(['one','two']),history:{metrics:{}},result,$:()=>box,esc:x=>String(x),recordText:a=>a.name,shotCard:()=>'',detail:null,detailRequest:0};
  vm.runInNewContext(part,ctx);
  const done=handler();
  if(mode==='owner'){owner='B';ctx.detailRequest++;classes.add('hidden');}
  if(mode==='request'){ctx.detailRequest++;classes.add('hidden');}
  respond({attempts:[{name:'A record',performed_on:'2026-11-04',attempt_no:1},{name:'A record',performed_on:'2026-11-04',attempt_no:2}]});
  await done;
  const visible=!classes.has('hidden'), showsA=result.innerHTML.includes('A record');
  assert.equal(visible,mode==='normal');
  assert.equal(showsA,mode==='normal');
  console.log(JSON.stringify({mode,selectedPerson:owner,comparisonVisible:visible,showsPreviousPerson:showsA}));
}
(async()=>{for(const mode of ['normal','owner','request'])await check(mode)})().catch(e=>{console.error(e);process.exitCode=1});
