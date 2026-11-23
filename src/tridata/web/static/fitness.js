// Unit 031 (REC-19 of `ver-fitness`): the Fitness tab. Plain ES module, no build step.
// Three blocks from ONE answer, GET /api/fitness: the load (form, fatigue and freshness of today with the chart of the
// 14 days, grafica.js, and where today's HR comes from), the last activity, and the recovery (sleep, resting HR and
// HRV, each next to its 7-day mean). Tapping the activity opens its detail (GET /api/actividad/ID): every lap with
// its distance, time, pace or speed, mean HR and the zone of that HR, and the weather. A Garmin datum that has not
// arrived says «aún no ha llegado» and one given half-done «⚠ incompleto»: never a 0 (G-47). The server computes.
// Unit 090 (REC-35): the range of the load (14 días · 6 semanas · 3 meses · 1 año; it opens in 6 semanas) asks
// /api/fitness?dias= again, and «Tus semanas» draws its weeks stacked by sport, in hours, km, load or zones
// (approximated by laps). «¿Cómo se calcula?» is a <details> of index.html.
// Unit 091 (REC-36): a sport selector over «Tus semanas» (it opens in «Todos») filters the weeks already received,
// without asking the server again; the measure and the range keep it.
// Unit 093 (REC-37): tapping a week of «Tus semanas» (or Enter on it) marks it and opens under the chart its totals
// next to the mean of the 4 weeks before and its activities (GET /api/fitness/semana, of the sport chosen); tapping
// one opens its session, the same detail as the last activity (`abrirActividad`), whose laps now also carry cadence,
// stride, SWOLF and climb by sport («—» when Garmin gave none). The same week again, or «Cerrar», closes it.
// Unit 094 (REC-40): a session with its detail second by second shows, above its laps, the curve of its HR over the
// bands of its zones with each lap's segment in its zone's colour (grafica.js `graficaPulso`), and the minutes and %
// in each zone counted second by second; tapping a lap of the list (or Enter on it) highlights its segment. Without
// detail it says «sin curva de pulso para esta sesión». In «zonas», «aproximado por vueltas» only while a session of
// the range is still by laps (each week's `aproximado`).
// Unit 096 (REC-41): a session's laps, curve and time in zone come with the zones of ITS sport (the server says which,
// and «zonas de bici estimadas desde carrera» when they are). Above the load, each pending proposal of a new threshold
// (GET /api/umbral/propuestas): «tu umbral de carrera pasa de X a Y (…), ¿actualizo desde hoy?», the zones it would
// give, and Sí / No (POST /api/umbral/propuestas/N). Only «Sí» changes the zones, from today. `avisoPropuesta` is
// shared with Ajustes › Pulso y zonas.
// Unit 097 (REC-42): a fifth button of the range, «Elegir fechas», opens «desde», «hasta» and «Aplicar»
// (/api/fitness?desde=&hasta=): the chart and «Tus semanas» of that period. Dates that are not valid show the
// server's reason and nothing is repainted. Under «Tus semanas», the table of this period against the one before of
// the same days (the `comparacion` of the answer): every sport and the total, whatever sport is chosen above.
// Unit 102 (REC-46): under «Recuperación», «Zapatillas» (GET /api/zapatillas): each shoe in use with its km, its cap,
// what is left (or gone over), «toca cambiarla pronto» / «pasada» and where the km come from with its date («de Strava,
// el 21/11» · «dicho por ti, hoy»), and why the last read of Strava failed if it did. «Cambiar km» and «Cambiar tope»
// open a small form in its row (POST /api/zapatillas/ID): the server checks it and says why when it does not go.
// Unit 103 (REC-47): «Dar de alta una zapatilla» (POST /api/zapatillas: name, km, cap and use) and «Retirar» in the
// rows of the shoes counted here; above the list, «¿Con qué zapatilla corriste el 26/11 (8,1 km)?» with a button per
// shoe for each run without one; and «Últimas carreras» with the shoe of each one, which a selector changes
// (POST /api/zapatillas/carrera/ID). Names always with `textContent`.

import { graficaCarga, graficaPulso, graficaSemanas, leyendaCarga, leyendaSemanas } from "./grafica.js";

const $ = (id) => document.getElementById(id);
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const unDecimal = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dosDecimales = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const hastaUnDecimal = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const diaMesCorto = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
const diaSemana = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "short" });
const NO_HA_LLEGADO = "aún no ha llegado";
const ICONOS = { Carrera: "🏃", Bici: "🚴", Natación: "🏊", Fuerza: "🏋️" };

let api = null; // app.js's `api` (it keeps the «sin conexión» notice)
let peticion = 0; // drops a late answer
let abierta = null; // id of the activity whose detail is open
let hoyISO = null;
let dias = 42; // unit 090: the range on screen
let medida = "horas"; // unit 090: what «Tus semanas» shows
let deporte = "todos"; // unit 091: the sport of «Tus semanas» (todos · correr · bici · nadar · fuerza)
let semanasVistas = []; // the weeks of the last answer, to repaint without asking again
let semanaElegida = null; // unit 093: the Monday of the week open under the chart
let peticionSemana = 0; // drops a late answer of the week
let sesionAbierta = null; // id of the activity of the week whose session is open
let fechas = null; // unit 097: {desde, hasta} of the period chosen with dates on screen; null with a shortcut
let periodoVisto = null; // unit 097: the `periodo` of the last answer (to fill «desde» and «hasta»)
let zapatillasVistas = null; // unit 102: the last /api/zapatillas answer
let zapatillaAbierta = null; // unit 102: {id, que: "km" | "tope"} of the form open
let peticionZapatillas = 0; // unit 102: drops a late answer
let altaAbierta = false; // unit 103: the form «Dar de alta» open
const USOS = [["suave", "Rodaje y suave"], ["calidad", "Series, ritmo y tiradas"], ["competicion", "Competición"]];
const PERIODOS = { 14: "últimos 14 días", 42: "últimas 6 semanas", 91: "últimos 3 meses", 365: "último año" };
const ETIQUETAS = { 14: "de los últimos 14 días", 42: "de las últimas 6 semanas", 91: "de los últimos 3 meses", 365: "del último año" };

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const conSigno = (n) => { const r = Math.round(n); return (r > 0 ? "+" : r < 0 ? "−" : "") + enteras.format(Math.abs(r)); };
const horas = (s) => { const m = Math.round(s / 60); return `${Math.floor(m / 60)} h ${m % 60} min`; };
const minutos = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
const mmss = (s) => { const r = Math.round(s); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`; };
const distancia = (m) => (m >= 1000 ? `${unDecimal.format(m / 1000)} km` : `${enteras.format(m)} m`);
const distanciaVuelta = (m) => (m >= 1000 ? `${dosDecimales.format(m / 1000)} km` : `${enteras.format(m)} m`);
const sumarDias = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

function cuando(iso) {
  if (iso === hoyISO) return "hoy";
  if (hoyISO && iso === sumarDias(hoyISO, -1)) return "ayer";
  return diaSemana.format(new Date(`${iso}T12:00:00`)).replace(".", "").replace(",", "");
}

// Unit 090: a group of buttons with aria-pressed; `alElegir(valor)` with the data-* of the one pressed.
function selector(id, clave, alElegir) {
  const grupo = $(id);
  grupo.addEventListener("click", (evento) => {
    const boton = evento.target.closest("button");
    if (!boton || boton.getAttribute("aria-pressed") === "true") return;
    for (const b of grupo.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === boton));
    alElegir(boton.dataset[clave]);
  });
}

export function iniciarFitness(opciones) {
  ({ api } = opciones);
  // unit 096: the proposals of a new threshold go above the load (a box of their own, created here)
  $("fitness-carga").before(el("div", { id: "fitness-propuestas", className: "propuestas-umbral", ariaLive: "polite" }));
  $("fitness-ultima").addEventListener("click", alternarDetalle);
  $("fitness-rango").addEventListener("click", alPulsarRango);
  $("fitness-fechas").noValidate = true; // the server says why a date does not go (R-268)
  $("fitness-fechas").addEventListener("submit", (evento) => { evento.preventDefault(); aplicarFechas(); });
  selector("fitness-medida", "medida", (valor) => { medida = valor; pintarSemanas(semanasVistas); });
  selector("fitness-deporte", "deporte", (valor) => {
    deporte = valor;
    pintarSemanas(semanasVistas);
    if (semanaElegida) cargarSemana();
  });
  // unit 093: a tap on a week (its whole column), or Enter / Space on it
  const grafica = $("fitness-grafica-semanas");
  grafica.addEventListener("click", (evento) => {
    const g = evento.target.closest("g.semana");
    if (g) alternarSemana(g.dataset.lunes);
  });
  grafica.addEventListener("keydown", (evento) => {
    const g = evento.target.closest("g.semana");
    if (!g || (evento.key !== "Enter" && evento.key !== " ")) return;
    evento.preventDefault();
    alternarSemana(g.dataset.lunes);
  });
  $("fitness-semana-cerrar").addEventListener("click", cerrarSemana);
  $("fitness-semana-lista").addEventListener("click", (evento) => {
    const boton = evento.target.closest("button.actividad-semana");
    if (boton) alternarSesion(boton.dataset.id);
  });
}

// -- unit 097: the range, a shortcut or dates of its own (R-263, R-268) ---------------------------------------------

function marcarRango(valor) {
  for (const b of $("fitness-rango").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.dias === valor));
}

function mostrarFechas(abiertas) {
  $("fitness-fechas").hidden = !abiertas;
  $("fitness-rango").querySelector('button[data-dias="fechas"]').setAttribute("aria-expanded", String(abiertas));
  $("fitness-fechas-error").hidden = true;
  if (!abiertas) return;
  if (hoyISO) { $("fitness-desde").max = hoyISO; $("fitness-hasta").max = hoyISO; }
  if (periodoVisto) { $("fitness-desde").value = periodoVisto.desde; $("fitness-hasta").value = periodoVisto.hasta; }
}

function alPulsarRango(evento) {
  const boton = evento.target.closest("button");
  if (!boton) return;
  if (boton.dataset.dias === "fechas") { mostrarFechas($("fitness-fechas").hidden); return; }
  mostrarFechas(false);
  if (boton.getAttribute("aria-pressed") === "true") return;
  marcarRango(boton.dataset.dias);
  dias = Number(boton.dataset.dias);
  fechas = null;
  cargarFitness();
}

function errorFechas(texto) {
  $("fitness-fechas-error").textContent = texto;
  $("fitness-fechas-error").hidden = false;
}

async function aplicarFechas() {
  const desde = $("fitness-desde").value, hasta = $("fitness-hasta").value;
  if (!desde || !hasta) { errorFechas("Elige el «desde» y el «hasta»."); return; }
  $("fitness-fechas-error").hidden = true;
  await cargarFitness({ desde, hasta });
}

// «del 1/9 al 15/10» (with the year when the two ends are of different years)
function textoPeriodo(desde, hasta) {
  const corta = (iso, conAno) => { const [a, m, d] = iso.split("-").map(Number); return conAno ? `${d}/${m}/${a}` : `${d}/${m}`; };
  const conAno = desde.slice(0, 4) !== hasta.slice(0, 4);
  return `del ${corta(desde, conAno)} al ${corta(hasta, conAno)}`;
}

// `candidatas` ({desde, hasta}): dates still to be accepted; a 400 says why and leaves the screen as it was (C-337).
export async function cargarFitness(candidatas = null) {
  const mia = ++peticion;
  const elegidas = candidatas ?? fechas;
  const consulta = elegidas ? `desde=${encodeURIComponent(elegidas.desde)}&hasta=${encodeURIComponent(elegidas.hasta)}` : `dias=${dias}`;
  const { ok, datos } = await api(`/api/fitness?${consulta}`);
  if (mia !== peticion) return;
  if (!ok) {
    if (candidatas) { errorFechas(datos.error ?? "Esas fechas no valen."); return; }
    $("fitness-pulso").textContent = datos.error ?? "No he podido cargar Fitness.";
    return;
  }
  if (candidatas) { fechas = candidatas; marcarRango("fechas"); }
  hoyISO = datos.fecha;
  periodoVisto = datos.periodo ?? null;
  pintarCarga(datos.carga);
  pintarComparacion(datos.comparacion);
  // unit 093: another range keeps the week open if it is still on the chart
  if (semanaElegida && !(datos.semanas ?? []).some((s) => s.lunes === semanaElegida)) cerrarSemana();
  pintarSemanas(datos.semanas ?? []);
  pintarUltima(datos.ultima_actividad);
  pintarRecuperacion(datos.recuperacion);
  cargarPropuestas();
  cargarZapatillas();
}

// -- unit 102 (REC-46): the shoes in use (R-287..R-291) --------------------------------------------------------------

async function cargarZapatillas() {
  const mia = ++peticionZapatillas;
  const { ok, datos } = await api("/api/zapatillas");
  if (mia !== peticionZapatillas || !ok) return;
  pintarZapatillas(datos);
}

function pintarZapatillas(d) {
  zapatillasVistas = d;
  const aviso = $("fitness-zapatillas-aviso");
  aviso.textContent = d.aviso_lectura ?? "";
  aviso.hidden = !d.aviso_lectura;
  const vacio = $("fitness-zapatillas-vacio");
  vacio.hidden = d.zapatillas.length > 0;
  vacio.textContent = d.strava?.conectado ? "Strava no tiene ninguna zapatilla en activo."
    : "Aún no hay zapatillas: dalas de alta con los km que llevan y desde entonces se les suman las carreras de Garmin.";
  $("fitness-zapatillas-lista").replaceChildren(...d.zapatillas.map(filaZapatilla));
  pintarPreguntas($("fitness-zapatillas-pregunta"), d.pendientes ?? [], elegirCarrera);
  pintarAlta();
  pintarCarreras(d.ultimas ?? [], d.opciones ?? []);
}

// unit 103 (R-294): the shoe of a run, chosen by Andrés; the answer repaints the shoes, or the reason is returned
async function elegirCarrera(carrera, zapatilla) {
  const { ok, datos } = await api(`/api/zapatillas/carrera/${encodeURIComponent(carrera)}`, { zapatilla });
  if (!ok) return datos.error ?? "No se ha podido guardar.";
  pintarZapatillas(datos);
  return null;
}

// unit 103 (C-375): one question per run without a shoe, with a button per shoe here in use. Shared with Hoy.
export function pintarPreguntas(caja, pendientes, elegir) {
  caja.hidden = pendientes.length === 0;
  caja.replaceChildren(...pendientes.map((p) => preguntaZapatilla(p, elegir)));
}

export function preguntaZapatilla(p, elegir) {
  const error = el("p", { className: "aviso", role: "alert", hidden: true });
  const botones = p.opciones.map((o) => {
    const boton = el("button", { type: "button", textContent: o.nombre });
    boton.addEventListener("click", async () => {
      for (const b of botones) b.disabled = true;
      const fallo = await elegir(p.activity_id, o.id);
      if (fallo) { error.textContent = fallo; error.hidden = false; for (const b of botones) b.disabled = false; }
    });
    return boton;
  });
  return el("div", { className: "pregunta-zapatilla" }, el("p", { textContent: p.texto }),
    el("div", { className: "opciones-zapatilla" }, ...botones), error);
}

// unit 103 (R-292): «Dar de alta una zapatilla» opens its form; the server checks everything and says why
function pintarAlta() {
  const caja = $("fitness-zapatillas-alta");
  if (!altaAbierta) {
    const abrir = el("button", { type: "button", textContent: "Dar de alta una zapatilla" });
    abrir.addEventListener("click", () => { altaAbierta = true; pintarAlta(); });
    caja.replaceChildren(abrir);
    return;
  }
  const campo = (id, etiqueta, props) => {
    const input = el("input", { id, type: "text", autocomplete: "off", ...props });
    return [el("label", { htmlFor: id, textContent: etiqueta }), input];
  };
  const [lNombre, nombre] = campo("alta-zapatilla-nombre", "Nombre", { maxLength: 80 });
  const [lKm, km] = campo("alta-zapatilla-km", "Km que lleva", { inputMode: "decimal" });
  const [lTope, tope] = campo("alta-zapatilla-tope", "Tope en km", { inputMode: "numeric", value: "700" });
  const uso = el("select", { id: "alta-zapatilla-uso" },
    ...USOS.map(([valor, texto]) => el("option", { value: valor, textContent: texto })));
  const lUso = el("label", { htmlFor: uso.id, textContent: "Para qué la usas" });
  const error = el("p", { className: "aviso", role: "alert", hidden: true });
  const guardar = el("button", { type: "submit", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const form = el("form", { className: "editar-zapatilla alta-zapatilla", noValidate: true },
    lNombre, nombre, lKm, km, lTope, tope, lUso, uso, error, el("div", { className: "acciones" }, guardar, cancelar));
  cancelar.addEventListener("click", () => { altaAbierta = false; pintarAlta(); });
  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    guardar.disabled = true;
    const { ok, datos } = await api("/api/zapatillas", { nombre: nombre.value.trim(), km: km.value.trim(),
      tope: tope.value.trim(), uso: uso.value });
    guardar.disabled = false;
    if (!ok) { error.textContent = datos.error ?? "No se ha podido guardar."; error.hidden = false; return; }
    altaAbierta = false;
    pintarZapatillas(datos);
  });
  caja.replaceChildren(form);
  queueMicrotask(() => nombre.focus());
}

// unit 103 (R-295): the last runs since the start with their shoe; changing it moves the km at once
function pintarCarreras(ultimas, opciones) {
  $("fitness-carreras").hidden = ultimas.length === 0;
  $("fitness-carreras-lista").replaceChildren(...ultimas.map((c) => {
    const [dia, mes] = [Number(c.fecha.slice(8, 10)), Number(c.fecha.slice(5, 7))];
    const elegir = el("select", { ariaLabel: `Zapatilla de la carrera del ${dia}/${mes}` });
    if (!c.zapatilla_id) elegir.append(el("option", { value: "", textContent: "¿Con cuál?", disabled: true, selected: true }));
    if (c.zapatilla_id && !opciones.some((o) => o.id === c.zapatilla_id)) {
      elegir.append(el("option", { value: c.zapatilla_id, textContent: c.nombre, disabled: true }));
    }
    elegir.append(...opciones.map((o) => el("option", { value: o.id, textContent: o.nombre })));
    if (c.zapatilla_id) elegir.value = c.zapatilla_id;
    const error = el("p", { className: "aviso", role: "alert", hidden: true });
    elegir.addEventListener("change", async () => {
      elegir.disabled = true;
      const fallo = await elegirCarrera(c.activity_id, elegir.value);
      if (fallo) { error.textContent = fallo; error.hidden = false; elegir.disabled = false; }
    });
    const origen = el("span", { className: `origen-carrera${c.zapatilla_id ? "" : " sin"}`,
      textContent: c.texto_origen ?? "sin zapatilla" });
    return el("li", { className: "carrera-zapatilla" },
      el("div", { className: "cab-carrera" }, el("strong", { className: "num", textContent: c.texto }), origen),
      elegir, error);
  }));
}

function restoZapatilla(z) {
  if (z.quedan > 0) return `quedan ${enteras.format(z.quedan)}`;
  return z.quedan < 0 ? `te has pasado ${enteras.format(-z.quedan)}` : "en el tope";
}

function filaZapatilla(z) {
  const fila = el("li", { className: `zapatilla ${z.estado}` });
  fila.dataset.id = z.id;
  const cabeza = el("div", { className: "cab-zapatilla" }, el("strong", { textContent: z.nombre }),
    z.texto_estado ? el("span", { className: `marca-zapatilla ${z.estado}`, textContent: z.texto_estado }) : null);
  const cifras = el("p", { className: "num cifras-zapatilla",
    textContent: `${enteras.format(Math.round(z.km))} de ${enteras.format(z.tope)} km · ${restoZapatilla(z)}` });
  const fuente = el("p", { className: "suave fuente-zapatilla",
    textContent: [z.texto_fuente, z.texto_lectura].filter(Boolean).join(" · ") });
  fila.append(cabeza, cifras, fuente);
  if (zapatillaAbierta?.id === z.id) {
    fila.append(formZapatilla(z, zapatillaAbierta.que));
    return fila;
  }
  const abrir = (que) => { zapatillaAbierta = { id: z.id, que }; pintarZapatillas(zapatillasVistas); };
  const km = el("button", { type: "button", textContent: "Cambiar km" });
  const tope = el("button", { type: "button", textContent: "Cambiar tope" });
  km.addEventListener("click", () => abrir("km"));
  tope.addEventListener("click", () => abrir("tope"));
  const acciones = el("div", { className: "acciones acciones-zapatilla" }, km, tope);
  if (z.fuente === "aqui") { // unit 103 (R-292): out of the list; what it counted stays with it
    const retirar = el("button", { type: "button", textContent: "Retirar" });
    retirar.addEventListener("click", async () => {
      retirar.disabled = true;
      const { ok, datos } = await api(`/api/zapatillas/${encodeURIComponent(z.id)}`, { retirar: true });
      if (ok) pintarZapatillas(datos); else retirar.disabled = false;
    });
    acciones.append(retirar);
  }
  fila.append(acciones);
  return fila;
}

// R-288: the cap, a whole number from 50 to 3000; R-291: the km he says, «dicho por ti» (the server checks both)
function formZapatilla(z, que) {
  const campo = el("input", { type: "text", inputMode: que === "km" ? "decimal" : "numeric", autocomplete: "off",
    id: `zapatilla-${que}-${z.id}`, value: que === "km" ? unDecimal.format(z.km).replace(/\./g, "") : String(z.tope) });
  const etiqueta = el("label", { htmlFor: campo.id, textContent: que === "km" ? "Km que lleva" : "Tope en km" });
  const error = el("p", { className: "aviso", role: "alert", hidden: true });
  const guardar = el("button", { type: "submit", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const form = el("form", { className: "editar-zapatilla", noValidate: true },
    etiqueta, campo, error, el("div", { className: "acciones" }, guardar, cancelar));
  cancelar.addEventListener("click", () => { zapatillaAbierta = null; pintarZapatillas(zapatillasVistas); });
  form.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    guardar.disabled = true;
    const { ok, datos } = await api(`/api/zapatillas/${encodeURIComponent(z.id)}`, { [que]: campo.value.trim() });
    guardar.disabled = false;
    if (!ok) { error.textContent = datos.error ?? "No se ha podido guardar."; error.hidden = false; return; }
    zapatillaAbierta = null;
    pintarZapatillas(datos);
  });
  queueMicrotask(() => campo.focus());
  return form;
}

// -- unit 096: the proposals of a new threshold (R-260, R-261) -------------------------------------------------------

const textoZona = (z, i) => (i === 4 ? `Z5 desde ${z[0]}` : `Z${i + 1} ${z[0]}–${z[1]}`);

// One proposal: what it says, the zones it would give and Sí / No. `responder(id, respuesta)` answers it and returns
// an error text, or null when it went well (the caller repaints).
export function avisoPropuesta(p, responder) {
  const si = el("button", { type: "button", className: "principal", textContent: "Sí" });
  const no = el("button", { type: "button", textContent: "No" });
  const error = el("p", { className: "aviso", hidden: true });
  const pulsar = async (respuesta) => {
    si.disabled = no.disabled = true;
    const fallo = await responder(p.id, respuesta);
    if (fallo) { error.textContent = fallo; error.hidden = false; si.disabled = no.disabled = false; }
  };
  si.addEventListener("click", () => pulsar("si"));
  no.addEventListener("click", () => pulsar("no"));
  return el("article", { className: "tarjeta propuesta-umbral" },
    el("p", { className: "fuerte" }, el("span", { className: "etiqueta-propuesta", textContent: "Propuesta: " }), p.texto),
    el("p", { className: "suave zonas-propuesta", textContent: `Zonas de ${p.nombre} que saldrían: ${p.zonas.map(textoZona).join(" · ")}` }),
    error, el("div", { className: "acciones" }, si, no));
}

async function cargarPropuestas() {
  const { ok, datos } = await api("/api/umbral/propuestas");
  if (ok) pintarPropuestas(datos.propuestas ?? []);
}

function pintarPropuestas(lista) {
  $("fitness-propuestas").replaceChildren(...lista.map((p) => avisoPropuesta(p, async (id, respuesta) => {
    const { ok, datos } = await api(`/api/umbral/propuestas/${id}`, { respuesta });
    if (!ok) return datos.error ?? "No se ha podido responder.";
    pintarPropuestas(datos.propuestas ?? []);
    return null;
  })));
}

function pintarSemanas(semanas) {
  semanasVistas = semanas;
  const caja = $("fitness-grafica-semanas");
  const enfocada = caja.contains(document.activeElement) ? document.activeElement.dataset?.lunes : null;
  caja.replaceChildren(graficaSemanas(semanas, medida, caja.clientWidth - 8, deporte, semanaElegida),
    leyendaSemanas(medida, deporte, semanas.some((s) => s.aproximado)));
  if (enfocada) caja.querySelector(`g.semana[data-lunes="${enfocada}"]`)?.focus(); // the keyboard keeps its place
}

// -- unit 093: the week tapped, its activities and the session of one ------------------------------------------------

function alternarSemana(lunes) {
  if (!lunes) return;
  if (semanaElegida === lunes) { cerrarSemana(); return; }
  semanaElegida = lunes;
  pintarSemanas(semanasVistas);
  cargarSemana();
}

function cerrarSemana() {
  if (!semanaElegida && $("fitness-semana").hidden) return;
  semanaElegida = null;
  peticionSemana++;
  cerrarSesion();
  $("fitness-semana").hidden = true;
  pintarSemanas(semanasVistas);
}

async function cargarSemana() {
  const mia = ++peticionSemana, lunes = semanaElegida;
  cerrarSesion();
  const panel = $("fitness-semana");
  panel.hidden = false;
  $("fitness-semana-titulo").textContent = tituloSemana(lunes, sumarDias(lunes, 6), false);
  $("fitness-semana-totales").replaceChildren();
  $("fitness-semana-lista").replaceChildren(el("p", { className: "suave", textContent: "Cargando la semana…" }));
  const { ok, datos } = await api(`/api/fitness/semana?lunes=${encodeURIComponent(lunes)}&deporte=${encodeURIComponent(deporte)}`);
  if (mia !== peticionSemana) return;
  if (!ok) {
    $("fitness-semana-lista").replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido cargar la semana." }));
    return;
  }
  pintarSemana(datos);
}

const fechaCorta = (iso) => diaMesCorto.format(new Date(`${iso}T12:00:00`)).replace(".", "");
const tituloSemana = (lunes, domingo, enCurso) => `Semana del ${fechaCorta(lunes)} al ${fechaCorta(domingo)}${enCurso ? " · en curso" : ""}`;

function textoMedia(n) {
  if (n === 4) return "media 4 semanas";
  return `media de ${n} ${n === 1 ? "semana" : "semanas"}`;
}

// R-240: each figure next to the mean of the weeks before, in plain text (no alarm colours)
function cifraSemana(titulo, valor, media, formato, semanasMedia) {
  const caja = el("div", { className: "cifra-semana" },
    el("span", { textContent: titulo }),
    el("strong", { className: "num", textContent: valor === null ? "—" : formato(valor) }));
  const detalle = semanasMedia ? `${textoMedia(semanasMedia)}: ${media === null ? "—" : formato(media)}` : "sin semanas antes";
  caja.append(el("small", { textContent: detalle }));
  return caja;
}

function pintarSemana(s) {
  $("fitness-semana-titulo").textContent = tituloSemana(s.lunes, s.domingo, s.en_curso);
  const t = s.totales, m = s.media ?? {}, k = s.semanas_media;
  const de = (clave) => (s.media ? m[clave] : null);
  $("fitness-semana-totales").replaceChildren(
    cifraSemana("Horas", t.horas, de("horas"), (v) => `${unDecimal.format(v)} h`, k),
    cifraSemana("Km", t.km, de("km"), (v) => `${unDecimal.format(v)} km`, k),
    cifraSemana("Carga", t.carga, de("carga"), (v) => enteras.format(Math.round(v)), k),
    cifraSemana("Nº de sesiones", t.sesiones, de("sesiones"), (v) => hastaUnDecimal.format(v), k));
  const lista = $("fitness-semana-lista");
  if (!s.actividades.length) {
    lista.replaceChildren(el("p", { className: "suave vacia-semana", textContent: "sin actividades esta semana" }));
    return;
  }
  lista.replaceChildren(el("ul", { className: "lista-semana" }, ...s.actividades.map(filaActividad)));
}

function filaActividad(a) {
  const cifras = [a.distancia_m ? distancia(a.distancia_m) : null, a.duracion_s ? minutos(a.duracion_s) : null,
    a.pulso_medio ? `❤ ${enteras.format(Math.round(a.pulso_medio))}` : "sin pulso",
    a.carga !== null ? `carga ${enteras.format(Math.round(a.carga))}` : null].filter(Boolean);
  const boton = el("button", { type: "button", className: "actividad-semana", ariaExpanded: "false" },
    el("span", { className: "icono-actividad", textContent: ICONOS[a.deporte] ?? "⏱️", ariaHidden: "true" }),
    el("span", { className: "texto-actividad-semana" },
      el("strong", { textContent: a.nombre }),
      el("span", { className: "suave", textContent: `${cuando(a.fecha)} · ${a.deporte}` }),
      el("span", { className: "cifras-actividad-semana", textContent: cifras.join(" · ") })),
    el("span", { className: "chev", textContent: "›", ariaHidden: "true" }));
  boton.dataset.id = a.actividad_id;
  boton.setAttribute("aria-controls", "fitness-sesion");
  return el("li", {}, boton);
}

function cerrarSesion() {
  sesionAbierta = null;
  $("fitness-sesion").hidden = true;
  for (const b of document.querySelectorAll("#fitness-semana-lista button.actividad-semana")) b.setAttribute("aria-expanded", "false");
}

async function alternarSesion(id) {
  if (!id) return;
  if (sesionAbierta === id) { cerrarSesion(); return; }
  cerrarSesion();
  sesionAbierta = id;
  const boton = [...document.querySelectorAll("#fitness-semana-lista button.actividad-semana")].find((b) => b.dataset.id === id);
  boton?.setAttribute("aria-expanded", "true");
  const caja = $("fitness-sesion");
  await abrirActividad(id, caja, () => sesionAbierta === id);
  if (sesionAbierta === id) caja.scrollIntoView({ block: "nearest" });
}

function pintarCarga(c) {
  const h = c.hoy;
  $("fitness-ctl").textContent = h ? enteras.format(Math.round(h.ctl)) : "—";
  $("fitness-atl").textContent = h ? enteras.format(Math.round(h.atl)) : "—";
  $("fitness-tsb").textContent = h ? conSigno(h.tsb) : "—";
  $("fitness-tsb").classList.toggle("bien", Boolean(h) && Math.round(h.tsb) > 0);
  const caja = $("fitness-grafica");
  const periodo = fechas && periodoVisto ? textoPeriodo(periodoVisto.desde, periodoVisto.hasta) : null;
  $("fitness-rango-texto").textContent = periodo ?? PERIODOS[dias];
  caja.replaceChildren(graficaCarga(c.serie, caja.clientWidth - 8, periodo ?? ETIQUETAS[dias]), leyendaCarga());
  $("fitness-pulso").textContent = `Tu FC y tus zonas: ${c.pulso.texto}.`;
  $("fitness-pulso").classList.toggle("aviso", !c.pulso.confirmada);
}

// -- unit 097: this period against the one before (R-264..R-267) ---------------------------------------------------

const NOMBRES_TABLA = { correr: "Carrera", bici: "Bici", nadar: "Natación", fuerza: "Fuerza", otros: "Otros", total: "Total" };
const MEDIDAS_TABLA = [["horas", "Horas", (v) => `${unDecimal.format(v)} h`], ["km", "Km", (v) => `${unDecimal.format(v)} km`],
  ["sesiones", "Sesiones", (v) => enteras.format(v)], ["horas_semana", "Horas por semana", (v) => `${unDecimal.format(v)} h`]];
const fechaLarga = (iso) => { const [a, m, d] = iso.split("-").map(Number); return `${d}/${m}/${a}`; };

// The difference with its sign, coloured «more» / «less» without judging it (no traffic lights), and its % or «—».
function celdaDiferencia(valor, pct, formato) {
  const td = el("td");
  if (valor === null) { td.textContent = "—"; return td; }
  const texto = formato(Math.abs(valor));
  const cero = formato(0) === texto;
  td.append(cero ? texto : `${valor > 0 ? "+" : "−"}${texto}`);
  if (!cero) td.classList.add(valor > 0 ? "dif-mas" : "dif-menos");
  td.append(el("span", { className: "pct", textContent: pct === null ? "—" : `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${enteras.format(Math.abs(pct))} %` }));
  return td;
}

function pintarComparacion(c) {
  if (!c) return;
  $("fitness-comparar-periodos").textContent = `Este periodo, ${textoPeriodo(c.este.desde, c.este.hasta)} (${c.este.dias} días), frente al anterior, ${textoPeriodo(c.anterior.desde, c.anterior.hasta)}.`;
  const aviso = $("fitness-comparar-aviso");
  aviso.hidden = !c.historial_desde;
  aviso.textContent = c.historial_desde
    ? `Ojo: tu historial empieza el ${fechaLarga(c.historial_desde)}, así que el periodo anterior no está entero y no doy %.` : "";
  const cabeza = el("thead", {}, el("tr", {}, el("th", { scope: "col", textContent: "" }),
    el("th", { scope: "col", textContent: "Este periodo" }), el("th", { scope: "col", textContent: "El anterior" }),
    el("th", { scope: "col", textContent: "Diferencia" })));
  const cuerpos = c.filas.map((f) => {
    const cuerpo = el("tbody", {}, el("tr", {}, el("th", { scope: "colgroup", colSpan: 4, textContent: NOMBRES_TABLA[f.deporte] ?? f.deporte })));
    cuerpo.dataset.deporte = f.deporte;
    for (const [clave, nombre, formato] of MEDIDAS_TABLA) {
      const valor = (v) => (v === null ? "—" : formato(v));
      cuerpo.append(el("tr", {}, el("td", { textContent: nombre }), el("td", { textContent: valor(f.este[clave]) }),
        el("td", { textContent: valor(f.anterior[clave]) }), celdaDiferencia(f.diferencia[clave], f.porcentaje[clave], formato)));
    }
    return cuerpo;
  });
  $("fitness-comparar-tabla").replaceChildren(el("table", { ariaLabel: "Este periodo frente al anterior, por deporte" }, cabeza, ...cuerpos));
}

function chips(a) {
  const lista = [];
  if (a.pulso_medio) lista.push(`Pulso medio ${enteras.format(Math.round(a.pulso_medio))}`);
  if (a.carga !== null) lista.push(`Carga ${enteras.format(Math.round(a.carga))}`);
  lista.push(a.vueltas ? `${a.vueltas} ${a.vueltas === 1 ? "vuelta" : "vueltas"}` : "sin vueltas");
  lista.push(a.clima ? "con clima" : "sin clima");
  return el("ul", { className: "chips-hoy" }, ...lista.map((t) => el("li", { className: "chip-hoy", textContent: t })));
}

function pintarUltima(a) {
  const boton = $("fitness-ultima");
  if (!a) {
    boton.disabled = true;
    boton.replaceChildren(el("p", { className: "suave", textContent: "Aún no hay ninguna actividad." }));
    $("fitness-detalle").hidden = true;
    return;
  }
  boton.disabled = false;
  boton.dataset.id = a.actividad_id;
  const linea = [a.distancia_m ? distancia(a.distancia_m) : null, a.duracion_s ? minutos(a.duracion_s) : null]
    .filter(Boolean).join(" · ");
  boton.replaceChildren(
    el("div", { className: "cab-actividad" },
      el("span", { className: "icono-actividad", textContent: ICONOS[a.deporte] ?? "⏱️", ariaHidden: "true" }),
      el("div", {},
        el("strong", { textContent: `${a.nombre} · ${cuando(a.fecha)}` }),
        el("span", { className: "suave", textContent: [a.deporte, linea].filter(Boolean).join(" · ") })),
      el("span", { className: "chev", textContent: "›" })),
    chips(a));
  if (abierta && abierta !== a.actividad_id) cerrarDetalle();
}

function cerrarDetalle() {
  abierta = null;
  $("fitness-detalle").hidden = true;
  $("fitness-ultima").setAttribute("aria-expanded", "false");
}

async function alternarDetalle() {
  const id = $("fitness-ultima").dataset.id;
  if (!id) return;
  if (abierta === id) { cerrarDetalle(); return; }
  abierta = id;
  $("fitness-ultima").setAttribute("aria-expanded", "true");
  await abrirActividad(id, $("fitness-detalle"), () => abierta === id);
}

// Unit 093 (R-241): the detail of any activity by its id, in `caja` (the last activity's or the week's session);
// `sigue()` says whether it is still wanted when the answer comes.
async function abrirActividad(id, caja, sigue) {
  caja.replaceChildren(el("p", { className: "suave", textContent: "Cargando las vueltas…" }));
  caja.hidden = false;
  const { ok, datos } = await api(`/api/actividad/${encodeURIComponent(id)}`);
  if (!sigue()) return;
  if (!ok) { caja.replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido cargarla." })); return; }
  pintarDetalle(datos, caja);
}

function ritmo(v) {
  if (v.velocidad_kmh !== null) return `${unDecimal.format(v.velocidad_kmh)} km/h`;
  if (v.ritmo_s_100m !== null) return `${mmss(v.ritmo_s_100m)} /100 m`;
  if (v.ritmo_s_km !== null) return `${mmss(v.ritmo_s_km)} /km`;
  return null;
}

// Unit 093 (R-242): only what the lap's sport carries (the server sends only those keys); «—» if Garmin gave none.
function extrasVuelta(v) {
  const partes = [];
  const falta = (titulo) => `${titulo} —`;
  if ("cadencia" in v) {
    const unidad = "swolf" in v ? "brazadas/min" : "ppm";
    partes.push(v.cadencia !== null ? `cadencia ${enteras.format(v.cadencia)} ${unidad}` : falta("cadencia"));
  }
  if ("zancada_m" in v) partes.push(v.zancada_m !== null ? `zancada ${dosDecimales.format(v.zancada_m)} m` : falta("zancada"));
  if ("swolf" in v) partes.push(v.swolf !== null ? `SWOLF ${enteras.format(v.swolf)}` : falta("SWOLF"));
  if ("subida_m" in v) partes.push(v.subida_m !== null ? `subida +${enteras.format(v.subida_m)} m` : falta("subida"));
  return partes.length ? el("small", { className: "extra-vuelta", textContent: partes.join(" · ") }) : null;
}

function filaVuelta(v) {
  const datos = [v.distancia_m ? distanciaVuelta(v.distancia_m) : null, v.duracion_s ? mmss(v.duracion_s) : null, ritmo(v),
    v.pulso_medio ? `❤ ${enteras.format(Math.round(v.pulso_medio))}` : "sin pulso"].filter(Boolean);
  const fila = el("li", { className: "vuelta" },
    el("span", { className: "n-vuelta", textContent: String(v.n) }),
    el("span", { className: "datos-vuelta" }, datos.join(" · "), extrasVuelta(v)),
    el("span", { className: `zona-vuelta${v.zona ? ` z${v.zona}` : ""}`, textContent: v.zona ? `Z${v.zona}` : "—" }));
  fila.dataset.n = String(v.n);
  return fila;
}

// -- unit 094: the curve of the HR of the session and its time in each zone ----------------------------------------

const ZONAS_SESION = [["z1", "Z1"], ["z2", "Z2"], ["z3", "Z3"], ["z4", "Z4"], ["z5", "Z5"], ["sin_pulso", "sin pulso"]];

// R-252: minutes and % of the session in each zone, counted second by second; «sin pulso» only if there is any.
function zonasSesion(zonasS) {
  const total = Object.values(zonasS).reduce((a, b) => a + b, 0) || 1;
  const filas = ZONAS_SESION.filter(([clave]) => clave !== "sin_pulso" || zonasS.sin_pulso > 0).map(([clave, nombre]) => {
    const segundos = zonasS[clave] ?? 0;
    return el("li", {},
      el("span", { className: `ley-capa capa-${clave}`, textContent: "■", ariaHidden: "true" }),
      ` ${nombre} ${enteras.format(Math.round(segundos / 60))}\u00a0min · ${enteras.format(Math.round((segundos / total) * 100))}\u00a0%`);
  });
  return el("ul", { className: "zonas-sesion", ariaLabel: "Tiempo en cada zona, segundo a segundo" }, ...filas);
}

// R-250, R-251: the curve over the laps; tapping a lap (or Enter / Space on it) highlights its segment, again unmarks it.
function curvaPulso(curva, ancho, lista) {
  const grafica = el("div", { className: "grafica grafica-pulso" });
  let elegida = null;
  const pintar = () => {
    grafica.replaceChildren(graficaPulso(curva, ancho, elegida));
    for (const li of lista?.querySelectorAll("li.vuelta") ?? []) {
      if (Number(li.dataset.n) === elegida) { li.setAttribute("aria-current", "true"); li.classList.add("vuelta-marcada"); }
      else { li.removeAttribute("aria-current"); li.classList.remove("vuelta-marcada"); }
    }
  };
  const elegir = (li) => { if (!li) return; const n = Number(li.dataset.n); elegida = elegida === n ? null : n; pintar(); };
  if (lista) {
    for (const li of lista.querySelectorAll("li.vuelta")) {
      li.tabIndex = 0;
      li.title = "Toca para verla en la curva";
      li.classList.add("vuelta-tocable");
    }
    lista.addEventListener("click", (evento) => elegir(evento.target.closest("li.vuelta")));
    lista.addEventListener("keydown", (evento) => {
      if (evento.key !== "Enter" && evento.key !== " ") return;
      const li = evento.target.closest("li.vuelta");
      if (!li) return;
      evento.preventDefault();
      elegir(li);
    });
  }
  pintar();
  const nodos = [el("h3", { textContent: "Pulso de la sesión" }), grafica,
    el("p", { className: "leyenda-grafica leyenda-pulso", textContent: "Bandas: tus zonas de ese día · franja de abajo: cada vuelta en el color de su zona" })];
  nodos.push(curva.limites ? zonasSesion(curva.zonas_s)
    : el("p", { className: "suave", textContent: "Sin zonas confirmadas ese día: el tiempo en cada zona sale al confirmarlas en Ajustes." }));
  return el("section", { className: "curva-pulso" }, ...nodos);
}

function textoClima(c) {
  const partes = [];
  if (c.temp_c !== null && c.temp_c !== undefined) partes.push(`${enteras.format(Math.round(c.temp_c))} °C`);
  if (c.condition) partes.push(c.condition);
  if (c.humidity_pct !== null && c.humidity_pct !== undefined) partes.push(`humedad ${enteras.format(Math.round(c.humidity_pct))} %`);
  if (c.wind_speed_kmh !== null && c.wind_speed_kmh !== undefined) partes.push(`viento ${enteras.format(Math.round(c.wind_speed_kmh))} km/h`);
  return partes.join(" · ");
}

function pintarDetalle(d, caja = $("fitness-detalle")) {
  const lista = d.vueltas.length ? el("ol", { className: "vueltas" }, ...d.vueltas.map(filaVuelta)) : null;
  // unit 094 (R-250, R-254): above the laps, the curve of the HR, or that there is none
  const nodos = [d.curva ? curvaPulso(d.curva, caja.clientWidth - 30, lista)
    : el("p", { className: "suave sin-curva", textContent: "sin curva de pulso para esta sesión" })];
  nodos.push(el("h3", { textContent: `Vueltas de ${d.actividad.nombre}` }));
  // unit 096 (C-329): which sport's zones colour this session, and whether they are estimated from the run
  if (d.zonas && d.zonas_deporte) {
    nodos.push(el("p", { className: `suave zonas-de-sesion${d.zonas_deporte.estimadas ? " estimadas" : ""}`,
      textContent: `Con tus ${d.zonas_deporte.texto} (umbral ${d.zonas_deporte.umbral}).` }));
  }
  if (lista) {
    if (!d.zonas) nodos.push(el("p", { className: "aviso", textContent: "Sin zonas confirmadas para ese día: confírmalas en Ajustes › Pulso y zonas." }));
    nodos.push(lista);
  } else {
    nodos.push(el("p", { className: "suave", textContent: "Esta actividad no tiene vueltas." }));
  }
  nodos.push(el("h3", { textContent: "Clima" }),
    el("p", { className: d.clima ? "clima" : "suave", textContent: d.clima ? textoClima(d.clima) : "Sin clima para esta actividad." }));
  caja.replaceChildren(...nodos);
}

function dato(titulo, principal, detalle, estado) {
  const caja = el("div", { className: `tarjeta dato-recuperacion${estado === "no_ha_llegado" ? " falta" : ""}${estado === "incompleto" ? " incompleto" : ""}` },
    el("span", { textContent: titulo }),
    el("strong", { textContent: estado === "no_ha_llegado" ? NO_HA_LLEGADO : principal }));
  if (estado === "incompleto") caja.append(el("small", { className: "marca-incompleto", textContent: "⚠ incompleto" }));
  if (detalle) caja.append(el("small", { textContent: detalle }));
  return caja;
}

function pintarRecuperacion(r) {
  const s = r.sueno, p = r.pulso_reposo, v = r.hrv;
  const mediaSueno = s.media_7d ? `media 7 días ${horas(s.media_7d)}` : null;
  const detalleSueno = [s.puntuacion ? `${s.puntuacion} puntos` : null, mediaSueno].filter(Boolean).join(" · ");
  $("fitness-recuperacion").replaceChildren(
    dato("😴 Sueño", s.segundos ? horas(s.segundos) : null, detalleSueno, s.estado),
    dato("❤️ Pulso en reposo", p.valor ? `${enteras.format(p.valor)} ppm` : null,
      p.media_7d ? `media 7 días ${unDecimal.format(p.media_7d)}` : null, p.estado),
    dato("HRV", v.valor ? `${enteras.format(Math.round(v.valor))} ms` : null,
      v.media_7d ? `media 7 días ${enteras.format(Math.round(v.media_7d))} ms` : null, v.estado));
}
