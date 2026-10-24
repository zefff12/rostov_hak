// Поп-ап виджет для встраивания в корпоративный портал:
// <script src="http://<сервер помощника>/widget.js" defer></script>
(function () {
  const origin = new URL(document.currentScript.src).origin;
  const css = `
  #dou-w-btn{position:fixed;right:20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:none;
    background:#1f5fae;color:#fff;font-size:26px;box-shadow:0 6px 18px rgba(0,0,0,.25);cursor:pointer;z-index:2147483000}
  #dou-w-frame{position:fixed;right:20px;bottom:90px;width:420px;height:640px;max-height:calc(100vh - 110px);
    border:1px solid #d0d5dd;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.25);background:#fff;
    z-index:2147483000;display:none}
  @media (max-width:520px){#dou-w-frame{right:0;bottom:0;width:100vw;height:100vh;max-height:none;border-radius:0}
    #dou-w-btn.open{display:none}}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const btn = document.createElement("button");
  btn.id = "dou-w-btn"; btn.title = "Помощник по документообороту"; btn.textContent = "💬";
  const fr = document.createElement("iframe");
  fr.id = "dou-w-frame"; fr.title = "Помощник по документообороту";
  let loaded = false;
  btn.onclick = () => {
    if (!loaded) { fr.src = origin + "/index.html?embed=1"; loaded = true; }
    const open = fr.style.display !== "block";
    fr.style.display = open ? "block" : "none";
    btn.textContent = open ? "✕" : "💬";
    btn.classList.toggle("open", open);
  };
  document.body.appendChild(fr); document.body.appendChild(btn);
})();
