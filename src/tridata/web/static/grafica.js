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

// Unit 031: the load of the Fitness tab — the days that end today (`serie` of GET /api/fitness, each
// {fecha, ctl, atl, tsb}). On top the form (CTL, solid line) and the fatigue (ATL, dashed); below the freshness
// (TSB) as a bar per day from its 0 line. The same SVG, axes and colours as the weight chart; the server computes.
// Unit 090: 14 days, 6 weeks, 3 months or 1 year; `periodo` names it in the label ("de las últimas 6 semanas").
export function graficaCarga(serie, ancho = ANCHO_POR_DEFECTO, periodo = "de los últimos 14 días") {
  const W = Math.max(260, Math.round(ancho || ANCHO_POR_DEFECTO));
  const x0 = 34, x1 = W - 8;
  const arriba = { arriba: 14, abajo: 120 };
  const abajo = { arriba: 136, abajo: 196 };
  const H = abajo.abajo + 26;
  const n = Math.max(serie.length, 1);
  const hueco = (x1 - x0) / n;
  const xCentro = (i) => x0 + (i + 0.5) * hueco;
  const partes = [];
  if (!serie.length) {
    partes.push(`<text class="eje" x="${(x0 + x1) / 2}" y="${H / 2}" text-anchor="middle">Aún no hay entrenos con carga</text>`);
  } else {
    const valores = serie.flatMap((p) => [p.ctl, p.atl]);
    let min = Math.floor(Math.min(...valores) / 10) * 10;
    let max = Math.ceil(Math.max(...valores) / 10) * 10;
    if (max - min < 20) max = min + 20;
    const paso = max - min <= 40 ? 10 : max - min <= 100 ? 20 : 50;
    const y = escala(min, max, arriba.arriba, arriba.abajo);
    for (let v = min; v <= max; v += paso) {
      partes.push(`<line class="rejilla" x1="${x0}" y1="${f1(y(v))}" x2="${x1}" y2="${f1(y(v))}"/>`,
        `<text class="eje" x="${x0 - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${enteras.format(v)}</text>`);
    }
    const linea = (clave) => serie.map((p, i) => `${f1(xCentro(i))},${f1(y(p[clave]))}`).join(" ");
    partes.push(`<polyline class="linea-atl" points="${linea("atl")}"/>`, `<polyline class="linea-ctl" points="${linea("ctl")}"/>`);

    const tope = Math.max(5, ...serie.map((p) => Math.abs(p.tsb)));
    const yb = escala(-tope, tope, abajo.arriba, abajo.abajo);
    const w = Math.max(Math.min(2, hueco), hueco * 0.6); // a year: under a pixel per day, the bars touch
    serie.forEach((p, i) => {
      const alto = Math.max(1.5, Math.abs(yb(p.tsb) - yb(0)));
      const top = p.tsb < 0 ? yb(0) : yb(0) - alto;
      const signo = p.tsb > 0 ? "+" : p.tsb < 0 ? "−" : "";
      partes.push(`<rect class="barra-tsb${p.tsb < 0 ? " negativa" : ""}" data-dia="${p.fecha}" x="${f1(xCentro(i) - w / 2)}" y="${f1(top)}" width="${f1(w)}" height="${f1(alto)}"><title>${etiquetaDia(p.fecha)}: forma ${enteras.format(p.ctl)}, fatiga ${enteras.format(p.atl)}, frescura ${signo}${enteras.format(Math.abs(p.tsb))}</title></rect>`);
    });
    partes.push(`<line class="linea-cero" x1="${x0}" y1="${f1(yb(0))}" x2="${x1}" y2="${f1(yb(0))}"/>`,
      `<text class="eje" x="${x0 - 6}" y="${f1(yb(0) + 4)}" text-anchor="end">0</text>`);
    const cada = Math.max(1, Math.ceil(SEPARACION_MIN / hueco));
    serie.forEach((p, i) => {
      if ((serie.length - 1 - i) % cada !== 0) return; // always today's label, the rest every `cada` days
      const xt = Math.min(Math.max(xCentro(i), 18), W - 18);
      partes.push(`<text class="etiqueta-x" x="${f1(xt)}" y="${abajo.abajo + 18}" text-anchor="middle">${etiquetaDia(p.fecha)}</text>`);
    });
  }
  const lienzo = document.createElement("div");
  lienzo.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Forma y fatiga arriba, frescura abajo, ${periodo}">${partes.join("")}</svg>`;
  return lienzo.firstChild;
}

export function leyendaCarga() {
  const p = document.createElement("p");
  p.className = "leyenda-grafica";
  [["ley-ctl", "—", "forma (CTL)"], ["ley-atl", "┄", "fatiga (ATL)"], ["ley-tsb", "▮", "frescura (TSB)"]].forEach(([clase, marca, texto], i) => {
    if (i) p.append(" · ");
    const s = document.createElement("span");
    s.className = clase;
    s.textContent = marca;
    p.append(s, ` ${texto}`);
  });
  return p;
}

// Unit 090 (R-234): the weeks of Fitness, Monday to Sunday (`semanas` of GET /api/fitness). `medida`: "horas" | "km" |
// "carga". Each week is a <g class="semana"> with its Monday; the week in course is `.en-curso` and says «en curso» under
// it. Strength has no km: the server sends `km` null. Unit 091 (R-237, R-238): `deporte` "todos" | "correr" | "bici" |
// "nadar" | "fuerza": with one sport each week is that sport's figure, with «todos» the total; strength in km draws no
// line and says that strength adds no km. Unit 093 (R-239, S-83): each week is a button (focusable, Enter/Space,
// aria-pressed) whose touch area is its whole column (`.toque-semana`), so a small point is still easy to tap;
// `elegida` (a Monday) is the week whose panel is open (`.elegida`). fitness.js listens to the taps on the chart.
// Unit 108 (REC-51, R-310..R-316): no more bars stacked by sport — ONE line as Strava's: a point per week
// (`.punto-semana`, with its `data-valor`) joined by `path.linea-semanas`, with `path.area-semanas` filled under it; a
// week without sessions is a point at 0. `marcada` (a Monday: the panel's, or the week in course when Fitness opens)
// carries a vertical line (`.raya-semana`). `porZonas` (hours or km, never load) puts under each point a bar of its
// height split by zone (`.barra-zona`, Z1..Z5 and «sin pulso»: the seconds of `zonas`, or the km of `zonas_km`).
// `otra` (the weeks of a second period) draws it as a second line (`.serie-b`: dashed, hollow points; the first is
// `.serie-a`) aligned by week number: the X axis says «sem 1, sem 2…», each column is a <g class="semana" data-n> and
// `largo` is how many weeks the longer period has (its weeks still to come are not drawn: the line stops); `numero`
// (N) marks that column.
export const CAPAS_ZONA = [["z1", "Z1"], ["z2", "Z2"], ["z3", "Z3"], ["z4", "Z4"], ["z5", "Z5"], ["sin_pulso", "sin pulso"]];
const UNIDAD_SEMANA = { horas: "h", km: "km", carga: "carga" };
const PASOS_SEMANA = [0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
const dosDecimalesSemana = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });

// The figures of a week for `deporte`: the week's total or that sport's (unit 091).
export const delDeporte = (s, deporte) => (deporte === "todos" ? s.total : s.deportes[deporte]) ?? {};

// The value of a week in `medida` (0 for strength in km: it has none).
export function valorSemana(s, medida, deporte = "todos") {
  return delDeporte(s, deporte)[medida] ?? 0;
}

// Unit 108 (R-311): the parts of the bar of zones of a week — `[[zona, valor]…]` scaled so that they add up to the
// value of the week (hours: the seconds in each zone; km: the km in each zone).
export function zonasSemana(s, medida, deporte = "todos") {
  const d = delDeporte(s, deporte);
  const por = medida === "km" ? d.zonas_km : d.zonas;
  const total = valorSemana(s, medida, deporte);
  if (!por || !total) return [];
  const suma = Object.values(por).reduce((a, b) => a + b, 0);
  if (!suma) return [];
  return CAPAS_ZONA.map(([clave]) => [clave, (por[clave] ?? 0) * total / suma]).filter(([, v]) => v > 0);
}

const textoMedida = (v, medida) => (medida === "carga" ? enteras.format(Math.round(v)) : dosDecimalesSemana.format(v));
const conDos = (v) => String(Math.round(v * 100) / 100);

export function graficaSemanas(semanas, medida = "horas", ancho = ANCHO_POR_DEFECTO, deporte = "todos", elegida = null,
  { porZonas = false, otra = null, largo = null, marcada = null, numero = null } = {}) {
  const W = Math.max(260, Math.round(ancho || ANCHO_POR_DEFECTO));
  const x0 = 34, x1 = W - 8, arriba = 24, abajo = 186;
  const H = abajo + 44; // unit 107: «en curso» goes on a second line, under the date of its week
  const comparando = Array.isArray(otra);
  const n = comparando ? Math.max(largo ?? 0, semanas.length, otra.length) : semanas.length;
  const hueco = (x1 - x0) / Math.max(n, 1);
  const fuerzaSinKm = deporte === "fuerza" && medida === "km";
  const zonas = porZonas && medida !== "carga" && !fuerzaSinKm;
  const deQuien = deporte === "todos" ? "" : ` de ${deporte}`;
  const valor = (s) => (fuerzaSinKm ? 0 : valorSemana(s, medida, deporte));
  const valoresA = semanas.map(valor), valoresB = comparando ? otra.map(valor) : [];
  const maximo = Math.max(0, ...valoresA, ...valoresB);
  const paso = PASOS_SEMANA.find((p) => Math.ceil(maximo / p) <= 4) ?? 10_000;
  const tope = Math.max(paso, Math.ceil(maximo / paso) * paso);
  const y = escala(0, tope, arriba, abajo);
  const centro = (i) => x0 + (i + 0.5) * hueco;
  const partes = [`<text class="titulo-eje" x="4" y="12">${UNIDAD_SEMANA[medida]}</text>`];
  for (let v = 0; v <= tope + 1e-9; v += paso) {
    partes.push(`<line class="${v ? "rejilla" : "linea-cero"}" x1="${x0}" y1="${f1(y(v))}" x2="${x1}" y2="${f1(y(v))}"/>`,
      `<text class="eje" x="${x0 - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${ejeKg.format(v)}</text>`);
  }
  // the line (and the area of the first series) under the columns, so that the points stay on top
  const camino = (valores) => valores.map((v, i) => `${i ? "L" : "M"}${f1(centro(i))},${f1(y(v))}`).join("");
  if (!fuerzaSinKm && valoresA.length) {
    const linea = camino(valoresA);
    partes.push(`<path class="area-semanas serie-a" d="${linea}L${f1(centro(valoresA.length - 1))},${f1(y(0))}L${f1(centro(0))},${f1(y(0))}Z"/>`,
      `<path class="linea-semanas serie-a" d="${linea}"/>`);
  }
  if (!fuerzaSinKm && comparando && valoresB.length) partes.push(`<path class="linea-semanas serie-b" d="${camino(valoresB)}"/>`);
  const cada = Math.max(1, Math.ceil(SEPARACION_MIN / hueco));
  const xCurso = (i) => Math.min(Math.max(centro(i), 24), W - 24); // «en curso», kept inside
  const r = Math.max(2.5, Math.min(4.5, hueco / 4));
  const ancho1 = comparando ? Math.max(1.5, Math.min(10, hueco * 0.32)) : Math.max(2, Math.min(14, hueco * 0.5));

  const barraZonas = (s, xc, clase) => {
    let base = 0;
    return zonasSemana(s, medida, deporte).map(([clave, v]) => {
      const top = y(base + v), bajo = y(base);
      base += v;
      const nombre = CAPAS_ZONA.find(([c]) => c === clave)[1];
      return `<rect class="barra-zona capa-${clave}${clase}" data-zona="${clave}" data-valor="${conDos(v)}" x="${f1(xc - ancho1 / 2)}" y="${f1(top)}" width="${f1(ancho1)}" height="${f1(Math.max(0.5, bajo - top))}"><title>${nombre}: ${textoMedida(v, medida)} ${UNIDAD_SEMANA[medida]}</title></rect>`;
    });
  };
  const punto = (s, i, serie) => {
    const v = valor(s);
    return `<circle class="punto-semana serie-${serie}" data-valor="${conDos(v)}" cx="${f1(centro(i))}" cy="${f1(y(v))}" r="${f1(r)}"/>`;
  };
  const resumenDe = (s) => `Semana del ${etiquetaDia(s.lunes)} al ${etiquetaDia(s.domingo)}${s.en_curso ? " (en curso)" : ""}${deQuien}: ${fuerzaSinKm ? "la fuerza no suma km" : `${textoMedida(valor(s), medida)} ${UNIDAD_SEMANA[medida]}`}`;

  for (let i = 0; i < n; i++) {
    const s = semanas[i] ?? null, o = comparando ? otra[i] ?? null : null;
    const dentro = [`<rect class="toque-semana" x="${f1(x0 + i * hueco)}" y="${arriba - 16}" width="${f1(hueco)}" height="${abajo - arriba + 40}"/>`];
    const conRaya = comparando ? numero === i + 1 : s && s.lunes === marcada;
    if (conRaya) dentro.push(`<line class="raya-semana" x1="${f1(centro(i))}" y1="${arriba - 8}" x2="${f1(centro(i))}" y2="${abajo}"/>`);
    if (zonas) {
      const dx = comparando ? ancho1 * 0.6 : 0;
      if (s) dentro.push(...barraZonas(s, centro(i) - dx, comparando ? " serie-a" : ""));
      if (o) dentro.push(...barraZonas(o, centro(i) + dx, " serie-b"));
    }
    if (!fuerzaSinKm) {
      if (o) dentro.push(punto(o, i, "b"));
      if (s) dentro.push(punto(s, i, "a"));
    }
    let resumen;
    if (comparando) {
      const partesResumen = [s ? resumenDe(s) : "este periodo no tiene esta semana", o ? resumenDe(o) : "el otro periodo no llega"];
      resumen = `Sem ${i + 1}: ${partesResumen.join(" · ")}`;
      if (i % cada === 0) {
        const xt = Math.min(Math.max(centro(i), 18), W - 18);
        dentro.push(`<text class="etiqueta-x" x="${f1(xt)}" y="${abajo + 18}" text-anchor="middle">sem ${i + 1}</text>`);
      }
    } else {
      resumen = resumenDe(s);
      // Unit 107 (R1): every week of the cadence keeps its date (counted back from the one in course)
      if ((semanas.length - 1 - i) % cada === 0) {
        const xt = Math.min(Math.max(centro(i), 18), W - 18);
        dentro.push(`<text class="etiqueta-x" x="${f1(xt)}" y="${abajo + 18}" text-anchor="middle">${etiquetaDia(s.lunes)}</text>`);
      }
    }
    if (s?.en_curso) dentro.push(`<text class="etiqueta-x etiqueta-en-curso" x="${f1(xCurso(i))}" y="${abajo + 36}" text-anchor="middle">en curso</text>`);
    dentro.unshift(`<title>${resumen}</title>`);
    const pulsada = comparando ? numero === i + 1 : Boolean(s) && s.lunes === elegida;
    const atributos = comparando
      ? `data-n="${i + 1}" data-lunes="${s?.lunes ?? ""}" data-lunes-otro="${o?.lunes ?? ""}"`
      : `data-lunes="${s.lunes}"`;
    const clases = `semana${s?.en_curso ? " en-curso" : ""}${pulsada ? " elegida" : ""}${conRaya ? " marcada" : ""}`;
    partes.push(`<g class="${clases}" ${atributos} tabindex="0" role="button" aria-pressed="${pulsada}" aria-label="${resumen}. ${comparando ? "Ver las dos semanas" : "Ver sus actividades"}">${dentro.join("")}</g>`);
  }
  if (fuerzaSinKm) {
    partes.push(`<text class="eje" x="${(x0 + x1) / 2}" y="${(arriba + abajo) / 2}" text-anchor="middle">La fuerza no suma km</text>`);
  } else if (!maximo) {
    partes.push(`<text class="eje" x="${(x0 + x1) / 2}" y="${(arriba + abajo) / 2}" text-anchor="middle">Sin entrenos${deQuien} en estas semanas</text>`);
  }
  const lienzo = document.createElement("div");
  const que = { horas: "horas", km: "kilómetros", carga: "carga" }[medida];
  lienzo.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Tus semanas de lunes a domingo: ${que}${deQuien} por semana${comparando ? ", dos periodos" : ""}${zonas ? ", por zonas" : ""}">${partes.join("")}</svg>`;
  return lienzo.firstChild;
}

// The legend of the weeks, with text and not only colour (unit 091: only what is drawn). Unit 094 (R-253):
// «aproximado por vueltas» only while `aproximado`. Unit 108: the zones only «por zonas» (never with «carga»), and
// comparing, the two periods by their dates — the first a solid line with full points, the other dashed and hollow.
export function leyendaSemanas(medida = "horas", deporte = "todos", aproximado = true, { porZonas = false, periodos = null } = {}) {
  const p = document.createElement("p");
  p.className = "leyenda-grafica leyenda-semanas";
  const trozos = [];
  if (periodos) {
    trozos.push(["ley-serie-a", "—●", periodos[0]], ["ley-serie-b", "┄○", periodos[1]]);
  }
  const fuerzaSinKm = deporte === "fuerza" && medida === "km";
  if (porZonas && medida !== "carga" && !fuerzaSinKm) {
    for (const [clave, texto] of CAPAS_ZONA) trozos.push([`ley-capa capa-${clave}`, "■", texto]);
  }
  if (!periodos && !trozos.length) trozos.push(["ley-serie-a", "—●", `${{ horas: "horas", km: "km", carga: "carga" }[medida]} por semana${deporte === "todos" ? "" : ` de ${deporte}`}`]);
  trozos.forEach(([clase, marca, texto], i) => {
    if (i) p.append(" · ");
    const s = document.createElement("span");
    s.className = clase;
    s.textContent = marca;
    s.ariaHidden = "true";
    p.append(s, ` ${texto}`);
  });
  if (medida === "km" && (deporte === "todos" || deporte === "fuerza")) p.append(trozos.length ? " · la fuerza no suma km" : "la fuerza no suma km");
  if (porZonas && medida !== "carga" && !fuerzaSinKm && aproximado) p.append(" · aproximado por vueltas");
  return p;
}

// Unit 094 (R-250, R-251): the HR of one session second by second (`curva` of GET /api/actividad/ID: the curve
// already reduced to draw, `fin_s`, `limites` of the zones of that day and the `tramos` of its laps). On top the line of
// the HR over five bands, one per zone (always the five: the scale reaches from Z2's start down and Z5's start up);
// under it a strip with each lap's segment in the colour of the zone of its mean HR. `elegida` (a lap number) is
// highlighted: its segment outlined and its stretch shaded over the curve. One <svg role="group">, like the weeks.
const TIEMPOS_EJE = [60, 120, 300, 600, 900, 1200, 1800, 3600, 7200];

export function graficaPulso(curva, ancho = ANCHO_POR_DEFECTO, elegida = null) {
  const W = Math.max(260, Math.round(ancho || ANCHO_POR_DEFECTO));
  const x0 = 34, x1 = W - 8, arriba = 22, abajo = 158, franja = 166, altoFranja = 14;
  const H = franja + altoFranja + 24;
  const tramos = curva.tramos ?? [];
  const fin = Math.max(1, curva.fin_s || 0, ...tramos.map((v) => v.fin_s));
  const x = (s) => x0 + (Math.min(Math.max(s, 0), fin) / fin) * (x1 - x0);
  const con = curva.pulso.filter((p) => p !== null);
  const limites = curva.limites;
  let lo = Math.min(...con), hi = Math.max(...con);
  if (limites) { lo = Math.min(lo, limites[1][0]); hi = Math.max(hi, limites[4][0]); }
  lo = Math.floor((lo - 8) / 10) * 10;
  hi = Math.ceil((hi + 8) / 10) * 10;
  const y = escala(lo, hi, arriba, abajo);
  const partes = [];
  if (limites) {
    limites.forEach(([desde], k) => {
      const techo = k < 4 ? limites[k + 1][0] : hi;
      const suelo = k === 0 ? lo : desde;
      const yTop = y(Math.min(Math.max(techo, lo), hi)), yBajo = y(Math.min(Math.max(suelo, lo), hi));
      partes.push(`<rect class="banda-zona capa-z${k + 1}" x="${x0}" y="${f1(yTop)}" width="${f1(x1 - x0)}" height="${f1(Math.max(0, yBajo - yTop))}"><title>Z${k + 1}: desde ${desde} ppm</title></rect>`);
    });
  }
  const paso = hi - lo <= 60 ? 10 : 20;
  for (let v = Math.ceil(lo / paso) * paso; v <= hi; v += paso) {
    partes.push(`<line class="rejilla" x1="${x0}" y1="${f1(y(v))}" x2="${x1}" y2="${f1(y(v))}"/>`,
      `<text class="eje" x="${x0 - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${v}</text>`);
  }
  const marcada = tramos.find((v) => v.n === elegida);
  if (marcada) {
    partes.push(`<rect class="resalte-vuelta" x="${f1(x(marcada.inicio_s))}" y="${arriba}" width="${f1(Math.max(1, x(marcada.fin_s) - x(marcada.inicio_s)))}" height="${abajo - arriba}"/>`);
  }
  tramos.slice(1).forEach((v) => partes.push(`<line class="limite-vuelta" x1="${f1(x(v.inicio_s))}" y1="${arriba}" x2="${f1(x(v.inicio_s))}" y2="${franja + altoFranja}"/>`));
  const puntos = curva.t.map((t, i) => (curva.pulso[i] === null ? null : `${f1(x(t))},${f1(y(curva.pulso[i]))}`)).filter(Boolean);
  partes.push(`<polyline class="linea-pulso" points="${puntos.join(" ")}"/>`);
  tramos.forEach((v) => {
    const clase = `tramo-vuelta ${v.zona ? `capa-z${v.zona}` : "capa-sin_pulso"}${v.n === elegida ? " resaltado" : ""}`;
    partes.push(`<rect class="${clase}" data-n="${v.n}" x="${f1(x(v.inicio_s))}" y="${franja}" width="${f1(Math.max(1, x(v.fin_s) - x(v.inicio_s)))}" height="${altoFranja}"><title>Vuelta ${v.n}: ${v.zona ? `Z${v.zona}` : "sin zona"}</title></rect>`);
  });
  const cada = TIEMPOS_EJE.find((s) => (s / fin) * (x1 - x0) >= SEPARACION_MIN + 6) ?? 14_400;
  for (let s = 0; s <= fin; s += cada) {
    const xt = Math.min(Math.max(x(s), 14), W - 22);
    partes.push(`<text class="etiqueta-x" x="${f1(xt)}" y="${franja + altoFranja + 16}" text-anchor="middle">${s ? `${Math.round(s / 60)} min` : "0"}</text>`);
  }
  partes.push(`<text class="titulo-eje" x="4" y="12">ppm</text>`);
  const lienzo = document.createElement("div");
  lienzo.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Pulso de la sesión sobre tus zonas">${partes.join("")}</svg>`;
  return lienzo.firstChild;
}
