#!/usr/bin/env python3
"""
Sistema de reporte bisemanal de hacienda.
Scrapea: Mercado de Liniers, Open-Meteo (clima), Coto / Carrefour / Disco.
Genera: reporte HTML interactivo + historial JSON.
Corre: martes y viernes via GitHub Actions.
"""

import requests
from bs4 import BeautifulSoup
import json, re, os, datetime, time

# ── CONFIG ────────────────────────────────────────────────────────────────────
LINIERS_URL  = "https://www.mercadoagroganadero.com.ar/dll/hacienda1.dll/haciinfo000002"
CLIMA_URL    = "https://api.open-meteo.com/v1/forecast"
CLIMA_LAT, CLIMA_LON = -34.6, -58.4

OUTPUT_DIR   = "docs"
OUTPUT_HTML  = f"{OUTPUT_DIR}/index.html"
OUTPUT_JSON  = f"{OUTPUT_DIR}/historial.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

RINDE = {
    "vaca":       {"faena": 0.55,  "mesa": 0.66,   "label": "Vaca"},
    "vaquillona": {"faena": 0.57,  "mesa": 0.6675, "label": "Vaquillona"},
    "novillo":    {"faena": 0.58,  "mesa": 0.675,  "label": "Novillo"},
}

SUPER_REF = {
    "Asado":            {"coto": 4190,  "carrefour": 4380,  "disco": 4650},
    "Vacío":            {"coto": 5200,  "carrefour": 5490,  "disco": 5890},
    "Cuadrada":         {"coto": 6400,  "carrefour": 6750,  "disco": 7200},
    "Nalga":            {"coto": 6800,  "carrefour": 7100,  "disco": 7600},
    "Bife de chorizo":  {"coto": 8900,  "carrefour": 9400,  "disco": 10200},
    "Bife de costilla": {"coto": 9200,  "carrefour": 9800,  "disco": 10800},
    "Lomo":             {"coto": 12500, "carrefour": 13200, "disco": 14500},
    "Paleta":           {"coto": 4800,  "carrefour": 5100,  "disco": 5400},
}


# ── LINIERS ───────────────────────────────────────────────────────────────────
def scrape_liniers():
    print("Scrapeando Mercado de Liniers...")
    try:
        r = requests.get(LINIERS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        r.encoding = "latin-1"
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ERROR: {e}")
        return _fallback_liniers()

    datos = {}

    # Estrategia 1 — parsear tablas HTML
    for tabla in soup.find_all("table"):
        for fila in tabla.find_all("tr"):
            celdas = [td.get_text(strip=True) for td in fila.find_all(["td","th"])]
            txt = " ".join(celdas).lower()
            for cat, aliases in {
                "vaca":       ["vaca","vacas"],
                "novillo":    ["novillo","novillos"],
                "vaquillona": ["vaquillona","vaquillon"],
            }.items():
                if any(a in txt for a in aliases) and cat not in datos:
                    for c in celdas:
                        p = _parse_precio(c)
                        if p:
                            datos[cat] = p
                            break
            if "volumen" not in datos:
                for w in ["total","entradas","ingreso"]:
                    if w in txt:
                        for c in celdas:
                            n = _parse_entero(c)
                            if n and 1000 < n < 100000:
                                datos["volumen"] = n

    # Estrategia 2 — regex sobre texto plano
    texto = soup.get_text(separator="\n").lower()
    for cat, pats in {
        "vaca":       [r"vaca[^0-9]{0,20}(\d[\d\.,]+)"],
        "novillo":    [r"novillo[^0-9]{0,20}(\d[\d\.,]+)"],
        "vaquillona": [r"vaquillon[^0-9]{0,20}(\d[\d\.,]+)"],
    }.items():
        if cat not in datos:
            for pat in pats:
                m = re.search(pat, texto)
                if m:
                    p = _parse_precio(m.group(1))
                    if p:
                        datos[cat] = p
                        break

    if "volumen" not in datos:
        m = re.search(r"(?:total|ingreso)[^0-9]{0,30}(\d[\d\.]+)", texto)
        if m:
            n = _parse_entero(m.group(1))
            if n and 1000 < n < 100000:
                datos["volumen"] = n

    fb = _fallback_liniers()
    for k in ["vaca","novillo","vaquillona","volumen"]:
        if k not in datos:
            datos[k] = fb[k]
            print(f"  FALLBACK {k}={fb[k]}")
        else:
            print(f"  OK {k}={datos[k]}")
    return datos


def _parse_precio(t):
    t = str(t).replace(",",".").strip()
    m = re.search(r"(\d[\d\.]+)", t)
    if not m: return None
    s = m.group(1)
    dots = s.count(".")
    if dots > 1:
        s = s.replace(".", "", dots - 1)
    try:
        v = float(s)
        return round(v) if 800 < v < 9000 else None
    except Exception:
        return None


def _parse_entero(t):
    t = re.sub(r"[.,]", "", str(t))
    m = re.search(r"(\d+)", t)
    try: return int(m.group(1)) if m else None
    except: return None


def _fallback_liniers():
    return {"vaca": 2180, "novillo": 2650, "vaquillona": 2480, "volumen": 12400}


# ── SUPERMERCADOS ─────────────────────────────────────────────────────────────
def _precio_de_html(html_text, rango=(2000, 30000)):
    """Extrae el primer precio válido de un bloque de HTML/texto."""
    # Buscar JSON con "price" o "sellingPrice"
    for pat in [r'"sellingPrice"\s*:\s*(\d+\.?\d*)',
                r'"price"\s*:\s*"?(\d+\.?\d*)"?',
                r'R\$\s*[\d.,]+']:   # ignorar R$
        m = re.search(pat, html_text)
        if m:
            try:
                v = float(m.group(1))
                if rango[0] < v < rango[1]:
                    return round(v)
            except Exception:
                pass
    # Buscar en texto plano
    soup = BeautifulSoup(html_text, "html.parser")
    for sel in ["[class*='price']","[class*='Price']","[class*='precio']"]:
        for el in soup.select(sel):
            raw = el.get_text(strip=True).replace("$","").replace(".","").replace(",",".")
            m = re.search(r"(\d{4,6}\.?\d*)", raw)
            if m:
                try:
                    v = float(m.group(1))
                    if rango[0] < v < rango[1]:
                        return round(v)
                except Exception:
                    pass
    return None


def _scrape_super(url, corte, super_name):
    try:
        r = requests.get(url, headers=HEADERS, timeout=14)
        p = _precio_de_html(r.text)
        if p:
            print(f"  LIVE {super_name} '{corte}': ${p:,}")
        return p
    except Exception as e:
        print(f"  ERROR {super_name} '{corte}': {e}")
        return None


def scrape_supermercados():
    print("Scrapeando supermercados...")
    resultado = {}
    consultas = [
        ("Asado",            "asado vacuno"),
        ("Vacío",            "vacio res"),
        ("Cuadrada",         "cuadrada bola"),
        ("Nalga",            "nalga res"),
        ("Bife de chorizo",  "bife de chorizo"),
        ("Bife de costilla", "bife de costilla"),
        ("Lomo",             "lomo res"),
        ("Paleta",           "paleta res"),
    ]

    for corte, q in consultas:
        ref = SUPER_REF.get(corte, {})
        q_enc = requests.utils.quote(q)

        # Coto
        p_coto = _scrape_super(
            f"https://www.cotodigital3.com.ar/sitios/cdigi/browse?_dyncharset=utf-8&Dy=1&Ntt={q_enc}&Nty=1&Ntk=All&siteScope=ok",
            corte, "Coto"
        ) or ref.get("coto")
        time.sleep(0.4)

        # Carrefour
        p_carre = _scrape_super(
            f"https://www.carrefour.com.ar/busca/?ft={q_enc}",
            corte, "Carrefour"
        ) or ref.get("carrefour")
        time.sleep(0.4)

        # Disco (Cencosud)
        p_disco = _scrape_super(
            f"https://www.disco.com.ar/{q_enc}?_q={q_enc}&map=ft",
            corte, "Disco"
        ) or ref.get("disco")
        time.sleep(0.4)

        vals   = [v for v in [p_coto, p_carre, p_disco] if v]
        prom   = round(sum(vals)/len(vals)) if vals else 0
        fuente = "live" if any(v and v != ref.get(k) for v,k in [(p_coto,"coto"),(p_carre,"carrefour"),(p_disco,"disco")]) else "ref"

        resultado[corte] = {
            "coto": p_coto or 0,
            "carrefour": p_carre or 0,
            "disco": p_disco or 0,
            "promedio": prom,
            "fuente": fuente,
        }

    return resultado


# ── CLIMA ─────────────────────────────────────────────────────────────────────
def get_clima():
    print("Consultando clima (Open-Meteo)...")
    try:
        r = requests.get(CLIMA_URL, params={
            "latitude": CLIMA_LAT, "longitude": CLIMA_LON,
            "daily": "precipitation_sum,weathercode",
            "forecast_days": 7,
            "timezone": "America/Argentina/Buenos_Aires",
        }, timeout=10)
        data = r.json()["daily"]
        dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
        alertas = []
        for f, ll, wc in zip(data["time"], data["precipitation_sum"], data["weathercode"]):
            dt = datetime.date.fromisoformat(f)
            dia = dias_es[dt.weekday()]
            ll  = ll or 0
            if ll > 5:
                alertas.append({
                    "tipo":"lluvia","fecha":dt.strftime("%d/%m"),"impacto":"alza",
                    "msg": f"Lluvia el {dia} {dt.strftime('%d/%m')} ({ll:.0f}mm) — precio sube +1.5% a +3% en sesiones siguientes",
                })
            if dt.weekday() == 4:   # viernes → avisa del finde
                alertas.append({
                    "tipo":"finde","fecha":dt.strftime("%d/%m"),"impacto":"alza",
                    "msg": f"Fin de semana — precio tiende a subir +2% a +4% el lunes siguiente",
                })
        return alertas[:4]
    except Exception as e:
        print(f"  ERROR clima: {e}")
        return []


# ── EFICIENCIA ────────────────────────────────────────────────────────────────
def calcular_eficiencia(precios):
    res = {}
    for cat, r in RINDE.items():
        pie = precios.get(cat, 0)
        if not pie: continue
        res[cat] = {
            **r,
            "precio_pie":  pie,
            "costo_mesa":  round(pie / r["mesa"]),
        }
    if "vaca" in res:
        base = res["vaca"]["costo_mesa"]
        for cat in ["novillo","vaquillona"]:
            if cat in res:
                max_p = round(base * RINDE[cat]["mesa"])
                diff  = round((max_p - res[cat]["precio_pie"]) / res[cat]["precio_pie"] * 100, 1)
                res[cat]["max_precio_equiv"] = max_p
                res[cat]["diff_pct"] = diff
    mejor = min(res.items(), key=lambda x: x[1]["costo_mesa"])
    res[mejor[0]]["mejor_opcion"] = True
    return res


# ── TENDENCIA ─────────────────────────────────────────────────────────────────
def calcular_tendencia(precios, volumen, alertas, historial):
    VOL_PROM = 15000
    score, factores = 0, []

    if volumen < VOL_PROM * 0.85:
        score += 2
        factores.append(f"Volumen bajo ({volumen:,} cab. vs promedio {VOL_PROM:,}) — oferta reducida presiona al alza")
    elif volumen > VOL_PROM * 1.15:
        score -= 2
        factores.append(f"Volumen alto ({volumen:,} cab.) — mayor oferta presiona a la baja")

    for a in alertas:
        if a["impacto"] == "alza":
            score += 1
            factores.append(a["msg"])

    if len(historial) >= 4:
        for cat in ["vaca","novillo","vaquillona"]:
            vals = [s["precios"].get(cat,0) for s in historial[-4:] if s["precios"].get(cat,0)]
            if vals and precios.get(cat):
                prom_h = sum(vals)/len(vals)
                var = round((precios[cat] - prom_h)/prom_h*100, 1)
                if abs(var) > 1:
                    dir_ = "sube" if var>0 else "baja"
                    factores.append(f"{cat.title()} {dir_} {abs(var):.1f}% respecto a las últimas 4 sesiones")
                    score += 1 if var>0 else -1

    if score >= 2:
        return {"señal":"ALZA","color":"#27500A","bg":"#EAF3DE","icono":"▲",
                "rec":"Cerrar compras esta semana antes de que suba más. No esperar.",
                "factores":factores}
    elif score <= -2:
        return {"señal":"BAJA","color":"#791F1F","bg":"#FCEBEB","icono":"▼",
                "rec":"Hay margen para negociar. El mercado afloja — pedir descuento o esperar.",
                "factores":factores}
    else:
        return {"señal":"ESTABLE","color":"#633806","bg":"#FAEEDA","icono":"→",
                "rec":"Mercado mixto. Priorizar la categoría con mejor eficiencia kg/mesa.",
                "factores":factores}


# ── HISTORIAL ─────────────────────────────────────────────────────────────────
def cargar_historial():
    if os.path.exists(OUTPUT_JSON):
        try:
            with open(OUTPUT_JSON) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def guardar_historial(historial, precios, tendencia):
    historial.append({
        "fecha":    datetime.datetime.now().isoformat(),
        "precios":  {k: precios.get(k) for k in ["vaca","novillo","vaquillona","volumen"]},
        "tendencia": tendencia["señal"],
    })
    historial = historial[-90:]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)
    return historial


# ── HTML ──────────────────────────────────────────────────────────────────────
def generar_html(precios, eficiencia, supermercados, alertas, tendencia, historial):
    now = datetime.datetime.now()
    dias_es  = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio",
                "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_str = f"{dias_es[now.weekday()]} {now.day} de {meses_es[now.month-1]} {now.year}"

    hoy = now.date()
    siguientes = {d: ((d - hoy.weekday()) % 7 or 7) for d in [1, 4]}  # martes=1, viernes=4
    prox_wd, prox_n = min(siguientes.items(), key=lambda x: x[1])
    prox_fecha  = (hoy + datetime.timedelta(days=prox_n)).strftime("%d/%m")
    prox_nombre = dias_es[prox_wd]

    # Datos para gráficos
    sess = historial[-11:] + [{"fecha": now.isoformat(),
                                "precios": precios, "tendencia": tendencia["señal"]}]
    h_labels  = [s["fecha"][:10] for s in sess]
    h_labels[-1] = "Hoy"
    h_vaca    = [s["precios"].get("vaca",0)       for s in sess]
    h_novillo = [s["precios"].get("novillo",0)    for s in sess]
    h_vaqui   = [s["precios"].get("vaquillona",0) for s in sess]
    h_vol     = [s["precios"].get("volumen",0)    for s in sess]
    labels_js  = json.dumps(h_labels, ensure_ascii=False)
    vaca_js    = json.dumps(h_vaca)
    novillo_js = json.dumps(h_novillo)
    vaqui_js   = json.dumps(h_vaqui)
    vol_js     = json.dumps(h_vol)


    # Eficiencia HTML
    ef_sorted = sorted(eficiencia.items(), key=lambda x: x[1]["costo_mesa"])
    ef_html = ""
    for i,(cat,d) in enumerate(ef_sorted):
        mejor = '<span class="tag-mejor">Mejor hoy</span>' if d.get("mejor_opcion") else ""
        max_t = ""
        if "max_precio_equiv" in d:
            sgn = "+" if d["diff_pct"] >= 0 else ""
            max_t = f'<div class="ef-maxp">Máx equiv. vaca: <b>${d["max_precio_equiv"]:,}</b> ({sgn}{d["diff_pct"]}% vs precio actual)</div>'
        faena_pct = round(RINDE[cat]["faena"] * 100)
        mesa_pct  = round(RINDE[cat]["mesa"]  * 100, 1)
        ef_html += f"""<div class="ef-row {'ef-best' if i==0 else ''}">
          <div class="ef-meta">
            <span class="ef-nombre">{d['label']}</span>{mejor}
            <div class="ef-piep">Pie: <b>${d['precio_pie']:,}/kg</b> · faena {faena_pct}% · mesa {mesa_pct}%</div>
            {max_t}
          </div>
          <div class="ef-costo">${d['costo_mesa']:,}<span class="ef-unit">/kg mesa</span></div>
        </div>"""

    # Alertas HTML
    al_html = ""
    for a in alertas:
        al_html += f'<div class="alerta"><span class="alerta-dot alerta-alza"></span><span>{a["msg"]}</span></div>'
    if not al_html:
        al_html = '<div class="alerta"><span class="alerta-dot" style="background:var(--muted)"></span><span>Sin alertas climáticas esta semana</span></div>'

    # Supermercados HTML
    super_html = ""
    for corte,d in supermercados.items():
        src = "" if d["fuente"]=="live" else '<sup style="color:#bbb;font-size:9px"> ref</sup>'
        super_html += f"""<tr>
          <td class="td-corte">{corte}</td>
          <td>${d['coto']:,}{src}</td>
          <td>${d['carrefour']:,}{src}</td>
          <td>${d['disco']:,}{src}</td>
          <td class="td-prom">${d['promedio']:,}</td>
        </tr>"""

    # Factores tendencia
    fac_html = "".join(f"<li>{f}</li>" for f in tendencia["factores"]) \
               or "<li>Sin factores de presión identificados</li>"

    vol = precios["volumen"]
    vol_ctx = ("Volumen bajo → presión alcista" if vol < 13000
               else "Volumen alto → presión bajista" if vol > 17000
               else "Volumen normal")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte Hacienda — {fecha_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#F2F0EA;--surface:#fff;--border:rgba(0,0,0,.08);
  --text:#1A1A18;--muted:#88887F;
  --alza:#27500A;--alza-bg:#EAF3DE;
  --baja:#791F1F;--baja-bg:#FCEBEB;
  --warn:#633806;--warn-bg:#FAEEDA;
  --info:#0C447C;--info-bg:#E6F1FB;
  --r:14px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);
      font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:680px;margin:0 auto;padding:16px 14px 48px}}
.mono{{font-family:'DM Mono',monospace}}

.header{{background:var(--surface);border-radius:var(--r);padding:20px 22px;
          margin-bottom:12px;border:.5px solid var(--border);
          display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.header h1{{font-size:19px;font-weight:600;letter-spacing:-.3px}}
.header-sub{{font-size:13px;color:var(--muted);margin-top:2px}}
.next-badge{{font-size:11px;white-space:nowrap;background:var(--info-bg);color:var(--info);
              padding:5px 12px;border-radius:20px}}

.senal{{border-radius:var(--r);padding:20px 22px;margin-bottom:12px;border:.5px solid var(--border)}}
.senal-lbl{{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;opacity:.7;margin-bottom:5px}}
.senal-val{{font-size:30px;font-weight:700;letter-spacing:-.5px;margin-bottom:5px}}
.senal-rec{{font-size:14px;opacity:.85}}
.senal-sep{{border:none;border-top:.5px solid rgba(0,0,0,.12);margin:14px 0}}
.senal-fac{{font-size:13px;opacity:.85}}
.senal-fac ul{{padding-left:16px}}
.senal-fac li{{margin-bottom:5px}}

.sec{{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;
       color:var(--muted);margin:18px 0 10px}}
.card{{background:var(--surface);border-radius:var(--r);padding:18px 20px;
        margin-bottom:12px;border:.5px solid var(--border)}}

.pgrid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}}
.pcard{{background:var(--surface);border-radius:12px;padding:14px 16px;border:.5px solid var(--border)}}
.plabel{{font-size:12px;color:var(--muted);margin-bottom:2px}}
.pnum{{font-size:24px;font-weight:600;letter-spacing:-.5px}}
.psub{{font-size:11px;color:var(--muted)}}

.volcrd{{background:var(--surface);border-radius:12px;padding:14px 16px;
          border:.5px solid var(--border);margin-bottom:12px;
          display:flex;align-items:center;gap:14px}}
.volnum{{font-size:24px;font-weight:600}}
.voltag{{font-size:12px;background:var(--warn-bg);color:var(--warn);
          padding:3px 10px;border-radius:20px;margin-left:auto;white-space:nowrap}}

.chart-wrap{{position:relative;width:100%;height:200px}}
.chart-wrap2{{position:relative;width:100%;height:160px;margin-top:16px}}
.legend{{display:flex;gap:16px;margin-top:10px;flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted)}}
.ldot{{width:10px;height:10px;border-radius:2px;flex-shrink:0}}

.ef-row{{display:flex;justify-content:space-between;align-items:flex-start;
          padding:12px 0;border-bottom:.5px solid rgba(0,0,0,.06)}}
.ef-row:last-child{{border-bottom:none}}
.ef-best .ef-nombre{{color:var(--info)}}
.ef-nombre{{font-size:15px;font-weight:600}}
.ef-piep,.ef-maxp{{font-size:12px;color:var(--muted);margin-top:3px}}
.ef-costo{{font-size:22px;font-weight:600;text-align:right;white-space:nowrap}}
.ef-unit{{font-size:11px;font-weight:400;color:var(--muted);display:block;text-align:right}}
.tag-mejor{{font-size:10px;background:var(--alza-bg);color:var(--alza);
             padding:2px 8px;border-radius:10px;margin-left:7px;vertical-align:middle}}

.alerta{{display:flex;align-items:flex-start;gap:9px;padding:9px 0;
          border-bottom:.5px solid rgba(0,0,0,.06);font-size:13px;line-height:1.5}}
.alerta:last-child{{border-bottom:none}}
.alerta-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:4px}}
.alerta-alza{{background:var(--alza)}}

.regla{{display:flex;justify-content:space-between;align-items:center;
         padding:8px 10px;background:rgba(0,0,0,.03);border-radius:8px;
         margin-bottom:6px;font-size:13px;gap:8px}}
.rbadge{{font-size:11px;font-weight:500;padding:3px 9px;border-radius:10px;white-space:nowrap}}

table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{padding:6px 8px;text-align:left;color:var(--muted);font-weight:500;font-size:11px;
    border-bottom:.5px solid var(--border);text-transform:uppercase;letter-spacing:.04em}}
td{{padding:8px;border-bottom:.5px solid rgba(0,0,0,.05)}}
tr:last-child td{{border-bottom:none}}
.td-corte{{font-weight:500}}
.td-prom{{font-weight:600}}

.footer{{text-align:center;font-size:12px;color:var(--muted);padding-top:20px}}

@media(max-width:420px){{
  .pgrid{{grid-template-columns:repeat(2,1fr)}}
  .pnum,.volnum{{font-size:20px}}
  .ef-costo{{font-size:18px}}
  .header{{flex-direction:column;gap:8px}}
  .senal-val{{font-size:24px}}
}}
</style>
</head>
<body>
<div class="wrap">

<div class="header">
  <div>
    <h1>Reporte de hacienda</h1>
    <div class="header-sub">{fecha_str}</div>
  </div>
  <span class="next-badge">Próx.: {prox_nombre} {prox_fecha}</span>
</div>

<div class="senal" style="background:{tendencia['bg']}">
  <div class="senal-lbl" style="color:{tendencia['color']}">Señal para negociar</div>
  <div class="senal-val" style="color:{tendencia['color']}">{tendencia['icono']} Tendencia a la {tendencia['señal'].lower()}</div>
  <div class="senal-rec" style="color:{tendencia['color']}">{tendencia['rec']}</div>
  <hr class="senal-sep">
  <div class="senal-fac" style="color:{tendencia['color']}"><ul>{fac_html}</ul></div>
</div>

<div class="sec">Precios en pie — Mercado de Liniers</div>
<div class="pgrid">
  <div class="pcard">
    <div class="plabel">Vaca</div>
    <div class="pnum mono" style="color:#A32D2D">${precios['vaca']:,}</div>
    <div class="psub">$/kg vivo</div>
  </div>
  <div class="pcard">
    <div class="plabel">Novillo</div>
    <div class="pnum mono" style="color:#185FA5">${precios['novillo']:,}</div>
    <div class="psub">$/kg vivo</div>
  </div>
  <div class="pcard">
    <div class="plabel">Vaquillona</div>
    <div class="pnum mono" style="color:#3B6D11">${precios['vaquillona']:,}</div>
    <div class="psub">$/kg vivo</div>
  </div>
</div>

<div class="volcrd">
  <div>
    <div class="psub">Volumen de entrada</div>
    <div class="volnum mono">{vol:,} <span style="font-size:14px;font-weight:400;color:var(--muted)">cabezas</span></div>
  </div>
  <span class="voltag">{vol_ctx}</span>
</div>

<div class="card">
  <div class="sec" style="margin-top:0">Evolución de precios — últimas sesiones</div>
  <div class="chart-wrap">
    <canvas id="cP" role="img" aria-label="Evolución de precios de vaca, novillo y vaquillona">Tendencia de precios por sesión.</canvas>
  </div>
  <div class="legend">
    <span><span class="ldot" style="background:#E24B4A"></span>Vaca</span>
    <span><span class="ldot" style="outline:1.5px dashed #378ADD"></span>Novillo</span>
    <span><span class="ldot" style="background:#639922"></span>Vaquillona</span>
  </div>
  <div class="sec" style="margin-top:20px">Volumen de entrada (cabezas)</div>
  <div class="chart-wrap2">
    <canvas id="cV" role="img" aria-label="Volumen de hacienda ingresada por sesión">Volumen histórico de entrada.</canvas>
  </div>
</div>

<div class="sec">Eficiencia real — costo por kg a la mesa</div>
<div class="card">{ef_html}</div>

<div class="sec">Alertas de mercado</div>
<div class="card">
  {al_html}
  <hr style="border:none;border-top:.5px solid rgba(0,0,0,.07);margin:14px 0">
  <div class="sec" style="margin:0 0 10px">Reglas históricas detectadas</div>
  <div class="regla"><span>Lluvia zona pampeana</span><span class="rbadge" style="background:var(--alza-bg);color:var(--alza)">Suba +1.5–3%</span></div>
  <div class="regla"><span>Fin de semana largo</span><span class="rbadge" style="background:var(--alza-bg);color:var(--alza)">Suba +2–4%</span></div>
  <div class="regla"><span>Volumen &lt;10.000 cab.</span><span class="rbadge" style="background:var(--alza-bg);color:var(--alza)">Suba +2–5%</span></div>
  <div class="regla"><span>Volumen &gt;18.000 cab.</span><span class="rbadge" style="background:var(--baja-bg);color:var(--baja)">Baja −1–3%</span></div>
  <div class="regla"><span>Post-lluvia + sol</span><span class="rbadge" style="background:var(--baja-bg);color:var(--baja)">Baja −0.5–2%</span></div>
</div>

<div class="sec">Precios en góndola — supermercados ($/kg)</div>
<div class="card">
  <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Corte</th><th>Coto</th><th>Carrefour</th><th>Disco</th><th>Promedio</th></tr></thead>
      <tbody>{super_html}</tbody>
    </table>
  </div>
  <div style="font-size:11px;color:var(--muted);margin-top:10px"><sup>ref</sup> = referencia · live = scrapeado hoy</div>
</div>

<div class="footer">Datos: Mercado de Liniers · Open-Meteo · Actualizado automáticamente martes y viernes</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const L={labels_js};
const V={vaca_js}, N={novillo_js}, Q={vaqui_js}, O={vol_js};
const gc='rgba(0,0,0,.06)', tc='#88887F';
const fp=v=>'$'+v.toLocaleString('es-AR');

new Chart(document.getElementById('cP'),{{
  type:'line',
  data:{{labels:L,datasets:[
    {{label:'Vaca',data:V,borderColor:'#E24B4A',backgroundColor:'transparent',
      borderWidth:2,pointRadius:3,pointBackgroundColor:'#E24B4A',tension:.35}},
    {{label:'Novillo',data:N,borderColor:'#378ADD',backgroundColor:'transparent',
      borderWidth:2,borderDash:[5,3],pointRadius:3,pointStyle:'triangle',
      pointBackgroundColor:'#378ADD',tension:.35}},
    {{label:'Vaquillona',data:Q,borderColor:'#639922',backgroundColor:'transparent',
      borderWidth:2,pointRadius:3,pointBackgroundColor:'#639922',tension:.35}},
  ]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{
      x:{{ticks:{{font:{{size:11}},color:tc,autoSkip:true,maxRotation:35}},grid:{{display:false}}}},
      y:{{ticks:{{font:{{size:11}},color:tc,callback:fp}},grid:{{color:gc}}}}
    }}}}
}});

new Chart(document.getElementById('cV'),{{
  type:'bar',
  data:{{labels:L,datasets:[{{
    label:'Cabezas',data:O,
    backgroundColor:O.map(v=>v<13000?'rgba(231,75,74,.55)':v>17000?'rgba(55,138,221,.55)':'rgba(136,135,128,.45)'),
    borderRadius:4,borderWidth:0
  }}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{
      x:{{ticks:{{font:{{size:11}},color:tc,autoSkip:true,maxRotation:35}},grid:{{display:false}}}},
      y:{{ticks:{{font:{{size:11}},color:tc,callback:v=>(v/1000).toFixed(0)+'k'}},grid:{{color:gc}}}}
    }}}}
}});
</script>
</body>
</html>"""


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("="*60)
    print("REPORTE HACIENDA — inicio")
    print("="*60)

    historial     = cargar_historial()
    precios       = scrape_liniers()
    supermercados = scrape_supermercados()
    alertas       = get_clima()
    eficiencia    = calcular_eficiencia(precios)
    tendencia     = calcular_tendencia(precios, precios["volumen"], alertas, historial)
    historial     = guardar_historial(historial, precios, tendencia)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html = generar_html(precios, eficiencia, supermercados, alertas, tendencia, historial)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nOK reporte -> {OUTPUT_HTML}")
    print(f"   precios: vaca={precios['vaca']} novillo={precios['novillo']} vaquillona={precios['vaquillona']}")
    print(f"   volumen: {precios['volumen']:,} cab.")
    print(f"   señal:   {tendencia['señal']}")
    print("="*60)

if __name__ == "__main__":
    main()
