"""Prueba de XSS en navegador real: mete código en todos los textos y comprueba que no se ejecuta.

Ejecutar:  python3 tests/xss_test.py   (requiere playwright y chromium)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PORT = 5077
tmp = tempfile.mkdtemp()
env = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp}/x.db", APP_SECRET_KEY="x" * 48, ALLOW_REGISTRATION="true",
           BOOTSTRAP_TOKEN="b" * 32, COOKIE_SECURE="false", RATELIMIT_STORAGE_URI="memory://", API_DEFAULT_LIMIT="5000 per minute")
srv = subprocess.Popen([sys.executable, "-c", f"import app; app.app.run(port={PORT})"], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=1)
        break
    except Exception:
        time.sleep(0.25)

req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/setup", method="POST", headers={"Content-Type": "application/json"},
    data=json.dumps({"bootstrap_token": "b" * 32, "username": "admin", "password": "Zeta-Roble-9182x"}).encode())
urllib.request.urlopen(req)
P = ['<img src=x onerror="window.__xss=1">', '"><svg onload=window.__xss=1>', "'-window.__xss=1-'",
     '<script>window.__xss=1</script>', 'javascript:window.__xss=1', '</textarea><img src=x onerror=window.__xss=1>']
fail = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(base_url=f"http://127.0.0.1:{PORT}")
        page = ctx.new_page()
        page.add_init_script("try{sessionStorage.setItem('qapk_s','1');localStorage.setItem('qapk','1')}catch(e){}")
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto("/")
        res = page.evaluate("""async (P) => {
          const j = async (u,o={}) => { const r = await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(window.__c?{'X-CSRF-Token':window.__c}:{})},...o}); return r.json().catch(()=>({})) };
          let r = await j('/api/register',{method:'POST',body:JSON.stringify({username:'xss'+P.length,email:'x@y.com',password:'Zorro-Plata-55231x',accept_terms:true})});
          let s = await j('/api/status'); window.__c = s.csrf_token;
          const st = await j("/api/state"); if(!st.data) return {err:1, r, s};
          const d = st.data, mk = (i) => 'id'+i; d.termsVersion='2026-10-23';
          const rows = (n, f) => P.map((p,i)=>f(p,i));
          d.accounts = rows(0,(p,i)=>({id:mk(i),name:p,kind:'banco',currency:'USD',initial:5}));
          d.incomes = rows(0,(p,i)=>({id:mk(i),date:'2026-10-01',amount:5,currency:'USD',name:p,kind:'extra'}));
          d.receivables = rows(0,(p,i)=>({id:mk(i),person:p,amount:9,paid:0,currency:'USD',date:'2026-10-01',dueDate:'2026-10-20',note:p}));
          d.expenses = rows(0,(p,i)=>({id:mk(i),amount:3,paid:3,currency:'USD',date:'2026-10-02',category:'comida',description:p,bank:p,method:'banco'}));
          d.debts = rows(0,(p,i)=>({id:mk(i),amount:30,paid:0,currency:'USD',date:'2026-10-02',type:'otro',entity:p,description:p,minPayment:3}));
          const put = await fetch('/api/state',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':window.__c},body:JSON.stringify({data:d,base_data:st.data,base_revision:st.revision})});
          return {reg: r.ok||r.error, put: put.status};
        }""", P)
        print("preparación:", res)
        if res.get("err"): raise SystemExit(1)
        page.reload()
        page.wait_for_timeout(1500)
        tabs = page.eval_on_selector_all("nav [data-tab]", "els => els.map(e => e.dataset.tab)")
        print("pestañas:", tabs)
        for t in tabs:
            page.click(f"nav [data-tab='{t}']")
            page.wait_for_timeout(300)
        page.wait_for_timeout(500)
        executed = page.evaluate("window.__xss === 1")
        # el texto debe verse como texto, no como etiquetas
        shown = False
        for t in tabs:
            page.click(f"nav [data-tab='{t}']"); page.wait_for_timeout(250)
            shown = shown or page.evaluate("document.body.innerText.includes('onerror')")
        saved = page.evaluate("fetch('/api/state').then(r=>r.json()).then(j=>[j.data.expenses.length,j.data.debts.length,j.data.accounts.length,j.data.receivables.length,(j.data.expenses[0]||{}).description])")
        print("guardado en el servidor:", saved)
        if executed: fail.append("se ejecutó código inyectado")
        if dialogs: fail.append(f"salieron diálogos: {dialogs}")
        print("texto inyectado visible como texto:", shown)
        b.close()
finally:
    srv.terminate()
print("XSS:", "FALLA " + "; ".join(fail) if fail else "ok, no se ejecutó nada")
sys.exit(1 if fail else 0)
