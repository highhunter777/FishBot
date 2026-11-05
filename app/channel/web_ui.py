PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>FishBot</title>
<style>
  :root{
    --bg:#0e1116; --panel:#161b22; --line:#232a35;
    --me:#3b5bfd; --bot:#1c2230; --txt:#e6edf3; --dim:#7d8799;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{height:100%;margin:0}
  body{
    background:radial-gradient(1200px 600px at 80% -10%,#1b2440 0%,var(--bg) 55%);
    color:var(--txt);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    display:flex;flex-direction:column;height:100dvh;
  }
  header{
    display:flex;align-items:center;gap:10px;padding:14px 18px;
    border-bottom:1px solid var(--line);backdrop-filter:blur(8px);position:sticky;top:0;z-index:5;
  }
  .dot{width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 8px #3fb950}
  .title{font-weight:600;letter-spacing:.3px}
  .sub{color:var(--dim);font-size:12px;margin-left:auto}
  #feed{flex:1;overflow-y:auto;padding:20px 16px 8px;display:flex;flex-direction:column;gap:12px;scroll-behavior:smooth}
  .row{display:flex;max-width:100%}
  .row.me{justify-content:flex-end}
  .bubble{
    max-width:min(78%,760px);padding:10px 14px;border-radius:16px;white-space:pre-wrap;
    word-break:break-word;border:1px solid transparent;
  }
  .me .bubble{background:linear-gradient(135deg,#3b5bfd,#6a4bff);border-bottom-right-radius:5px}
  .bot .bubble{background:var(--bot);border-color:var(--line);border-bottom-left-radius:5px}
  .meta{color:var(--dim);font-size:11px;margin:2px 6px 0}
  .typing{display:inline-flex;gap:4px;align-items:center;padding:2px 0}
  .typing i{width:6px;height:6px;border-radius:50%;background:#5b6474;animation:b 1.2s infinite}
  .typing i:nth-child(2){animation-delay:.2s}
  .typing i:nth-child(3){animation-delay:.4s}
  @keyframes b{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-4px);opacity:1}}
  footer{padding:12px 14px calc(14px + env(safe-area-inset-bottom));border-top:1px solid var(--line);background:rgba(14,17,22,.8);backdrop-filter:blur(10px)}
  .composer{display:flex;gap:10px;align-items:flex-end}
  textarea{
    flex:1;resize:none;max-height:140px;min-height:46px;padding:12px 14px;border-radius:14px;
    background:var(--panel);border:1px solid var(--line);color:var(--txt);font:inherit;outline:none;
  }
  textarea:focus{border-color:#3b5bfd}
  button{
    height:46px;padding:0 18px;border-radius:14px;border:0;cursor:pointer;font-weight:600;
    background:linear-gradient(135deg,#3b5bfd,#6a4bff);color:#fff;
  }
  button:disabled{opacity:.45;cursor:default}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim);font-weight:500;padding:0 12px}
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <span class="title">FishBot</span>
  <span class="sub" id="status">就绪</span>
</header>
<div id="feed"></div>
<footer>
  <div class="composer">
    <textarea id="input" rows="1" placeholder="说点什么…  （/help 看指令）"></textarea>
    <button class="ghost" id="reset" title="清空上下文">清空</button>
    <button id="send">发送</button>
  </div>
</footer>
<script>
const feed = document.getElementById('feed');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const uid = localStorage.getItem('fb_uid') || (crypto.randomUUID().slice(0,8));
localStorage.setItem('fb_uid', uid);
const sid = localStorage.getItem('fb_sid') || ('web-' + uid);
localStorage.setItem('fb_sid', sid);

let queue = [], pumping = false, current = null, busy = false;

function row(who){
  const r = document.createElement('div');
  r.className = 'row ' + who;
  const b = document.createElement('div');
  b.className = 'bubble';
  r.appendChild(b);
  feed.appendChild(r);
  return b;
}
function scroll(){ feed.scrollTop = feed.scrollHeight; }

async function pump(el){
  pumping = true;
  while (queue.length){
    const ch = queue.shift();
    el.textContent += ch;
    scroll();
    await sleep(18 + Math.random()*22);
  }
  pumping = false;
}
function enqueue(text){
  if (!current) current = row('bot');
  queue.push(...text);
  if (!pumping) pump(current);
}

async function send(){
  const text = input.value.trim();
  if (!text || busy) return;
  input.value = ''; autoGrow();
  row('me').textContent = text;
  scroll();

  busy = true; sendBtn.disabled = true; statusEl.textContent = '思考中';
  current = null; queue = [];

  // 预期停顿期间先露个"正在输入"
  const holder = document.createElement('div');
  holder.className = 'row bot';
  holder.innerHTML = '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>';
  feed.appendChild(holder); scroll();

  try{
    const resp = await fetch('/api/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sid, user_id: uid, text})
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      const lines = buf.split('\\n\\n');
      buf = lines.pop();
      for (const line of lines){
        if (!line.startsWith('data:')) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.type === 'action'){
          if (holder.parentNode){ holder.remove(); }
          if (evt.delay) await sleep(evt.delay * 1000);
          enqueue(evt.text || '');
        }
      }
    }
    while (pumping) await sleep(30);
  }catch(e){
    if (holder.parentNode) holder.remove();
    enqueue('连接断了，刷新下页面。');
  }finally{
    if (holder.parentNode) holder.remove();
    current = null;
    busy = false; sendBtn.disabled = false; statusEl.textContent = '就绪';
  }
}

function autoGrow(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
}
input.addEventListener('input', autoGrow);
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); }
});
sendBtn.addEventListener('click', send);
document.getElementById('reset').addEventListener('click', async () => {
  await fetch('/api/reset', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({session_id: sid, user_id: uid})});
  feed.innerHTML = ''; enqueue('清了。'); current = null;
});

(async () => {
  try{
    const r = await fetch('/api/history?session_id=' + encodeURIComponent(sid));
    const data = await r.json();
    for (const m of data.messages){
      row(m.role === 'user' ? 'me' : 'bot').textContent = m.content;
    }
    scroll();
  }catch(e){}
})();
</script>
</body>
</html>"""
