"use strict";
(() => {
  let settings=null;
  window.commerceSessionHeaders=()=>settings?.csrf_header?{[settings.csrf_header]:settings.csrf_token}:{};
  window.setupCommerceSession=config=>{
    settings=config;if(!config.owner_mode)return;
    const environment=document.querySelector(".environment");
    environment.textContent="店主工作空间 ";
    const logout=document.createElement("button");logout.type="button";logout.className="secondary";logout.textContent="退出";logout.id="logout";
    logout.onclick=async()=>{logout.disabled=true;try{const r=await fetch("/logout",{method:"POST",headers:window.commerceSessionHeaders(),body:"",signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error();location.assign("/login?logout");}catch(_){logout.textContent="请刷新后退出";logout.disabled=false;}};
    environment.append(logout);
    const side=document.querySelector(".side-note");side.textContent="独立工作空间";
    const detail=document.createElement("p");detail.textContent="数据与报告保存在此部署";side.append(detail);
    const footer=document.querySelector("footer span:last-child");if(footer)footer.textContent="个人部署 · 单店主空间";
  };
})();
