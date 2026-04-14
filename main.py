"""
RCV Microservicio — SIG PharosChile
Descarga el Registro de Compras y Ventas del SII usando Playwright.
"""
import asyncio
import gzip
import os
import sys
import json
import base64
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI(title="RCV Microservicio", version="1.0.0")

# Token de autenticación (se configura como variable de entorno)
API_SECRET = os.environ.get("API_SECRET", "")

class DescargaRequest(BaseModel):
    rut: str
    clave: str
    fecha_inicio: str   # YYYY-MM-DD
    fecha_final: str    # YYYY-MM-DD
    tipo_descarga: str  # "compras" | "ventas" | "ambos"
    descarga_id: str    # para nombrar los archivos

MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
         'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']

async def login(page, rut, clave):
    print("STEP:login", flush=True)
    await page.goto(
        'https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html'
        '?https://www4.sii.cl/consdcvinternetui/'
    )
    await page.wait_for_load_state('networkidle')
    await page.fill('input[name="rutcntr"]', rut)
    await page.fill('input[name="clave"]', clave)
    await page.click('button[type="submit"], button#bt_ingresar')
    await page.wait_for_load_state('networkidle')
    await page.wait_for_timeout(3000)
    if 'consdcvinternetui' not in page.url:
        raise Exception(f"Login fallido: {page.url}")
    print(f"LOGIN_OK:{page.url}", flush=True)

async def seleccionar_periodo(page, mes_label, anho):
    print(f"STEP:seleccionar_periodo:{mes_label} {anho}", flush=True)
    selects = await page.query_selector_all('select')
    for sel in selects:
        opts = await sel.inner_text()
        if mes_label in opts:
            await sel.select_option(label=mes_label)
        elif anho in opts:
            await sel.select_option(value=anho)
    btns = await page.query_selector_all('button')
    for btn in btns:
        txt = (await btn.inner_text()).strip()
        if 'Consultar' in txt:
            await btn.click()
            break
    await page.wait_for_timeout(5000)
    print(f"PERIODO_OK", flush=True)

async def navegar_state(page, state):
    try:
        await page.evaluate(f"""
            () => {{
                let el = document.querySelector('[ng-app]') || document.querySelector('[ng-controller]');
                if (el) angular.element(el).injector().get('$state').go('{state}');
            }}
        """)
        await page.wait_for_timeout(4000)
        print(f"NAV_OK:{state}", flush=True)
    except Exception as e:
        print(f"ERROR:navegar_state:{state}:{e}", flush=True)

async def get_total(page):
    try:
        t = await page.evaluate("""
            () => {
                let rows = document.querySelectorAll('table tbody tr'), s = 0;
                rows.forEach(r => {
                    let c = r.querySelectorAll('td');
                    if (c.length > 1) s += parseInt(c[1].textContent.replace(/[^0-9]/g,'')) || 0;
                });
                return s;
            }
        """)
        return int(t) if t else 0
    except:
        return 0

async def get_limite(page):
    try:
        v = await page.evaluate("""
            () => {
                try {
                    let el = document.querySelector('[ng-controller]') || document.querySelector('[ng-app]');
                    let s = angular.element(el).scope();
                    return (s.$root && s.$root.limiteDoc) || s.limiteDoc || 1000;
                } catch(e) { return 1000; }
            }
        """)
        return int(v) if v else 1000
    except:
        return 1000

async def dl_resumenes(page, prefijo, out_dir):
    print(f"STEP:resumenes_{prefijo}", flush=True)
    try:
        async with page.expect_download(timeout=30000) as info:
            await page.get_by_text('Descargar Resumenes', exact=False).first.click()
        dl = await info.value
        path = os.path.join(out_dir, f'{prefijo}_RESUMENES_{dl.suggested_filename}')
        await dl.save_as(path)
        print(f"FILE:{path}", flush=True)
        return path
    except Exception as e:
        print(f"ERROR:resumenes_{prefijo}:{e}", flush=True)
        return None

async def dl_detalles_sync(page, prefijo, out_dir):
    print(f"STEP:detalles_sync_{prefijo}", flush=True)
    try:
        async with page.expect_download(timeout=60000) as info:
            await page.get_by_text('Descargar Detalles', exact=False).first.click()
        dl = await info.value
        path = os.path.join(out_dir, f'{prefijo}_DETALLES_{dl.suggested_filename}')
        await dl.save_as(path)
        print(f"FILE:{path}", flush=True)
        return path
    except Exception as e:
        print(f"ERROR:detalles_sync_{prefijo}:{e}", flush=True)
        return None

async def dl_detalles_async(page, prefijo, out_dir, total):
    print(f"STEP:detalles_async_{prefijo}:{total}", flush=True)
    await page.get_by_text('Descargar Detalles', exact=False).first.click()
    await page.wait_for_timeout(2000)
    confirm = page.get_by_role('button', name='Confirmar')
    if await confirm.count() > 0:
        await confirm.first.click()
        await page.wait_for_timeout(5000)
    for i in range(60):
        await page.wait_for_timeout(15000)
        refresh = page.get_by_text('Refrescar', exact=False)
        if await refresh.count() > 0:
            await refresh.first.click()
            await page.wait_for_timeout(2000)
        estado = await page.evaluate("""
            () => {
                for (let l of document.querySelectorAll('.label')) {
                    let t = l.textContent.trim();
                    if (['SOLICITADA','EN PROCESO','TERMINADA'].includes(t)) return t;
                }
                for (let b of document.querySelectorAll('button'))
                    if (b.textContent.trim()==='Descargar') return 'LISTO';
                return '';
            }
        """)
        print(f"ASYNC_STATUS:{prefijo}:{i+1}:{estado}", flush=True)
        if estado in ('TERMINADA', 'LISTO'):
            break
    else:
        print(f"ERROR:async_timeout_{prefijo}", flush=True)
        return None
    try:
        async with page.expect_download(timeout=120000) as info:
            await page.get_by_role('button', name='Descargar').first.click()
        dl = await info.value
        fname = dl.suggested_filename or 'detalles.csv.gz'
        gzpath = os.path.join(out_dir, f'{prefijo}_DETALLES_ASYNC_{fname}')
        await dl.save_as(gzpath)
        if gzpath.endswith('.gz'):
            csvpath = gzpath[:-3]
            with gzip.open(gzpath, 'rb') as fi, open(csvpath, 'wb') as fo:
                fo.write(fi.read())
            os.remove(gzpath)
            print(f"FILE:{csvpath}", flush=True)
            return csvpath
        print(f"FILE:{gzpath}", flush=True)
        return gzpath
    except Exception as e:
        print(f"ERROR:async_download_{prefijo}:{e}", flush=True)
        return None

async def procesar(page, prefijo, out_dir):
    await dl_resumenes(page, prefijo, out_dir)
    total = await get_total(page)
    limite = await get_limite(page)
    print(f"INFO:{prefijo}:total={total}:limite={limite}", flush=True)
    if total <= limite:
        d = await dl_detalles_sync(page, prefijo, out_dir)
        if d: return
    await dl_detalles_async(page, prefijo, out_dir, total)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "RCV Microservicio"}

@app.post("/descargar")
async def descargar(req: DescargaRequest, x_api_secret: str = Header(default="")):
    # Verificar token
    if API_SECRET and x_api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    anho, mes, _ = req.fecha_inicio.split('-')
    mes_label = MESES[int(mes) - 1]
    hacer_compras = req.tipo_descarga in ('compras', 'ambos')
    hacer_ventas  = req.tipo_descarga in ('ventas', 'ambos')

    out_dir = f'/tmp/rcv_{req.descarga_id}'
    os.makedirs(out_dir, exist_ok=True)

    archivos = []
    logs = []

    try:
        async with async_playwright() as p:
            br  = await p.chromium.launch(headless=True)
            ctx = await br.new_context(accept_downloads=True)
            pg  = await ctx.new_page()

            await login(pg, req.rut, req.clave)
            logs.append("LOGIN_OK")

            await seleccionar_periodo(pg, mes_label, anho)
            logs.append(f"PERIODO_OK:{mes_label} {anho}")

            if hacer_compras:
                await procesar(pg, 'COMPRAS', out_dir)
                logs.append("COMPRAS_OK")

            if hacer_ventas:
                await navegar_state(pg, 'venta')
                await procesar(pg, 'VENTAS', out_dir)
                logs.append("VENTAS_OK")

            await br.close()

        # Leer archivos y codificar en base64
        for fname in os.listdir(out_dir):
            fpath = os.path.join(out_dir, fname)
            with open(fpath, 'rb') as f:
                contenido = base64.b64encode(f.read()).decode('utf-8')
            tipo = 'otro'
            fn = fname.upper()
            if 'COMPRAS_RESUMENES' in fn:   tipo = 'compras_resumenes'
            elif 'COMPRAS_DETALLES' in fn:  tipo = 'compras_detalles'
            elif 'VENTAS_RESUMENES' in fn:  tipo = 'ventas_resumenes'
            elif 'VENTAS_DETALLES' in fn:   tipo = 'ventas_detalles'
            archivos.append({
                "nombre": fname,
                "tipo": tipo,
                "contenido_b64": contenido,
                "size": os.path.getsize(fpath)
            })

        # Limpiar tmp
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)

        return JSONResponse({
            "ok": True,
            "archivos": archivos,
            "logs": logs
        })

    except Exception as e:
        import traceback
        return JSONResponse({
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "logs": logs
        }, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
