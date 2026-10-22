'use strict';
/* Windroida bots in the web app: inline keyboards under bot messages, toast/alert answers, the START button of an
   empty bot chat and the "/" command menu. Safe DOM building only. Loaded after app.js (and stories.js). */
(function(){
const safeUrl=u=>/^https?:\/\/[^\s]+$/i.test(u||'');

/* ---- inline keyboard under a bot message */
(window.wdBubble=window.wdBubble||[]).push((b,m)=>{
  if(!m.markup||!m.markup.length)return;
  const stop=e=>e.stopPropagation();
  const kb=h('div',{class:'bkb',onclick:stop,ontouchstart:stop,onpointerdown:stop},m.markup.map(row=>h('div',{class:'bkrow'},row.map(bt=>{
    if(bt.url){return safeUrl(bt.url)?h('a',{class:'bkbtn link',href:bt.url,target:'_blank',rel:'noopener noreferrer nofollow',title:bt.url},bt.text,' ↗'):null}
    const el=h('button',{type:'button',class:'bkbtn'},bt.text);
    el.onclick=async()=>{if(el.classList.contains('wait'))return;el.classList.add('wait');
      try{await call('bot_callback',{message_id:m.id,data:bt.callback_data})}catch(e){toast(e.message)}
      setTimeout(()=>el.classList.remove('wait'),700)};
    return el}))));
  b.append(kb)});

/* ---- a bot answered a button press (answerCallbackQuery) */
(window.wdEvents=window.wdEvents||[]).push(e=>{
  if(e.event!=='bot_answer')return false;
  if(e.alert)dialog(e.bot||'ربات',h('div',{style:'white-space:pre-wrap;margin:6px 0',dir:'auto'},e.text),h('button',{class:'btn',onclick:closeDialog},'باشه'));
  else toast(e.text,false);
  return true});

/* ---- START button + "/" command menu in a bot chat */
const botOf=()=>{const c=S.chats[S.current];return c&&c.kind==='private'&&c.peer&&c.peer.bot&&typeof c.peer.bot==='object'?c:null};
function ensureUi(){
  let bar=$('#botstart');
  if(!bar){
    bar=h('div',{id:'botstart',hidden:true});$('#composer').before(bar);
    const menu=h('div',{id:'botcmds',hidden:true});$('#composer').before(menu);
    const mb=h('button',{class:'iconbtn',id:'botmenu',type:'button',title:'دستورهای ربات',hidden:true,onclick:()=>toggleMenu()},ic('chatb'));
    $('#input').before(mb);
    $('#input').addEventListener('input',()=>{const v=$('#input').value;if(botOf()&&/^\/[A-Za-z0-9_]*$/.test(v))toggleMenu(true,v.slice(1).toLowerCase());else toggleMenu(false)});
    document.addEventListener('click',e=>{const m=$('#botcmds');if(m&&!m.hidden&&!m.contains(e.target)&&e.target.id!=='botmenu'&&!e.target.closest('#botmenu'))m.hidden=true})}
  return bar}
function toggleMenu(force,filter=''){
  const c=botOf(),m=$('#botcmds');if(!m)return;
  const open=force!==undefined?force:m.hidden;
  if(!open||!c){m.hidden=true;return}
  const cmds=(c.peer.bot.commands||[]).filter(x=>x.command.startsWith(filter));
  if(!cmds.length){m.hidden=true;return}
  m.replaceChildren(...cmds.map(x=>h('button',{type:'button',class:'bcmd',onclick:()=>{m.hidden=true;runCommand('/'+x.command)}},
    h('b',{dir:'ltr'},'/'+x.command),h('span',{},x.description||''))));
  m.hidden=false}
async function runCommand(text){
  const id=S.current;if(!id)return;
  $('#input').value='';autosize();updateSendBtn();
  try{const r=await call('send',{chat_id:id,text});sfx('send');window.__live=Date.now();ingest(r.message);$('#msgs').scrollTop=$('#msgs').scrollHeight}catch(e){toast(e.message)}}
function sync(){
  const bar=ensureUi(),c=botOf(),mb=$('#botmenu');
  if(mb)mb.hidden=!(c&&(c.peer.bot.commands||[]).length);
  if(!c){bar.hidden=true;toggleMenu(false);return}
  const list=S.msgs[c.id],empty=list&&!list.some(m=>m.kind!=='system');
  bar.hidden=!empty;
  if(empty){
    const kids=[];if(c.peer.bot.description)kids.push(h('div',{class:'bdesc',dir:'auto'},c.peer.bot.description));
    kids.push(h('button',{class:'btn',type:'button',onclick:()=>runCommand('/start')},'شروع'));bar.replaceChildren(...kids)}}
(window.wdChat=window.wdChat||[]).push(sync);

/* ---- the "ربات" tag in the profile card is added by app.js (dlgUser); BotFather has its own entry in the side menu */
})();
