// The weight-and-deficit chart (unit 017, asked by Andrés on 2026-10-22), in the Balance tab (4 weeks, 3 months,
// everything or between two dates). Plain ES module, own SVG, no library:
// a library would have to be copied into static/ and the service worker, would draw in <canvas> (the E2E
// could not look at its bars and labels) and would keep its dark mode apart from estilos.css.
//
// Two panels in ONE <svg> with the same X axis: on top the kg (a dot per weight, the 7-day mean line and the
// weight goal dashed, always inside the scale), below the kcal (a bar per day with the food closed, deficit
// down and surplus up from the 0 line, today open striped). Every Monday has a mark; the labels are written
// every 1, 2 or 4 weeks, the smallest that leaves 44 px or more between two of them, so they never overlap.
// The server does every sum; this only draws.
// Unit 027: `{ ver, eje }` — `ver` "peso" | "dos" | "deficit" draws only that panel (alone it takes the whole
// height); `eje` "kcal" | "pct" — with "pct" each bar measures `balance_pct` (the balance ÷ what was spent that
// day, or the week's by week) on a % axis, and a bar without `balance_pct` is not drawn (never an invented 0).

const ejeKg = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const kilos = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const porCiento = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
const SEPARACION_MIN = 44; // px between two X labels (R12)
const ANCHO_POR_DEFECTO = 348; // the inner width of a chart card at 390 px
const PASOS_KCAL = [100, 200, 250, 500, 1000, 2000, 5000];
const PASOS_PCT = [5, 10, 20, 25, 50, 100];
let chartId = 0; // each chart its own pattern id: a hidden tab's <svg> must not lend it to another

const comoFecha = (iso) => new Date(`${iso}T12:00:00`);
const diasEntre = (desde, hasta) => Math.round((comoFecha(hasta) - comoFecha(desde)) / 86_400_000);
const sumar = (iso, n) => { const d = comoFecha(iso); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const kcalTexto = (n) => (Math.round(n) < 0 ? "−" : Math.round(n) > 0 ? "+" : "") + enteras.format(Math.abs(Math.round(n)));
const signoDe = (n) => (n < 0 ? "−" : n > 0 ? "+" : "");
const pctTexto = (n) => `${signoDe(n)}${porCiento.format(Math.abs(n))} %`;
const etiquetaDia = (iso) => diaMes.format(comoFecha(iso)).replace(".", "");
const f1 = (n) => n.toFixed(1);
const mayus = (t) => t.charAt(0).toUpperCase() + t.slice(1);

function escala(min, max, arriba, abajo) {
  return (v) => abajo - ((v - min) / (max - min || 1)) * (abajo - arriba);
}

// The 0 line and the steps of the kcal axis: at most 4 gaps, always through 0.
function rangoKcal(valores) {
  let min = Math.min(0, ...valores);
  let max = Math.max(0, ...valores);
  if (max - min < 200) { min = Math.min(min, -250); max = Math.max(max, 250); }
  const paso = PASOS_KCAL.find((p) => Math.ceil(max / p) - Math.floor(min / p) <= 4) ?? 10_000;
  return { min: Math.floor(min / paso) * paso, max: Math.ceil(max / paso) * paso, paso };
}

// Unit 027: the same for the % axis.
function rangoPct(valores) {
  let min = Math.min(0, ...valores);
  let max = Math.max(0, ...valores);
  if (max - min < 10) { min = Math.min(min, -5); max = Math.max(max, 5); }
  const paso = PASOS_PCT.find((p) => Math.ceil(max / p) - Math.floor(min / p) <= 4) ?? 200;
  return { min: Math.floor(min / paso) * paso, max: Math.ceil(max / paso) * paso, paso };
}

function rangoKg(valores) {
  let min = Math.floor(Math.min(...valores) - 0.3);
  let max = Math.ceil(Math.max(...valores) + 0.3);
  if (max - min < 2) { min -= 1; max += 1; }
  const paso = max - min <= 4 ? 1 : max - min <= 10 ? 2 : 5;
  return { min, max, paso };
}

// `datos`: {pesos, medias, barras, objetivo, desde, hasta, agrupado} as /api/peso or /api/balance give them;
// `ancho`: the width the <svg> will have on screen (1 unit = 1 px); `ver` and `eje` (unit 027) what to draw and
// on which axis. Returns the <svg> element.
export function graficaPesoDeficit(datos, ancho = ANCHO_POR_DEFECTO, { ver = "dos", eje = "kcal" } = {}) {
  const W = Math.max(260, Math.round(ancho || ANCHO_POR_DEFECTO));
  const x0 = 40, x1 = W - 8;
  const conPeso = ver !== "deficit", conDeficit = ver !== "peso";
  const enPct = eje === "pct";
  const kg = conDeficit ? { arriba: 22, abajo: 132 } : { arriba: 22, abajo: 232 };
  const kc = conPeso ? { arriba: 166, abajo: 262 } : { arriba: 22, abajo: 232 };
  const abajoX = conDeficit ? kc.abajo : kg.abajo;
  const H = abajoX + 30;
  const n = diasEntre(datos.desde, datos.hasta) + 1;
  const hueco = (x1 - x0) / n;
  const indice = (iso) => diasEntre(datos.desde, iso);
  const xCentro = (iso) => x0 + (indice(iso) + 0.5) * hueco;
  const id = `rayado-${++chartId}`;
  const partes = [`<defs><pattern id="${id}" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
    + `<rect class="rayado-fondo" width="5" height="5"/><line class="rayado-linea" x1="0" y1="0" x2="0" y2="5"/></pattern></defs>`];

  if (conPeso) panelKg();
  if (conDeficit) panelDeficit();

  // -- top panel (or the only one): kg
  function panelKg() {
    partes.push(`<text class="titulo-eje" x="4" y="12">kg</text>`);
    const valores = [...datos.pesos.map((p) => p.kg), ...datos.medias.map((m) => m.kg)];
    if (datos.objetivo) valores.push(datos.objetivo.kg);
    if (valores.length) {
      const r = rangoKg(valores);
      const y = escala(r.min, r.max, kg.arriba, kg.abajo);
      for (let v = Math.ceil(r.min / r.paso) * r.paso; v <= r.max; v += r.paso) {
        partes.push(`<line class="rejilla" x1="${x0}" y1="${f1(y(v))}" x2="${x1}" y2="${f1(y(v))}"/>`,
          `<text class="eje" x="${x0 - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${ejeKg.format(v)}</text>`);
      }
      if (datos.objetivo) {
        const yo = f1(y(datos.objetivo.kg));
        partes.push(`<line class="linea-objetivo" x1="${x0}" y1="${yo}" x2="${x1}" y2="${yo}"/>`);
      }
      if (datos.medias.length) {
        const puntos = datos.medias.map((m) => `${f1(xCentro(m.dia))},${f1(y(m.kg))}`).join(" ");
        partes.push(`<polyline class="linea-media" points="${puntos}"/>`);
      }
      if (datos.agrupado !== "semana") { // by week there is no dot per weight: only the mean line
        datos.pesos.forEach((p, i) => {
          const ultimo = i === datos.pesos.length - 1;
          partes.push(`<circle class="punto-peso${ultimo ? " ultimo" : ""}" cx="${f1(xCentro(p.dia))}" cy="${f1(y(p.kg))}" r="${ultimo ? 4.5 : 3}"><title>${etiquetaDia(p.dia)}: ${kilos.format(p.kg)} kg</title></circle>`);
        });
      }
    } else {
      partes.push(`<text class="eje" x="${(x0 + x1) / 2}" y="${(kg.arriba + kg.abajo) / 2}" text-anchor="middle">Aún no hay pesos en estos días</text>`);
    }
  }

  // -- bottom panel (or the only one): the balance, in kcal or in % of what was spent
  function panelDeficit() {
    partes.push(`<text class="titulo-eje" x="4" y="${kc.arriba - 10}">${enPct ? "%" : "kcal"}</text>`);
    const valorDe = (b) => (enPct ? b.balance_pct : b.balance_kcal);
    const barras = datos.barras.filter((b) => valorDe(b) !== null && valorDe(b) !== undefined);
    const r = (enPct ? rangoPct : rangoKcal)(barras.map(valorDe));
    const y = escala(r.min, r.max, kc.arriba, kc.abajo);
    for (let v = r.min; v <= r.max; v += r.paso) {
      if (v !== 0) partes.push(`<line class="rejilla" x1="${x0}" y1="${f1(y(v))}" x2="${x1}" y2="${f1(y(v))}"/>`);
      const texto = v === 0 ? "0" : enPct ? pctTexto(v) : kcalTexto(v);
      partes.push(`<text class="eje" x="${x0 - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${texto}</text>`);
    }
    for (const b of barras) {
      const desde = b.desde && b.desde < datos.desde ? datos.desde : (b.desde ?? b.dia);
      const hasta = b.hasta && b.hasta > datos.hasta ? datos.hasta : (b.hasta ?? b.dia);
      const ancho = (indice(hasta) - indice(desde) + 1) * hueco;
      const w = Math.max(1.5, ancho * (b.hasta ? 0.82 : 0.7));
      const x = x0 + indice(desde) * hueco + (ancho - w) / 2;
      const v = valorDe(b);
      const alto = Math.max(1.5, Math.abs(y(v) - y(0)));
      const top = v < 0 ? y(0) : y(0) - alto;
      const clase = `barra-kcal ${v < 0 ? "barra-deficit" : "barra-superavit"}${b.provisional ? " provisional" : ""}`;
      const kcal = b.balance_kcal;
      const que = b.hasta ? `semana del ${etiquetaDia(b.desde)}: media ${kcalTexto(kcal)} kcal/día` : `${etiquetaDia(b.dia)}: ${kcalTexto(kcal)} kcal${b.provisional ? " (hoy, provisional)" : ""}`;
      const pct = b.balance_pct === null || b.balance_pct === undefined ? "" : ` (${pctTexto(b.balance_pct)}${enPct ? " de lo gastado" : ""})`;
      partes.push(`<rect class="${clase}" data-dia="${b.dia}" data-valor="${v}" x="${f1(x)}" y="${f1(top)}" width="${f1(w)}" height="${f1(alto)}"${b.provisional ? ` fill="url(#${id})"` : ""}><title>${que}${pct}</title></rect>`);
    }
    partes.push(`<line class="linea-cero" x1="${x0}" y1="${f1(y(0))}" x2="${x1}" y2="${f1(y(0))}"/>`);
  }

  // -- the X axis: a mark every Monday, a label every 1, 2 or 4 weeks
  const lunes = [];
  for (let i = 0; i < n; i++) {
    const iso = sumar(datos.desde, i);
    if (comoFecha(iso).getDay() === 1) lunes.push(iso);
  }
  const cada = [1, 2, 4].find((k) => 7 * k * hueco >= SEPARACION_MIN) ?? 8;
  partes.push(`<line class="eje-x" x1="${x0}" y1="${abajoX}" x2="${x1}" y2="${abajoX}"/>`);
  lunes.forEach((iso, i) => {
    const x = x0 + indice(iso) * hueco;
    partes.push(`<line class="marca-x" x1="${f1(x)}" y1="${abajoX}" x2="${f1(x)}" y2="${abajoX + 5}"/>`);
    if (i % cada === 0) {
      const mitad = 18; // about half a «28 sept» at 11 px: a label never leaves the drawing
      const xt = Math.min(Math.max(x, mitad), W - mitad);
      partes.push(`<text class="etiqueta-x" x="${f1(xt)}" y="${abajoX + 18}" text-anchor="middle">${etiquetaDia(iso)}</text>`);
    }
  });

  const lienzo = document.createElement("div");
  const deficit = enPct ? "déficit (% de lo gastado)" : "balance diario (kcal)";
  const etiqueta = !conDeficit ? "Peso (kg) por fechas" : !conPeso ? `${mayus(deficit)} por fechas` : `Peso (kg) arriba y ${deficit} abajo, por fechas`;
  lienzo.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${etiqueta}">${partes.join("")}</svg>`;
  return lienzo.firstChild;
}

// The legend, with text and not only colour (R12); unit 027: only what is drawn.
export function leyendaPesoDeficit(objetivo, { ver = "dos", eje = "kcal" } = {}) {
  const p = document.createElement("p");
  p.className = "leyenda-grafica";
  const trozos = [];
  if (ver !== "deficit") {
    trozos.push(["ley-peso", "●", "peso"], ["ley-media", "—", "media 7 d"]);
    if (objetivo) trozos.push(["ley-objetivo", "┄", `objetivo ${kilos.format(objetivo.kg)} kg`]);
  }
  if (ver !== "peso") {
    trozos.push(["ley-deficit", "▮", "déficit"], ["ley-superavit", "▮", "superávit"]);
    if (eje === "pct") trozos.push([null, "", "en % de lo gastado ese día"]);
  }
  trozos.forEach(([clase, marca, texto], i) => {
    if (i) p.append(" · ");
    if (!clase) { p.append(texto); return; }
    const s = document.createElement("span");
    s.className = clase;
    s.textContent = marca;
    p.append(s, ` ${texto}`);
  });
  return p;
}
