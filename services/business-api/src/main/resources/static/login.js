"use strict";
const form=document.getElementById("login-form"),message=document.getElementById("message"),submit=document.getElementById("login-submit");
function notice(text){message.textContent=text;message.hidden=false;}
const params=new URLSearchParams(location.search);
if(params.has("error"))notice("账户或密码不正确，请重新输入。");
else if(params.has("logout"))notice("已退出工作空间。");
else if(params.has("expired"))notice("登录已过期，请重新登录。未提交的文件需要重新选择。");
fetch("/session/csrf",{signal:AbortSignal.timeout(10000)}).then(async r=>{if(!r.ok)throw new Error();const c=await r.json();const field=document.getElementById("csrf");field.name=c.parameter_name;field.value=c.token;submit.disabled=false;}).catch(()=>notice("登录页面暂时无法连接服务，请稍后刷新。"));
form.addEventListener("submit",async event=>{
  event.preventDefault();submit.disabled=true;submit.textContent="正在登录…";
  try {
    const body=new URLSearchParams(new FormData(form));
    const response=await fetch("/login",{method:"POST",body,signal:AbortSignal.timeout(15000)});
    if(response.status===429){notice("登录尝试较多，请一分钟后再试。");return;}
    if(response.status===403){notice("登录请求已过期，请刷新页面后重试。");return;}
    if(!response.ok)throw new Error();
    if(new URL(response.url).pathname==="/demo/index.html"){location.assign("/demo/index.html");return;}
    document.getElementById("password").value="";notice("账户或密码不正确，请重新输入。");
  } catch(_){notice("暂时无法连接登录服务，请稍后重试。");}
  finally{submit.disabled=false;submit.textContent="登录工作空间";}
});
