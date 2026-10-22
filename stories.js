'use strict';
/* Windroida stories (24 h): the strip above the chat list, the full-screen viewer and the composer.
   Safe DOM building only (no innerHTML with user data). Loaded after app.js; talks to the server with call() / XHR. */
(function(){
IC.plus="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z";
IC.eye="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z";
IC.heart="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z";
IC.story="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm1-13h-2v4H7v2h4v4h2v-4h4v-2h-4V7z";

const BGS=['linear-gradient(135deg,#667eea,#764ba2)','linear-gradient(135deg,#f093fb,#f5576c)','linear-gradient(135deg,#4facfe,#00c6fb)',
  'linear-gradient(135deg,#43e97b,#38a3a5)','linear-gradient(135deg,#fa709a,#fee140)','linear-gradient(135deg,#30cfd0,#330867)',
  'linear-gradient(135deg,#ff9a44,#fc6076)','linear-gradient(135deg,#232526,#414345)'];
const QUICK=['❤️','🔥','👍','😂','😮','😢','👏'];
const ST={groups:[],t:null,seen:new Set(),v:null};
const rtl=()=>getComputedStyle(document.documentElement).direction==='rtl';
const mediaUrl=s=>`/story/${s.id}?k=${encodeURIComponent(S.mediaKey||'')}`;
const ago=ts=>{const m=Math.max(0,Math.floor((Date.now()/1000-ts)/60));return m<1?'همین الان':m<60?fa(m)+' دقیقه پیش':fa(Math.floor(m/60))+' ساعت پیش'};
const left=ts=>{const m=Math.max(0,Math.floor((ts-Date.now()/1000)/60));return m<60?fa(m)+' دقیقه':fa(Math.ceil(m/60))+' ساعت'};

/* ------------------------------------------------------------ feed + strip */
async function load(){
  if(!S.me||!S.mediaKey)return;
  try{const r=await call('stories_feed');ST.groups=r.groups||[];renderStrip();const v=ST.v;
    if(v){const g=ST.groups.find(x=>x.user.id===v.uid);if(!g)closeViewer();else{v.g=g;if(v.i>=g.stories.length)v.i=g.stories.length-1}}}catch{}}
function loadSoon(){clearTimeout(ST.t);ST.t=setTimeout(load,400)}
function renderStrip(){
  const box=$('#stories');if(!box)return;
  const mine=ST.groups.find(g=>g.user.id===S.me.id),others=ST.groups.filter(g=>g.user.id!==S.me.id);
  const item=(g,isMe)=>{
    const u=isMe?S.me:g.user,has=!!g,seen=has&&g.all_seen;
    const ring=h('div',{class:'st-ring'+(has?(seen?' seen':''):' none')},avatar('u',u.id,u.display_name,u.avatar_at,52,false));
    const it=h('button',{type:'button',class:'st-item',title:isMe?'استوری من':u.display_name,onclick:()=>{if(has)openViewer(g);else composer()}},ring,
      h('span',{class:'st-name'},isMe?'استوری من':short(u.display_name,10)));
    if(isMe)it.append(h('span',{class:'st-plus',onclick:e=>{e.stopPropagation();composer()},title:'استوری جدید'},ic('plus',16)));
    return it};
  box.replaceChildren(item(mine,true),...others.map(g=>item(g,false)));
  box.hidden=false}

/* ------------------------------------------------------------ viewer */
function ensureViewer(){
  let el=$('#storyview');if(el)return el;
  el=h('div',{id:'storyview',hidden:true});document.body.append(el);return el}
function openViewer(g,startId){
  let i=g.stories.findIndex(s=>!s.seen);if(i<0)i=0;
  if(startId){const k=g.stories.findIndex(s=>s.id===startId);if(k>=0)i=k}
  ST.v={g,uid:g.user.id,i,raf:0,t0:0,dur:5000,el:0,paused:false,order:ST.groups.map(x=>x.user.id)};
  ensureViewer().hidden=false;document.body.classList.add('sv-open');show()}
function closeViewer(){
  const v=ST.v;ST.v=null;cancelAnimationFrame(v&&v.raf);const el=$('#storyview');if(el){el.hidden=true;el.replaceChildren()}
  document.body.classList.remove('sv-open');renderStrip()}
function gotoNext(){
  const v=ST.v;if(!v)return;
  if(v.i<v.g.stories.length-1){v.i++;return show()}
  const idx=ST.groups.findIndex(x=>x.user.id===v.uid),nx=ST.groups[idx+1];
  if(nx){v.g=nx;v.uid=nx.user.id;v.i=Math.max(0,nx.stories.findIndex(s=>!s.seen));return show()}
  closeViewer()}
function gotoPrev(){
  const v=ST.v;if(!v)return;
  if(v.el>1500||v.i===0&&ST.groups.findIndex(x=>x.user.id===v.uid)===0){v.el=0;return show()}
  if(v.i>0){v.i--;return show()}
  const idx=ST.groups.findIndex(x=>x.user.id===v.uid),pv=ST.groups[idx-1];if(pv){v.g=pv;v.uid=pv.user.id;v.i=pv.stories.length-1;show()}}
function show(){
  const v=ST.v,root=ensureViewer();if(!v)return;cancelAnimationFrame(v.raf);
  const g=v.g,s=g.stories[v.i],mine=g.user.id===S.me.id;v.s=s;v.el=0;v.paused=false;v.media=null;
  if(!mine&&!s.seen){s.seen=true;call('story_seen',{story_id:s.id}).catch(()=>{});g.all_seen=g.stories.every(x=>x.seen)}
  const bars=h('div',{class:'sv-bars'},g.stories.map((x,k)=>h('i',{class:k<v.i?'done':''},h('b',{}))));
  v.bar=bars.children[v.i]&&bars.children[v.i].firstChild;
  const head=h('div',{class:'sv-head'},avatar('u',g.user.id,g.user.display_name,g.user.avatar_at,38,false),
    h('div',{class:'sv-who'},h('b',{},mine?'استوری من':g.user.display_name),h('span',{},ago(s.created_at)+(mine?' · '+left(s.expires_at)+' مونده':''))),
    h('button',{class:'iconbtn sv-btn',type:'button',title:'بستن',onclick:closeViewer},ic('close')));
  const stage=h('div',{class:'sv-media'+(s.kind==='text'?' text':'')});
  if(s.kind==='text'){stage.style.background=BGS[s.bg%BGS.length];stage.append(h('div',{class:'sv-text',dir:'auto'},s.text));v.dur=Math.min(12000,4000+s.text.length*60)}
  else if(s.kind==='image'){const im=h('img',{alt:'',src:mediaUrl(s),draggable:'false'});stage.append(im);v.dur=5000;v.media=im}
  else{const vd=h('video',{src:mediaUrl(s),playsinline:'',autoplay:'',preload:'auto'});vd.muted=false;stage.append(vd);v.dur=Math.max(1000,(s.dur||10)*1000);v.media=vd;
    vd.onloadedmetadata=()=>{if(isFinite(vd.duration))v.dur=vd.duration*1000};vd.onended=gotoNext;
    vd.play().catch(()=>{vd.muted=true;vd.play().catch(()=>{})})}
  if(s.kind!=='text'&&s.text)stage.append(h('div',{class:'sv-cap',dir:'auto'},s.text));
  const tap=fn=>()=>{if(v.justHeld){v.justHeld=false;return}fn()};
  const prevZ=h('div',{class:'sv-tap '+(rtl()?'r':'l'),onclick:tap(rtl()?gotoPrev:gotoNext)}),
        nextZ=h('div',{class:'sv-tap '+(rtl()?'l':'r'),onclick:tap(rtl()?gotoNext:gotoPrev)});
  [prevZ,nextZ].forEach(z=>{
    z.addEventListener('pointerdown',()=>{clearTimeout(v.hold);v.hold=setTimeout(()=>{v.hold=0;v.justHeld=true;pause(true)},220)});
    z.addEventListener('pointerup',()=>{if(v.hold){clearTimeout(v.hold);v.hold=0}else if(v.justHeld&&!v.inputFocus)pause(false)});
    z.addEventListener('pointercancel',()=>{clearTimeout(v.hold);v.hold=0;if(v.justHeld){v.justHeld=false;if(!v.inputFocus)pause(false)}})});
  root.replaceChildren(h('div',{class:'sv-stage'},stage,prevZ,nextZ,bars,head,footer(g,s,mine)));
  swipeClose(root.firstChild);
  v.t0=performance.now();tick()}
function pause(on){
  const v=ST.v;if(!v||v.paused===on)return;v.paused=on;
  if(v.media&&v.media.tagName==='VIDEO'){on?v.media.pause():v.media.play().catch(()=>{})}
  if(!on)v.t0=performance.now()-v.el}
function tick(){
  const v=ST.v;if(!v)return;
  if(!v.paused){
    const vd=v.media&&v.media.tagName==='VIDEO'?v.media:null;
    v.el=vd&&vd.currentTime?vd.currentTime*1000:performance.now()-v.t0;
    if(v.bar)v.bar.style.width=Math.min(100,v.el/v.dur*100)+'%';
    if(v.el>=v.dur&&!(vd&&!vd.ended&&isFinite(vd.duration)&&vd.currentTime<vd.duration-.2)){return gotoNext()}}
  v.raf=requestAnimationFrame(tick)}
function swipeClose(el){
  let y0=null;el.addEventListener('touchstart',e=>{y0=e.touches[0].clientY},{passive:true});
  el.addEventListener('touchend',e=>{if(y0!=null&&e.changedTouches[0].clientY-y0>110)closeViewer();y0=null},{passive:true})}
function footer(g,s,mine){
  const v=ST.v;
  if(mine){
    return h('div',{class:'sv-foot'},
      h('button',{type:'button',class:'sv-pill',onclick:()=>viewersDialog(s)},ic('eye',18),fa(s.views||0),s.likes?h('span',{class:'sv-likes'},'❤️ '+fa(s.likes)):null),
      h('span',{class:'grow'}),
      h('button',{type:'button',class:'iconbtn sv-btn',title:'حذف استوری',onclick:async()=>{if(!confirm('این استوری پاک بشه؟'))return;pause(true);
        try{await call('story_delete',{story_id:s.id});await load();if(!ST.v)return;const gg=ST.groups.find(x=>x.user.id===S.me.id);if(!gg)closeViewer();else{ST.v.g=gg;ST.v.i=Math.min(ST.v.i,gg.stories.length-1);show()}}catch(e){toast(e.message);pause(false)}}},ic('delete')));
  }
  const inp=h('input',{class:'sv-in',placeholder:'پاسخ به '+short(g.user.display_name,16)+'…',maxlength:'500',autocomplete:'off',dir:'auto'});
  inp.onfocus=()=>{v.inputFocus=true;pause(true)};inp.onblur=()=>{v.inputFocus=false;pause(false)};
  inp.onkeydown=e=>{e.stopPropagation();if(e.key==='Enter'&&inp.value.trim()){send()}};
  const send=async()=>{const text=inp.value.trim();if(!text)return;inp.value='';
    try{const r=await call('story_reply',{story_id:s.id,text});toast('پاسخ ارسال شد',false);inp.blur();if(r.message){if(!S.chats[r.chat_id])await refreshChats();ingest(r.message)}}catch(e){toast(e.message);inp.value=text}};
  const heart=h('button',{type:'button',class:'sv-heart'+(s.my_reaction?' on':''),title:'پسندیدن',onclick:async()=>{
    const on=s.my_reaction!=='❤️';try{await call('story_react',{story_id:s.id,emoji:on?'❤️':''});s.my_reaction=on?'❤️':null;heart.classList.toggle('on',on);if(on)burst(heart)}catch(e){toast(e.message)}}},ic('heart',26));
  return h('div',{class:'sv-foot'},inp,heart,h('button',{type:'button',class:'iconbtn sv-btn',title:'ارسال',onclick:send},ic('send')))}
function burst(el){const r=el.getBoundingClientRect();for(let i=0;i<6;i++){const p=h('span',{class:'sv-fly'},'❤️');p.style.left=(r.left+r.width/2+(Math.random()*40-20))+'px';p.style.top=r.top+'px';p.style.animationDelay=(i*60)+'ms';document.body.append(p);setTimeout(()=>p.remove(),1400)}}
async function viewersDialog(s){
  pause(true);const list=h('div',{class:'ulist',style:'max-height:360px'},h('div',{class:'hint'},'در حال بارگذاری…'));
  const m=dialog('بیننده‌های استوری',list);const old=m.onclick;m.onclick=e=>{old(e);if(e.target===m)pause(false)};
  try{const r=await call('story_viewers',{story_id:s.id});s.views=r.viewers.length;
    list.replaceChildren(...(r.viewers.length?r.viewers.map(x=>h('div',{class:'srow'},avatar('u',x.user.id,x.user.display_name,x.user.avatar_at,40,false),
      h('div',{class:'grow'},h('b',{},x.user.display_name),h('div',{class:'muted',style:'font-size:12px'},ago(x.at))),x.reaction?h('span',{style:'font-size:20px'},x.reaction):null)):[h('div',{class:'hint',style:'position:static'},'هنوز کسی ندیده')]))}
  catch(e){list.replaceChildren(h('div',{class:'hint',style:'position:static'},e.message))}}
document.addEventListener('keydown',e=>{
  if(!ST.v||$('#modal').hidden===false)return;
  if(e.key==='Escape')closeViewer();else if(e.key==='ArrowLeft')(rtl()?gotoNext:gotoPrev)();else if(e.key==='ArrowRight')(rtl()?gotoPrev:gotoNext)();
  else if(e.key===' '&&document.activeElement.tagName!=='INPUT'){e.preventDefault();pause(!ST.v.paused)}});
document.addEventListener('visibilitychange',()=>{if(ST.v&&document.visibilityState!=='visible')pause(true);else if(ST.v&&!ST.v.inputFocus)pause(false)});

/* ------------------------------------------------------------ composer */
function composer(){
  let mode='media',file=null,bg=0,audience='contacts',busy=false;
  const body=h('div',{});
  const aud=h('select',{class:'st-aud',onchange:e=>{audience=e.target.value}},h('option',{value:'contacts'},'فقط مخاطبین من'),h('option',{value:'shared'},'مخاطبین + هم‌گروهی‌ها'));
  const msg=h('div',{class:'msg'}),btn=h('button',{class:'btn',type:'button',onclick:publish},'انتشار');
  const tabs=h('div',{class:'tabs st-tabs'},['media','text'].map(k=>h('button',{type:'button',class:k===mode?'on':'',onclick:()=>{mode=k;paint()}},k==='media'?'عکس / ویدیو':'متنی')));
  const m=dialog('استوری جدید',tabs,body,h('label',{class:'st-lbl'},'چه کسانی ببینن؟'),aud,msg,btn);
  function paint(){
    [...tabs.children].forEach((b,i)=>b.classList.toggle('on',['media','text'][i]===mode));msg.textContent='';
    if(mode==='text'){
      const ta=h('textarea',{rows:'5',maxlength:'300',placeholder:'یه چیزی بنویس…',dir:'auto'}),box=h('div',{class:'st-textbox'},ta);
      const paintBg=()=>{box.style.background=BGS[bg]};paintBg();
      const chips=h('div',{class:'st-chips'},BGS.map((g,i)=>h('button',{type:'button',style:'background:'+g,class:i===bg?'on':'',onclick:e=>{bg=i;paintBg();[...chips.children].forEach((c,k)=>c.classList.toggle('on',k===i))}})));
      body.replaceChildren(box,chips);body.ta=ta;setTimeout(()=>ta.focus(),50)}
    else{
      const pick=h('input',{type:'file',accept:'image/*,video/mp4,video/webm',hidden:true,onchange:e=>choose(e.target.files[0])});
      const pv=h('div',{class:'st-pv',onclick:()=>pick.click()},h('div',{class:'st-pick'},ic('image',34),h('span',{},'یه عکس یا ویدیو (حداکثر ۳۰ ثانیه) انتخاب کن')));
      const cap=h('input',{placeholder:'توضیح (اختیاری)',maxlength:'200',dir:'auto',style:'margin-top:8px'});
      body.replaceChildren(pv,pick,cap);body.pv=pv;body.cap=cap;body.dur=0;if(file)choose(file)}}
  function choose(f){
    if(!f)return;file=f;const isV=/^video\//.test(f.type),url=URL.createObjectURL(f);msg.textContent='';
    const pv=body.pv;if(!pv)return;
    if(isV){const vd=h('video',{src:url,muted:'',playsinline:'',controls:'',preload:'metadata'});
      vd.onloadedmetadata=()=>{body.dur=vd.duration;if(vd.duration>30.5){msg.textContent='ویدیوی استوری حداکثر ۳۰ ثانیه است.';file=null}};pv.replaceChildren(vd)}
    else pv.replaceChildren(h('img',{src:url,alt:''}))}
  async function publish(){
    if(busy)return;msg.textContent='';
    if(mode==='text'){const text=body.ta.value.trim();if(!text){msg.textContent='متن خالیه';return}
      busy=true;btn.disabled=true;try{await call('story_text',{text,bg,audience});done()}catch(e){msg.textContent=e.message}busy=false;btn.disabled=false;return}
    if(!file){msg.textContent=msg.textContent||'اول یه عکس یا ویدیو انتخاب کن';return}
    const isV=/^video\//.test(file.type);
    const q=new URLSearchParams({k:S.mediaKey,kind:isV?'video':'image',audience});if(body.cap.value.trim())q.set('text',body.cap.value.trim());if(isV)q.set('dur',String(Math.ceil(body.dur||0)));
    busy=true;btn.disabled=true;btn.textContent='در حال ارسال… ۰٪';
    const x=new XMLHttpRequest();x.open('POST','/storyupload?'+q);
    x.upload.onprogress=e=>{if(e.lengthComputable)btn.textContent='در حال ارسال… '+fa(Math.round(e.loaded/e.total*100))+'٪'};
    x.onload=()=>{let r={};try{r=JSON.parse(x.responseText)}catch{}busy=false;btn.disabled=false;btn.textContent='انتشار';
      if(x.status===200&&r.ok)done();else msg.textContent=r.error||'ارسال ناموفق بود'};
    x.onerror=()=>{busy=false;btn.disabled=false;btn.textContent='انتشار';msg.textContent='ارسال ناموفق بود'};x.send(file)}
  function done(){closeDialog();toast('استوری منتشر شد',false);load()}
  paint()}

/* ------------------------------------------------------------ hooks into the app */
function openById(id){
  for(const g of ST.groups){const k=g.stories.findIndex(s=>s.id===id);if(k>=0)return openViewer(g,id)}
  toast('این استوری منقضی شده')}
(window.wdBubble=window.wdBubble||[]).push((b,m)=>{
  if(!m.story)return;
  const chip=h('div',{class:'story-chip',onclick:e=>{e.stopPropagation();openById(m.story.id)}},ic('story',16),'پاسخ به استوری');
  const at=b.querySelector('.sender')?1:0;b.insertBefore(chip,b.children[at]||null)});
(window.wdEvents=window.wdEvents||[]).push(e=>{
  if(e.event==='story_new'||e.event==='story_gone'){loadSoon();return true}
  if(e.event==='story_react'){toast((e.user&&e.user.display_name||'کسی')+' به استوری تو '+e.emoji+' داد',false);loadSoon();return true}
  return false});
window.wdStories={load,composer,open:openById};
setInterval(()=>{if(S.me)load()},5*60*1000);
})();
