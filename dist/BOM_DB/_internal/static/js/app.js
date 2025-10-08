import { authHeader } from './auth.js';

window.api = function(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), ...authHeader() };
  return fetch(url, opts);
};

window.checkOperator = async function(){
  const tok = localStorage.getItem('bomdb_token');
  if(!tok) return;
  const r = await api('/auth/me');
  if(r.ok){
    const u = await r.json();
    if(u.role === 'viewer'){
      document.querySelectorAll('input').forEach(el=>el.disabled=true);
      document.querySelectorAll('#save-bom,.del-btn,#upload-bom,#import-btn,.upload-btn,#po-btn').forEach(el=>el && el.classList.add('hidden'));
    }
  }
};

window.addEventListener('load', window.checkOperator);
