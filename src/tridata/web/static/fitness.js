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
// server's reason and nothing is repainted.
// Unit 108 (REC-51): «Tus semanas» is a line (grafica.js) in hours, km or load, and «Por zonas» (not with load) puts
// a bar of zones under each point. The week marked (the one tapped, or the one in course when Fitness opens) shows
// above the chart its dates and its distance, time and climb in large, and the panel of the week carries its bar of
// zones. «Comparar con…» asks for both periods (/api/fitness?…&otro_desde=&otro_hasta=; comparing, a «hasta» may be
// after today): two lines aligned by week number, one sentence with the total of each (the weeks still to come of
// one do not count in either) and, tapping a number, both weeks side by side with a sentence of what changes; tapping
// one opens its week. «Quitar comparación» goes back to one line. The table against the period before is gone.
// Unit 102 (REC-46): under «Recuperación», «Zapatillas» (GET /api/zapatillas): each shoe in use with its km, its cap,
// what is left (or gone over), «toca cambiarla pronto» / «pasada» and where the km come from with its date («de Strava,
// el 21/11» · «dicho por ti, hoy»), and why the last read of Strava failed if it did. «Cambiar km» and «Cambiar tope»
// open a small form in its row (POST /api/zapatillas/ID): the server checks it and says why when it does not go.
// Unit 103 (REC-47): «Dar de alta una zapatilla» (POST /api/zapatillas: name, km, cap and use) and «Retirar» in the
// rows of the shoes counted here; above the list, «¿Con qué zapatilla corriste el 26/11 (8,1 km)?» with a button per
// shoe for each run without one; and «Últimas carreras» with the shoe of each one, which a selector changes
// (POST /api/zapatillas/carrera/ID). Names always with `textContent`.
// Unit 106 (REC-50): the use «Trail» in the form; «Retirar» asks in its row («¿Retirar la Gel Nimbus 28?» Sí / No);
// the retired ones go folded into «Retiradas», each with «Volver a ponerla en activo» (POST /api/zapatillas/ID
// {activar}); and the detail of a run (`abrirActividad`) carries above its laps the line of its shoe
// (GET /api/zapatillas/carrera/ID: «25/11: 12,4 km con SonicBlast» · «Cambiar», a button per shoe), shared with Hoy
// (`lineaZapatilla`). Not a run counted here (404): no line.

import { CAPAS_ZONA, delDeporte, graficaCarga, graficaPulso, graficaSemanas, leyendaCarga, leyendaSemanas, valorSemana,
  zonasSemana } from "./grafica.js";

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
let medida = "horas"; // unit 090: what «Tus semanas» shows (unit 108: horas · km · carga)
let porZonas = false; // unit 108: «Por zonas» on (only with horas or km)
let comparando = null; // unit 108: {desde, hasta} of the second period on screen; null without comparing
let otraVista = null; // unit 108: the weeks of the second period of the last answer
let periodoOtro = null; // unit 108: its `periodo`
let historialDesde = null; // unit 108: the first activity, when a period on screen starts before it
let numeroElegido = null; // unit 108: comparing, the week number tapped
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
let retirarAbierta = null; // unit 106: the id of the shoe whose «¿Retirar…?» is open
const USOS = [["suave", "Rodaje y suave"], ["calidad", "Series, ritmo y tiradas"], ["competicion", "Competición"],
  ["trail", "Trail"]];
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
  selector("fitness-medidas", "medida", (valor) => {
    medida = valor;
    if (medida === "carga") porZonas = false; // R-311: with load there is no view by zones
    pintarMedida();
    pintarSemanas(semanasVistas);
  });
  $("fitness-por-zonas").addEventListener("click", () => { porZonas = !porZonas; pintarMedida(); pintarSemanas(semanasVistas); });
  // unit 108 (R-313): «Comparar con…», «Quitar comparación» and the two weeks of a number
  $("fitness-comparar-abrir").addEventListener("click", () => mostrarComparar($("fitness-comparar-form").hidden));
  $("fitness-comparar-form").noValidate = true;
  $("fitness-comparar-form").addEventListener("submit", (evento) => { evento.preventDefault(); aplicarComparar(); });
  $("fitness-comparar-quitar").addEventListener("click", quitarComparacion);
  $("fitness-comparar-semana").addEventListener("click", (evento) => {
    const boton = evento.target.closest("button.semana-comparada");
    if (boton) abrirSemanaDe(boton.dataset.lunes);
  });
  selector("fitness-deporte", "deporte", (valor) => {
    deporte = valor;
    pintarSemanas(semanasVistas);
    if (semanaElegida && !$("fitness-semana").hidden) cargarSemana();
  });
  // unit 093: a tap on a week (its whole column), or Enter / Space on it
  const grafica = $("fitness-grafica-semanas");
  const tocar = (g) => (otraVista ? alternarNumero(Number(g.dataset.n)) : alternarSemana(g.dataset.lunes));
  grafica.addEventListener("click", (evento) => {
    const g = evento.target.closest("g.semana");
    if (g) tocar(g);
  });
  grafica.addEventListener("keydown", (evento) => {
    const g = evento.target.closest("g.semana");
    if (!g || (evento.key !== "Enter" && evento.key !== " ")) return;
    evento.preventDefault();
    tocar(g);
  });
  $("fitness-semana-cerrar").addEventListener("click", cerrarSemana);
  // unit 107 (R2): ‹ › to the week before or after among those the chart paints, as tapping its bar
  $("fitness-semana-anterior").addEventListener("click", () => moverSemana(-1));
  $("fitness-semana-siguiente").addEventListener("click", () => moverSemana(1));
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
  if (hoyISO) { $("fitness-desde").max = hoyISO; $("fitness-hasta").max = comparando ? "" : hoyISO; } // R-316
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
// Unit 108: `otroCandidato` ({desde, hasta}) a second period still to be accepted (undefined: the one on screen).
export async function cargarFitness(candidatas = null, otroCandidato = undefined) {
  const mia = ++peticion;
  const elegidas = candidatas ?? fechas;
  const otro = otroCandidato === undefined ? comparando : otroCandidato;
  let consulta = elegidas ? `desde=${encodeURIComponent(elegidas.desde)}&hasta=${encodeURIComponent(elegidas.hasta)}` : `dias=${dias}`;
  if (otro) consulta += `&otro_desde=${encodeURIComponent(otro.desde)}&otro_hasta=${encodeURIComponent(otro.hasta)}`;
  const { ok, datos } = await api(`/api/fitness?${consulta}`);
  if (mia !== peticion) return;
  if (!ok) {
    if (otroCandidato) { errorComparar(datos.error ?? "Esas fechas no valen."); return; } // R-316: as it was
    if (candidatas) { errorFechas(datos.error ?? "Esas fechas no valen."); return; }
    $("fitness-pulso").textContent = datos.error ?? "No he podido cargar Fitness.";
    return;
  }
  if (candidatas) { fechas = candidatas; marcarRango("fechas"); }
  if (otroCandidato !== undefined) {
    comparando = otroCandidato;
    numeroElegido = null;
    if (comparando) mostrarComparar(false);
  }
  hoyISO = datos.fecha;
  periodoVisto = datos.periodo ?? null;
  otraVista = datos.otro?.semanas ?? null;
  periodoOtro = datos.otro?.periodo ?? null;
  historialDesde = datos.historial_desde ?? datos.otro?.historial_desde ?? null;
  if (otraVista && numeroElegido && numeroElegido > Math.max(otraVista.length, (datos.semanas ?? []).length)) numeroElegido = null;
  $("fitness-comparar-quitar").hidden = !comparando;
  pintarCarga(datos.carga);
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
  pintarRetiradas(d.retiradas ?? []);
  pintarCarreras(d.ultimas ?? [], d.opciones ?? []);
}

// unit 106 (R-305): the shoes here retired, folded; «Volver a ponerla en activo» brings it back with its km
function pintarRetiradas(retiradas) {
  $("fitness-retiradas").hidden = retiradas.length === 0;
  $("fitness-retiradas-lista").replaceChildren(...retiradas.map((z) => {
    const volver = el("button", { type: "button", textContent: "Volver a ponerla en activo" });
    const error = el("p", { className: "aviso", role: "alert", hidden: true });
    volver.addEventListener("click", async () => {
      volver.disabled = true;
      const { ok, datos } = await api(`/api/zapatillas/${encodeURIComponent(z.id)}`, { activar: true });
      if (ok) { pintarZapatillas(datos); return; }
      error.textContent = datos.error ?? "No se ha podido guardar.";
      error.hidden = false;
      volver.disabled = false;
    });
    const fila = el("li", { className: "zapatilla" },
      el("div", { className: "cab-zapatilla" }, el("strong", { textContent: z.nombre })),
      el("p", { className: "num cifras-zapatilla",
        textContent: `${enteras.format(Math.round(z.km))} de ${enteras.format(z.tope)} km · ${restoZapatilla(z)}` }),
      volver, error);
    fila.dataset.id = z.id;
    return fila;
  }));
}

// unit 106 (R-308, R-309): the line of a run with its shoe and «Cambiar» (or «Sin zapatilla» and «Elegir»); tapping
// it shows a button per shoe in use, and one tap gives it that shoe. `elegir(carrera, zapatilla)` saves it and returns
// an error text, or null (the caller repaints). Shared with Hoy.
export function lineaZapatilla(l, elegir) {
  const error = el("p", { className: "aviso", role: "alert", hidden: true });
  const opciones = el("div", { className: "opciones-zapatilla", hidden: true });
  const botones = l.opciones.map((o) => {
    const boton = el("button", { type: "button", textContent: o.nombre });
    boton.addEventListener("click", async () => {
      for (const b of botones) b.disabled = true;
      const fallo = await elegir(l.activity_id, o.id);
      if (fallo) { error.textContent = fallo; error.hidden = false; for (const b of botones) b.disabled = false; }
    });
    return boton;
  });
  opciones.append(...botones);
  const abrir = el("button", { type: "button", textContent: l.boton });
  abrir.setAttribute("aria-expanded", "false");
  abrir.addEventListener("click", () => {
    opciones.hidden = !opciones.hidden;
    abrir.setAttribute("aria-expanded", String(!opciones.hidden));
  });
  const linea = el("div", { className: `linea-zapatilla${l.zapatilla_id ? "" : " sin"}` },
    el("div", { className: "cab-linea-zapatilla" }, el("p", { className: "num", textContent: l.texto }), abrir),
    opciones, error);
  linea.dataset.carrera = l.activity_id;
  return linea;
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
      textContent: c.texto_ya_contada ?? c.texto_origen ?? "sin zapatilla" }); // unit 106 (R-307)
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
  if (z.fuente === "aqui" && retirarAbierta === z.id) { // unit 106 (R-305): it asks first, in its row
    fila.append(confirmarRetirar(z));
    return fila;
  }
  if (z.fuente === "aqui") { // unit 103 (R-292): out of the list; what it counted stays with it
    const retirar = el("button", { type: "button", textContent: "Retirar" });
    retirar.addEventListener("click", () => { retirarAbierta = z.id; pintarZapatillas(zapatillasVistas); });
    acciones.append(retirar);
  }
  fila.append(acciones);
  return fila;
}

// unit 106 (R-305, C-388): «¿Retirar la Gel Nimbus 28?» Sí / No; «No» changes nothing
function confirmarRetirar(z) {
  const si = el("button", { type: "button", className: "principal", textContent: "Sí" });
  const no = el("button", { type: "button", textContent: "No" });
  const error = el("p", { className: "aviso", role: "alert", hidden: true });
  no.addEventListener("click", () => { retirarAbierta = null; pintarZapatillas(zapatillasVistas); });
  si.addEventListener("click", async () => {
    si.disabled = no.disabled = true;
    const { ok, datos } = await api(`/api/zapatillas/${encodeURIComponent(z.id)}`, { retirar: true });
    if (!ok) {
      error.textContent = datos.error ?? "No se ha podido retirar.";
      error.hidden = false;
      si.disabled = no.disabled = false;
      return;
    }
    retirarAbierta = null;
    pintarZapatillas(datos);
  });
  queueMicrotask(() => no.focus());
  return el("div", { className: "confirmar-retirar", role: "group" },
    el("p", { textContent: `¿Retirar la ${z.nombre}?` }), el("div", { className: "acciones" }, si, no), error);
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

const zonasActivas = () => porZonas && medida !== "carga";

// Unit 108: «Por zonas» only with hours or km, pressed or not
function pintarMedida() {
  const boton = $("fitness-por-zonas");
  boton.hidden = medida === "carga";
  boton.setAttribute("aria-pressed", String(zonasActivas()));
}

function pintarSemanas(semanas) {
  semanasVistas = semanas;
  const caja = $("fitness-grafica-semanas");
  const enfocada = caja.contains(document.activeElement)
    ? (otraVista ? `[data-n="${document.activeElement.dataset?.n}"]` : `[data-lunes="${document.activeElement.dataset?.lunes}"]`) : null;
  // R-312: the week marked is the one open, or the one in course
  const marcada = otraVista ? null : semanaElegida ?? semanas.find((s) => s.en_curso)?.lunes ?? semanas.at(-1)?.lunes ?? null;
  const largo = Math.max(periodoVisto?.semanas ?? 0, periodoOtro?.semanas ?? 0);
  const aproximado = semanas.some((s) => s.aproximado) || (otraVista ?? []).some((s) => s.aproximado);
  caja.replaceChildren(graficaSemanas(semanas, medida, caja.clientWidth - 8, deporte, semanaElegida,
    { porZonas: zonasActivas(), otra: otraVista, largo, marcada, numero: numeroElegido }),
  leyendaSemanas(medida, deporte, aproximado,
    { porZonas: zonasActivas(), periodos: otraVista ? [periodoCorto(periodoVisto), periodoCorto(periodoOtro)] : null }));
  if (enfocada) caja.querySelector(`g.semana${enfocada}`)?.focus(); // the keyboard keeps its place
  pintarCabecera(marcada);
  pintarFrase();
  pintarDoble();
  pintarFlechas();
}

// -- unit 108: the week marked in large, the comparison of two periods (R-312..R-316) -------------------------------

const diaMesSinPunto = (iso) => diaMesCorto.format(new Date(`${iso}T12:00:00`)).replace(".", "");

// «9 nov – 15 nov 2026» (with both years when the week goes from one to the other)
function rangoSemana(lunes, domingo) {
  const [a1, a2] = [lunes.slice(0, 4), domingo.slice(0, 4)];
  return a1 === a2 ? `${diaMesSinPunto(lunes)} – ${diaMesSinPunto(domingo)} ${a2}`
    : `${diaMesSinPunto(lunes)} ${a1} – ${diaMesSinPunto(domingo)} ${a2}`;
}

// «6h 45min», as Strava
function tiempoStrava(horasSemana) {
  const m = Math.round((horasSemana ?? 0) * 60);
  return m < 60 ? `${m}min` : `${Math.floor(m / 60)}h ${m % 60}min`;
}

// Distance (2 decimals), time and climb of a week for the sport chosen; «—» where there is nothing to say
function cifrasDe(s) {
  const d = delDeporte(s, deporte);
  return [["Distancia", d.km === null || d.km === undefined ? "—" : `${dosDecimales.format(d.km)} km`],
    ["Tiempo", tiempoStrava(d.horas)],
    ["Desnivel", d.subida_m === null || d.subida_m === undefined ? "—" : `${enteras.format(d.subida_m)} m`]];
}

const datosCabecera = (s) => el("div", { className: "datos-cabecera" }, ...cifrasDe(s).map(([titulo, valor]) =>
  el("div", { className: "dato-cabecera" }, el("span", { textContent: titulo }), el("strong", { className: "num", textContent: valor }))));

function pintarCabecera(lunes) {
  const caja = $("fitness-semana-cabecera");
  const s = lunes ? semanasVistas.find((x) => x.lunes === lunes) : null;
  caja.hidden = !s;
  if (!s) { caja.replaceChildren(); return; }
  caja.replaceChildren(
    el("p", { className: "fechas-cabecera" }, rangoSemana(s.lunes, s.domingo),
      s.en_curso ? el("span", { className: "suave", textContent: " · en curso" }) : null),
    datosCabecera(s));
}

// R-312: the bar of zones of a week, in the measure (load: hours), with its figures in text
function barraZonasPanel(s) {
  const en = medida === "km" ? "km" : "horas";
  const partes = zonasSemana(s, en, deporte);
  if (!partes.length) return el("p", { className: "suave sin-zonas-semana", textContent: "Sin zonas esta semana." });
  const total = partes.reduce((a, [, v]) => a + v, 0);
  const texto = (v) => (en === "km" ? `${unDecimal.format(v)} km` : minutos(v * 3600));
  const nombre = (clave) => CAPAS_ZONA.find(([c]) => c === clave)[1];
  const barra = el("div", { className: "barra-zona-panel", role: "img",
    ariaLabel: `Por zonas: ${partes.map(([c, v]) => `${nombre(c)} ${texto(v)}`).join(", ")}` },
  ...partes.map(([clave, v]) => {
    const trozo = el("span", { className: `trozo-zona capa-${clave}`, title: `${nombre(clave)}: ${texto(v)}` });
    trozo.style.flexGrow = String(v / total);
    return trozo;
  }));
  return el("div", { className: "zonas-semana-panel" }, barra,
    el("small", { className: "suave", textContent: `${partes.map(([c, v]) => `${nombre(c)} ${texto(v)}`).join(" · ")} · aproximado por vueltas` }));
}

// «1/9–6/12/2026» (each end with its year when they are of different years)
function periodoCorto(p) {
  if (!p) return "";
  const [a1, m1, d1] = p.desde.split("-").map(Number), [a2, m2, d2] = p.hasta.split("-").map(Number);
  return a1 === a2 ? `${d1}/${m1}–${d2}/${m2}/${a2}` : `${d1}/${m1}/${a1}–${d2}/${m2}/${a2}`;
}

// How the first period is called in the sentences: «este año» against another year, else «este periodo»
function nombreEste() {
  const a = periodoVisto?.desde.slice(0, 4), b = periodoOtro?.desde.slice(0, 4);
  if (a === b) return "este periodo";
  return a === hoyISO?.slice(0, 4) ? "este año" : `en ${a}`;
}

const UNIDAD = { horas: "h", km: "km", carga: "de carga" };
const cantidad = (v) => `${medida === "carga" ? enteras.format(Math.round(v)) : hastaUnDecimal.format(v)} ${UNIDAD[medida]}`;
function conSignoMedida(v) {
  const texto = cantidad(Math.abs(v));
  return cantidad(0) === texto ? texto : `${v > 0 ? "+" : "−"}${texto}`;
}

function minutosConSigno(segundos) {
  const m = Math.round(Math.abs(segundos) / 60);
  const texto = m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  return `${segundos > 0 ? "+" : "−"}${texto}`;
}

// R-314: the total of each period and the difference in one sentence; the weeks still to come of one period do not
// count in either (the two are compared up to the same week). R-316: the history warning.
function pintarFrase() {
  const frase = $("fitness-comparar-frase"), aviso = $("fitness-comparar-aviso");
  aviso.hidden = !(otraVista && historialDesde);
  aviso.textContent = otraVista && historialDesde
    ? `Ojo: tu historial empieza el ${fechaLarga(historialDesde)}; antes no hay datos que comparar.` : "";
  frase.hidden = !otraVista;
  if (!otraVista) { frase.textContent = ""; return; }
  if (deporte === "fuerza" && medida === "km") { frase.textContent = "La fuerza no suma km."; return; }
  const nA = semanasVistas.length, nB = otraVista.length;
  const corte = Math.min(nA < (periodoVisto?.semanas ?? nA) ? nA : Infinity, nB < (periodoOtro?.semanas ?? nB) ? nB : Infinity);
  const cuentaA = Math.min(nA, corte), cuentaB = Math.min(nB, corte);
  const suma = (lista, k) => lista.slice(0, k).reduce((a, s) => a + valorSemana(s, medida, deporte), 0);
  const a = suma(semanasVistas, cuentaA), b = suma(otraVista, cuentaB);
  const semanasTexto = (k) => `${k} ${k === 1 ? "semana" : "semanas"}`;
  const pct = b ? Math.round(((a - b) / b) * 100) : null;
  const pctTexto = pct === null ? "—" : `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${enteras.format(Math.abs(pct))} %`;
  frase.textContent = `${periodoCorto(periodoVisto)}: ${cantidad(a)} en ${semanasTexto(cuentaA)} · `
    + `${periodoCorto(periodoOtro)}: ${cantidad(b)} en ${semanasTexto(cuentaB)} → ${nombreEste()} ${conSignoMedida(a - b)} (${pctTexto})`;
}

function alternarNumero(n) {
  if (!n) return;
  numeroElegido = numeroElegido === n ? null : n;
  pintarSemanas(semanasVistas);
  if (numeroElegido) $("fitness-comparar-semana").scrollIntoView({ block: "nearest" });
}

function tarjetaComparada(s, etiqueta, serie) {
  const boton = el("button", { type: "button", className: `semana-comparada serie-${serie}` },
    el("span", { className: "periodo-comparado", textContent: etiqueta }),
    el("strong", { className: "fechas-cabecera", textContent: rangoSemana(s.lunes, s.domingo) }),
    datosCabecera(s), barraZonasPanel(s),
    el("span", { className: "ver-semana-comparada", textContent: "Ver sus actividades ›" }));
  boton.dataset.lunes = s.lunes;
  return boton;
}

// R-315, R-316: the two weeks of the number tapped side by side, and a sentence: the difference in the measure and
// the zone whose time changed most; a period without that week says why.
function pintarDoble() {
  const caja = $("fitness-comparar-semana");
  if (!otraVista || !numeroElegido) { caja.hidden = true; caja.replaceChildren(); return; }
  const n = numeroElegido, a = semanasVistas[n - 1], b = otraVista[n - 1];
  const falta = (p, propio) => (n > (p?.semanas ?? 0)
    ? (propio ? `tu periodo no llega a la sem ${n}` : `el otro periodo no llega a la sem ${n}`)
    : `la sem ${n} ${propio ? "de tu periodo" : "del otro periodo"} aún no ha llegado`);
  const nodos = [];
  if (a && b) {
    const dif = valorSemana(a, medida, deporte) - valorSemana(b, medida, deporte);
    let texto = `sem ${n}: ${nombreEste()} ${deporte === "fuerza" && medida === "km" ? "la fuerza no suma km" : conSignoMedida(dif)}`;
    const za = delDeporte(a, deporte).zonas ?? {}, zb = delDeporte(b, deporte).zonas ?? {};
    const [clave, cambio] = CAPAS_ZONA.slice(0, 5).map(([c]) => [c, (za[c] ?? 0) - (zb[c] ?? 0)])
      .reduce((m, x) => (Math.abs(x[1]) > Math.abs(m[1]) ? x : m), ["z1", 0]);
    if (Math.abs(cambio) >= 60) texto += ` y ${minutosConSigno(cambio)} en ${clave.toUpperCase()}`;
    nodos.push(el("p", { className: "frase-semana-comparada", textContent: texto }));
  } else {
    nodos.push(el("p", { className: "suave falta-semana-comparada", textContent: !a ? falta(periodoVisto, true) : falta(periodoOtro, false) }));
  }
  nodos.push(el("div", { className: "semanas-comparadas" },
    a ? tarjetaComparada(a, periodoCorto(periodoVisto), "a") : null,
    b ? tarjetaComparada(b, periodoCorto(periodoOtro), "b") : null));
  caja.replaceChildren(...nodos);
  caja.hidden = false;
}

// Tapping one of the two weeks opens its panel, as tapping a point (R-312)
function abrirSemanaDe(lunes) {
  if (!lunes) return;
  semanaElegida = lunes;
  pintarFlechas();
  cargarSemana();
  $("fitness-semana").scrollIntoView({ block: "nearest" });
}

function mostrarComparar(abierto) {
  $("fitness-comparar-form").hidden = !abierto;
  $("fitness-comparar-abrir").setAttribute("aria-expanded", String(abierto));
  $("fitness-comparar-error").hidden = true;
  if (!abierto || !periodoVisto) return;
  $("fitness-este-desde").value = periodoVisto.desde;
  $("fitness-este-hasta").value = periodoVisto.hasta;
  const unAnoAntes = (iso) => `${Number(iso.slice(0, 4)) - 1}${iso.slice(4)}`.replace("-02-29", "-02-28");
  $("fitness-otro-desde").value = comparando?.desde ?? unAnoAntes(periodoVisto.desde);
  $("fitness-otro-hasta").value = comparando?.hasta ?? unAnoAntes(periodoVisto.hasta);
}

function errorComparar(texto) {
  $("fitness-comparar-error").textContent = texto;
  $("fitness-comparar-error").hidden = false;
}

async function aplicarComparar() {
  const valor = (id) => $(id).value;
  const este = { desde: valor("fitness-este-desde"), hasta: valor("fitness-este-hasta") };
  const otro = { desde: valor("fitness-otro-desde"), hasta: valor("fitness-otro-hasta") };
  if (!este.desde || !este.hasta || !otro.desde || !otro.hasta) { errorComparar("Elige las cuatro fechas."); return; }
  $("fitness-comparar-error").hidden = true;
  await cargarFitness(este, otro);
}

// R-313: back to one line; a «hasta» after today goes back to today (without comparing it cannot pass it)
async function quitarComparacion() {
  numeroElegido = null;
  if (fechas && hoyISO && fechas.hasta > hoyISO) {
    const largo = Math.round((new Date(`${hoyISO}T12:00:00Z`) - new Date(`${fechas.desde}T12:00:00Z`)) / 86_400_000) + 1;
    if (largo >= 7) {
      fechas = { desde: fechas.desde, hasta: hoyISO };
    } else {
      fechas = null;
      dias = 42;
      marcarRango("42");
    }
  }
  mostrarComparar(false);
  await cargarFitness(null, null);
}

// -- unit 093: the week tapped, its activities and the session of one ------------------------------------------------

function alternarSemana(lunes) {
  if (!lunes) return;
  if (semanaElegida === lunes) { cerrarSemana(); return; }
  semanaElegida = lunes;
  pintarSemanas(semanasVistas);
  cargarSemana();
}

// Unit 107 (R2): the week `paso` places away from the open one, among `semanasVistas`; at an end, nothing.
function moverSemana(paso) {
  const i = semanasVistas.findIndex((s) => s.lunes === semanaElegida);
  const otra = i < 0 ? null : semanasVistas[i + paso];
  if (!otra) return;
  semanaElegida = otra.lunes;
  pintarSemanas(semanasVistas);
  cargarSemana();
}

// ‹ off on the first week of the range, › on the last (the one in course)
function pintarFlechas() {
  const i = semanasVistas.findIndex((s) => s.lunes === semanaElegida);
  $("fitness-semana-anterior").disabled = i <= 0;
  $("fitness-semana-siguiente").disabled = i < 0 || i >= semanasVistas.length - 1;
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
  // unit 108 (R-312): its bar of zones, from the weeks already on screen (of either period)
  const semana = [...semanasVistas, ...(otraVista ?? [])].find((s) => s.lunes === lunes);
  $("fitness-semana-zonas").replaceChildren(...(semana ? [barraZonasPanel(semana)] : []));
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

// «1/9/2026»
const fechaLarga = (iso) => { const [a, m, d] = iso.split("-").map(Number); return `${d}/${m}/${a}`; };

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
// Unit 106 (R-309): above everything, the line of its shoe when it is a run counted here (a 404 says it is not).
async function abrirActividad(id, caja, sigue) {
  caja.replaceChildren(el("p", { className: "suave", textContent: "Cargando las vueltas…" }));
  caja.hidden = false;
  const [{ ok, datos }, zapatilla] = await Promise.all([api(`/api/actividad/${encodeURIComponent(id)}`),
    api(`/api/zapatillas/carrera/${encodeURIComponent(id)}`)]);
  if (!sigue()) return;
  if (!ok) { caja.replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido cargarla." })); return; }
  pintarDetalle(datos, caja);
  if (zapatilla.ok) caja.prepend(lineaDeCarrera(zapatilla.datos, caja, sigue));
}

// The line of the detail: choosing repaints it (and the shoes above), in its place
function lineaDeCarrera(linea, caja, sigue) {
  const nodo = lineaZapatilla(linea, async (carrera, zapatilla) => {
    const { ok, datos } = await api(`/api/zapatillas/carrera/${encodeURIComponent(carrera)}`, { zapatilla });
    if (!ok) return datos.error ?? "No se ha podido guardar.";
    pintarZapatillas(datos);
    const otra = await api(`/api/zapatillas/carrera/${encodeURIComponent(carrera)}`);
    if (otra.ok && sigue() && nodo.isConnected) nodo.replaceWith(lineaDeCarrera(otra.datos, caja, sigue));
    return null;
  });
  return nodo;
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
    const unidad = v.cadencia_unidad ?? ("swolf" in v ? "brazadas/min" : "ppm"); // unit 107: a ride's is «rpm»
    partes.push(v.cadencia !== null ? `cadencia ${enteras.format(v.cadencia)} ${unidad}` : falta("cadencia"));
  }
  if ("zancada_m" in v) partes.push(v.zancada_m !== null ? `zancada ${dosDecimales.format(v.zancada_m)} m` : falta("zancada"));
  if ("swolf" in v) partes.push(v.swolf !== null ? `SWOLF ${enteras.format(v.swolf)}` : falta("SWOLF"));
  if ("subida_m" in v) partes.push(v.subida_m !== null ? `subida +${enteras.format(v.subida_m)} m` : falta("subida"));
  return partes.length ? el("small", { className: "extra-vuelta", textContent: partes.join(" · ") }) : null;
}

export function filaVuelta(v) {
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
  // unit 110 (R7, C-420): one imported from Strava's export says so
  if (d.actividad.fuente === "strava") nodos.unshift(el("p", { className: "suave fuente-sesion", textContent: "de Strava" }));
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
