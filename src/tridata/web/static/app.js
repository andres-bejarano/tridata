// Day page of the food log. Plain ES module, no build step. The server computes every total.
// A saved food is changed, moved, deleted or marked eaten by tapping its row in the day table.
// Every card reads its inputs when "Guardar" is pressed, not on `change`.
// Unit 008: the bar at the top, the full expenditure with Garmin and its breakdown, the
// «Actualizar Garmin» button, today's objective, levels and planned training.
// Unit 080 (P-20261105-cf32bea5): the app calls no AI. Gone are the box to write or dictate (with its review of what
// was interpreted, the assistant's cards and the recordings queue of cola.js, which is no longer loaded: what an old
// phone kept there is never sent) and the Asistente tab. Four tabs: Hoy · Nutrición · Fitness · Ajustes. Foods are
// added by hand («Añadir») or by the coach from the PC. The service worker (sw.js) lets the page open with the PC off.
// Unit 010 (presentation only, no rule changes): Hoy, Peso and Recetas are sections of this page
// chosen by #dia / #balance / #recetas (unit 029); the «Te quedan» card shows in big the level being looked at
// (remembered on this phone) with the others as buttons; everything that adjusts the day lives in
// the sheet «⚙ Ajustar el día» (<dialog id="hoja">); the day is a list Comido / Planeado. The Peso
// tab is drawn by peso.js, always for today. Unit 027: the Peso tab is inside Balance (balance.js calls peso.js);
// an old #peso opens #balance.
// Unit 011: the Recetas tab is drawn by recetas.js. A food that comes from a saved recipe or fixed
// food says so («de tu receta …», «provisional») in the day list.
// Unit 014: under the day list, the state of the day (open, food closed, waiting for Garmin, closed with
// Garmin) with its balance and the week, the trail of Garmin's corrections, and «Cerrar el día» while it is
// open (never on a later day). Today shows the relevant corrections in #avisos until «Entendido». The
// breakdown says when the kcal per step were calibrated with Garmin (or why that was discarded).
// Unit 016: every kcal on screen in whole numbers (the server keeps them exact; the kcal inputs too); the
// page asks the day again when it comes back to the front and every minute in sight, and past midnight it
// moves from the old today to the new one; an open card survives a repaint.
// Unit 019: in «Añadir a mano» the name is the search box of buscador.js; choosing a
// saved recipe or ingredient leaves only how much (grams, units, or the whole recipe / ½ / ⅓ / ¼) with its kcal at
// once, and the food goes with `guardado: {tipo, id}` so the server logs it with ITS kcal. «Cambiar» goes back to
// the free fields. A fraction reads «la receta entera», «½ receta»…; an origin «de tu alimento fijo …» logged
// before the rename reads «de tu ingrediente …» (the base is not rewritten).
// Unit 030: the app opens in Hoy (hoy.js paints its cards from GET /api/hoy); `/`, `#hoy` and reloading in Hoy
// stay in Hoy.
// Unit 031: the Fitness tab is painted by fitness.js (GET /api/fitness) every time it is entered.
// Unit 040: Fitness has two parts, like Nutrición: Forma (`#fitness`, unit 031 unchanged) and Plan (`#fitness/plan`,
// plan.js); each is painted when it shows.
// Unit 035: the day list goes by the sections Andrés creates (desayuno … cena, then «Sin sección»), each with its
// eaten kcal; they replace the groups Comido / Planeado. A section is there when a food is in it, or when it was
// just created with «Añadir sección» and the day has not changed (S-45: it only lives on this screen). Each row is
// dragged from its handle ⠿ (pointer events, so it works with the finger on the iPhone) and dropped on another
// section: POST /api/alimento/N/seccion saves only the section (G-52) and the day comes back painted.

import { pintarBalance } from "./balance.js";
import { buscador, cajaElegida, cargarGuardado, leerOrigen, textoReceta } from "./buscador.js";
import { pintarRecetas } from "./recetas.js";
import { cargarHoy, iniciarHoy } from "./hoy.js";
import { cargarFitness, iniciarFitness } from "./fitness.js";
import { cargarPlan, iniciarPlan } from "./plan.js";

const $ = (id) => document.getElementById(id);
// kcal with a thousands point always («1.040»): es-ES does not group 4 digits by default
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, useGrouping: "always" });
// unit 016 (R8): kcal are shown whole («1.790», «17»); only shown: what is saved and the inputs stay exact
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const kcalEnteras = (n) => enteras.format(Math.round(n) === 0 ? 0 : n); // never «−0»
const porPaso = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 3 }); // 0,04 kcal/paso must not read 0
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const signo = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + numero.format(Math.abs(n));
const etiquetaNivel = (n) => (n === 0 ? "Mantenimiento" : `${signo(n)} %`);
const conSignoKcal = (n) => (Math.round(n) < 0 ? `−${kcalEnteras(-n)}` : kcalEnteras(Math.max(n, 0)));
const signoKcal = (n) => (Math.round(n) > 0 ? "+" : Math.round(n) < 0 ? "−" : "") + kcalEnteras(Math.abs(n));
const MINUTO_MS = 60_000; // unit 016 (R3): how often the day is asked again while it is in sight
// unit 080: four tabs (Hoy · Nutrición · Fitness · Ajustes, the last one its own page); Nutrición has three parts,
// Día · Balance · Recetas (unit 027: Peso is inside Balance). Each place has its address; an old #asistente opens Día.
const SITIOS = ["hoy", "dia", "balance", "recetas", "fitness"];
const NUTRICION = ["dia", "balance", "recetas"];
const CLAVE_NIVEL = "tridata.nivel-que-miras"; // a screen preference of this phone, never the objective
let hoyISO = null; // today, as the server says in every day it sends (unit 016: it moves at midnight)
let diaVisto = null; // the day on screen (ISO)
let ultimoDia = null;
let editando = null; // id of the saved food whose row is open for change
let anadiendo = false; // the "add by hand" card under the table is open
let fichaAbierta = null; // unit 016 (R4): { clave, nodo } of the open card, kept between repaints
let peticionDia = 0; // drops a late answer when ◀ ▶ are tapped fast
let conectado = null; // whether the PC answered the last request (null: not known yet)
let pestanaVista = null; // the place on screen, to reload the day when coming back to Día (unit 013)
let parteFitness = null; // unit 040: "forma" or "plan", the part of Fitness on screen
let diaPedido = null; // unit 017: a day tapped in Balance, opened when Día shows up
// unit 035: the sections in the order of the day; "" is «Sin sección», always last. Unit 074 (R-212, S-71): the
// sixth, «Nutrición en carrera», after dinner
const SECCIONES = [["desayuno", "Desayuno"], ["media_manana", "Media mañana"], ["comida", "Comida"],
  ["merienda", "Merienda"], ["cena", "Cena"], ["carrera", "Nutrición en carrera"]];
const NOMBRE_SECCION = Object.fromEntries([...SECCIONES, ["", "Sin sección"]]);
let seccionesNuevas = new Set(); // unit 035 (S-45): sections just created, still empty, of the day on screen
let eligiendoSeccion = false; // unit 035: «Añadir sección» open: false · "elegir" · "todas"
let arrastre = null; // unit 035: { a, fila, asa, fantasma, destino, dy, puntero } while a row is dragged
let diaTrasArrastre = null; // a day that came while dragging: painted when the drag ends

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const kcal = (n) => (n === null || n === undefined ? "—" : `${kcalEnteras(n)} kcal`);
const cantidad = (a) => {
  if (a.cantidad === null) return "sin cantidad";
  if (a.unidad === "receta") return textoReceta(a.cantidad); // unit 019: «la receta entera», «½ receta»
  return `${numero.format(a.cantidad)} ${a.unidad ?? ""}`.trim();
};
const deFecha = (iso) => fechaLarga.format(new Date(`${iso}T12:00:00`)).replace(",", "");
const sumarDias = (iso, n) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const mayuscula = (t) => t.charAt(0).toUpperCase() + t.slice(1);
// the phone's own date (AAAA-MM-DD), shown until the PC answers
const fechaLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

async function api(ruta, cuerpo) {
  const opciones = cuerpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo),
  };
  try {
    const r = await fetch(ruta, opciones);
    const datos = await r.json().catch(() => ({}));
    ponerConexion(r.ok || Boolean(datos.error)); // a proxy page (PC off behind tailscale) is no PC
    return { ok: r.ok, estado: r.status, datos };
  } catch {
    ponerConexion(false);
    return { ok: false, estado: 0, datos: { error: "No hay conexión con el PC. Inténtalo de nuevo." } };
  }
}

function ponerConexion(hay) {
  const volvio = hay && conectado === false;
  conectado = hay;
  $("sin-conexion").hidden = hay;
  if (volvio) cargarDia(diaVisto);
}

function mensaje(texto, tipo = "") {
  $("mensaje").textContent = texto;
  $("mensaje").className = tipo;
}

// A day without basal or kcal per step (409): the message of the server and a way to Ajustes.
function mensajeAjustes(texto) {
  mensaje(texto, "error");
  $("mensaje").append(" ", el("a", { href: "/ajustes", className: "enlace", textContent: "Ir a Ajustes ›" }));
}

// -- the level being looked at (unit 010) ---------------------------------------------

function nivelRecordado() {
  try {
    const v = localStorage.getItem(CLAVE_NIVEL);
    return v === null ? null : Number(v);
  } catch {
    return null;
  }
}

function recordarNivel(n) {
  try { localStorage.setItem(CLAVE_NIVEL, String(n)); } catch { /* private mode: only for this visit */ }
}

let nivelDeLaVisita = null; // if storage fails, the choice still holds until the page is closed

// The remembered one if it is still a level of the day; else the one marked as the objective of
// the day; else the one equal to today's objective %; else the first.
function nivelMirado(dia) {
  const niveles = dia.margenes.map((m) => m.nivel);
  for (const n of [nivelRecordado(), nivelDeLaVisita]) if (n !== null && niveles.includes(n)) return n;
  const marcado = dia.margenes.find((m) => m.objetivo);
  if (marcado) return marcado.nivel;
  if (niveles.includes(dia.objetivo.porcentaje)) return dia.objetivo.porcentaje;
  return niveles[0];
}

function mirar(n) {
  nivelDeLaVisita = n;
  recordarNivel(n);
  if (ultimoDia) { pintarTarjeta(ultimoDia); pintarSegmentado(ultimoDia); pintarConPlaneado(ultimoDia); }
}

// -- the day --------------------------------------------------------------------

function nombreDelDia(iso) {
  if (iso === hoyISO) return "Hoy";
  if (iso === sumarDias(hoyISO, -1)) return "Ayer";
  if (iso === sumarDias(hoyISO, 1)) return "Mañana";
  return mayuscula(deFecha(iso));
}

// The plan of the day being looked at, from the settings in force that day (unit 005).
function textoPlan(plan) {
  if (!plan || plan.objetivo_pct === null) return "Plan: sin objetivo fijado (en Ajustes).";
  const n = plan.objetivo_pct;
  const objetivo = n === 0 ? "mantenimiento" : `${n > 0 ? "+" : "−"}${numero.format(Math.abs(n))} %`;
  const motivo = plan.motivo.startsWith("carrera ") ? ` por la ${plan.motivo}` : "";
  const limite = plan.limite_saludable_pct === null ? "" : ` · límite saludable ${numero.format(plan.limite_saludable_pct)} %`;
  return `Plan: ${objetivo}${motivo}${limite}.`;
}

function pintarDia(dia) {
  const anterior = hoyISO;
  hoyISO = dia.hoy ?? hoyISO ?? dia.fecha;
  // unit 016 (R3): midnight passed while looking at today: go to the new today (another day stays)
  if (anterior !== null && anterior !== hoyISO && dia.fecha === anterior && diaVisto === anterior) {
    cargarDia(hoyISO);
    return;
  }
  if (dia.fecha !== diaVisto) { editando = null; anadiendo = false; seccionesNuevas = new Set(); eligiendoSeccion = false; }
  diaVisto = dia.fecha;
  ultimoDia = dia;
  // unit 018 (R12): until a day is painted ◀ ▶ have no day to move from; then the page says which one it shows
  for (const id of ["anterior", "siguiente", "ir-hoy"]) $(id).disabled = false;
  document.body.dataset.dia = dia.fecha;
  const esHoy = dia.fecha === hoyISO;
  $("titulo").textContent = nombreDelDia(dia.fecha);
  if (pestanaActual() === "dia") document.title = `TriData · ${nombreDelDia(dia.fecha)}`;
  $("fecha").textContent = deFecha(dia.fecha);
  $("ir-hoy").hidden = esHoy;
  $("t-comido-dia").textContent = esHoy ? "Tu día" : mayuscula(deFecha(dia.fecha));
  $("vacio").textContent = esHoy ? "Aún no has apuntado nada hoy." : `No hay nada apuntado el ${deFecha(dia.fecha)}.`;
  $("total").textContent = `${kcalEnteras(dia.total_kcal)} kcal comidas`;
  const previstos = previstosDe(dia).length; // unit 047: several planned trainings
  const entrenos = previstos === 0 ? "" : previstos === 1 ? " · entreno" : ` · ${previstos} entrenos`;
  $("ajustar-resumen").textContent = `gasto ${kcalEnteras(dia.gasto.kcal)}${entrenos} ›`;
  pintarTarjeta(dia);
  pintarBarra(dia);
  pintarHoja(dia, esHoy);
  pintarLista(dia);
  pintarCierre(dia, esHoy);
  pintarAvisoSync(dia, esHoy);
}

// -- unit 018: Garmin is not synced (R-91) -------------------------------------------------------

function pintarAvisoSync(dia, esHoy) {
  for (const viejo of $("avisos").querySelectorAll(".aviso-sync")) viejo.remove();
  const s = dia.garmin_sync;
  if (!esHoy || !s || !s.aviso) return;
  const boton = el("button", { type: "button", id: "aviso-sync-actualizar", className: "boton", textContent: "Actualizar Garmin" });
  boton.addEventListener("click", actualizarGarmin);
  $("avisos").prepend(el("div", { className: "aviso-sync", role: "status" },
    el("p", { textContent: s.texto }), boton, el("p", { id: "aviso-sync-error", className: "suave error", hidden: true })));
}

// -- unit 014: the close of the day -------------------------------------------------

function pintarCierre(dia, esHoy) {
  const c = dia.cierre;
  const futuro = dia.fecha > hoyISO;
  $("cierre").hidden = futuro || !c;
  $("cerrar-dia").hidden = futuro || !c || c.estado !== "abierto" || dia.alimentos.length === 0;
  if (futuro || !c) return;
  $("cierre").dataset.estado = c.espera_motivo ? "espera" : c.estado;
  $("cierre-estado").textContent = c.texto;
  $("cierre-balance").textContent = c.balance_texto;
  $("cierre-balance").hidden = c.estado === "abierto" && dia.alimentos.length === 0; // nothing yet: no balance
  $("cierre-balance").title = c.espera_motivo ?? "";
  $("cierre-semana").textContent = c.semana.dias ? c.semana.texto : "";
  $("cierre-semana").hidden = !c.semana.dias;
  $("cierre-rastro").replaceChildren(...c.rastro.map((r) => el("p", {
    textContent: `Garmin lo corrigió el ${r.fecha.slice(0, 10)}: gasto ${kcalEnteras(r.gasto_antes)} → `
      + `${kcalEnteras(r.gasto_despues)} kcal; balance ${signoKcal(r.balance_antes)} → ${signoKcal(r.balance_despues)}`,
  })));
  $("cierre-rastro").hidden = c.rastro.length === 0;
  if (esHoy) pintarAvisosCierre(dia.avisos_cierre ?? []);
}

function pintarAvisosCierre(avisos) {
  for (const viejo of $("avisos").querySelectorAll(".aviso-cierre")) viejo.remove();
  for (const a of avisos) {
    const boton = el("button", { type: "button", className: "boton", textContent: "Entendido" });
    boton.addEventListener("click", () => avisoVisto(a.id, boton));
    $("avisos").append(el("div", { className: "aviso-cierre", role: "status" }, el("p", { textContent: a.texto }), boton));
  }
}

async function avisoVisto(id, boton) {
  boton.disabled = true;
  const { ok, datos } = await api(`/api/aviso-cierre/${id}/visto`, {});
  if (!ok) {
    boton.disabled = false;
    mensaje(datos.error ?? "No se ha podido marcar el aviso.", "error");
    return;
  }
  if (ultimoDia) ultimoDia.avisos_cierre = datos.avisos_cierre;
  pintarAvisosCierre(datos.avisos_cierre);
}

async function cerrarDia() {
  const boton = $("cerrar-dia");
  boton.disabled = true;
  const { ok, datos } = await api("/api/dia/cerrar", { fecha: diaVisto });
  boton.disabled = false;
  if (!ok) { mensaje(datos.error ?? "No se ha podido cerrar el día.", "error"); return; }
  mensaje("Día cerrado: lo planeado cuenta como comido. Puedes seguir corrigiendo olvidos.", "bien");
  pintarDia(datos.dia);
}

// The «Te quedan» card: the level being looked at in big, the others as buttons (R1).
function pintarTarjeta(dia) {
  const n = nivelMirado(dia);
  const m = dia.margenes.find((x) => x.nivel === n);
  const pasado = m.kcal < 0;
  $("queda-texto").textContent = pasado ? "Te has pasado" : "Te quedan para comer";
  $("queda-cifra").textContent = kcalEnteras(Math.abs(m.kcal));
  $("queda").classList.toggle("pasado", pasado);
  $("nivel-activo").textContent = n === 0 ? "Mantenimiento" : `Nivel ${signo(n)} %`;
  $("queda-objetivo").hidden = !m.objetivo;
  $("otros").replaceChildren(...dia.margenes.filter((x) => x.nivel !== n).map((x) => {
    const boton = el("button", { type: "button", className: `otro${x.objetivo ? " objetivo" : ""}`,
      title: x.objetivo ? "Objetivo del día" : "" },
    el("span", { textContent: etiquetaNivel(x.nivel) }), el("strong", { className: "num", textContent: conSignoKcal(x.kcal) }));
    boton.addEventListener("click", () => mirar(x.nivel));
    return boton;
  }));
}

// Unit 013 (R-8): kcal without a quantity that are not an estimate count, marked «kcal a mano».
const kcalAMano = (a) => a.cantidad === null && a.kcal !== null && !Number.isNaN(a.kcal) && !a.estimado;

// Unit 013 (R-56): «kcal/100 g» works the kcal out with the grams (kcal/100 g × g ÷ 100); never saved.
// It opens from a button and its input only exists once opened, so a card that has not used it keeps a
// single input named «kcal». `alCalcular(kcal)` gets the result; `alEscribir(v)` what is typed.
function kcalPor100(inicial, alCalcular, alEscribir = () => {}) {
  const abrir = el("button", { type: "button", className: "por100", textContent: "kcal/100 g" });
  const nota = el("p", { className: "solo-gramos", hidden: true, textContent: "Las kcal/100 g van solo con gramos: con otra unidad no lo calculo." });
  const caja = el("div", { className: "kcal-100g" }, abrir, nota);
  let entrada = null;
  let cantidad = null;
  let unidad = null;
  const valor = () => {
    const v = entrada ? comoNumero(entrada.value) : null;
    return v === null || Number.isNaN(v) || v < 0 ? null : v;
  };
  const calcular = () => {
    const por100 = valor();
    const gramos = !unidad || unidad === "g";
    nota.hidden = por100 === null || gramos;
    if (por100 === null || !gramos || cantidad === null || Number.isNaN(cantidad) || cantidad <= 0) return;
    alCalcular(Math.round(por100 * cantidad / 100 * 10) / 10);
  };
  const crear = (valorInicial) => {
    entrada = el("input", { type: "number", step: "any", min: "0", inputMode: "decimal", value: valorInicial ?? "" });
    entrada.addEventListener("input", () => { alEscribir(valor()); calcular(); });
    abrir.replaceWith(el("label", { className: "campo number" }, el("span", { textContent: "kcal/100 g" }), entrada));
  };
  if (inicial !== null && inicial !== undefined) crear(String(inicial));
  abrir.addEventListener("click", () => { crear(""); entrada.focus(); });
  return {
    caja, valor,
    // the quantity or the unit changed: work the kcal out again (R-56: 6 g → 16,8; 10 g → 28)
    cambiar: (nuevaCantidad, nuevaUnidad) => { cantidad = nuevaCantidad; unidad = nuevaUnidad; calcular(); },
  };
}

// The day as a list by sections (unit 035); tapping a row opens its change card (R4), its handle ⠿ drags it.
function filaAlimento(a) {
  if (a.id === editando) return fichaConservada(`cambio:${a.id}`, () => fichaCambio(a));
  const planeado = a.estado === "planeado";
  const detalle = el("span", { className: a.cantidad === null ? "sin-cantidad" : "" }, a.cantidad === null ? "" : cantidad(a));
  if (a.estimado) detalle.append(el("i", { className: "marca", textContent: "estimado" }));
  if (a.provisional) detalle.append(el("i", { className: "marca", textContent: "provisional" }));
  if (a.origen) detalle.append(el("i", { className: "marca origen", textContent: leerOrigen(a.origen) }));
  if (a.pendiente_cantidad) detalle.append(el("i", { className: "marca alerta", textContent: "falta cantidad" }));
  if (kcalAMano(a)) detalle.append(el("i", { className: "marca", textContent: "kcal a mano" }));
  const cuenta = !a.pendiente_cantidad && a.kcal !== null;
  const fila = el("div", { className: `fila${planeado ? " planeada" : ""}`, tabIndex: 0, title: "Toca para cambiarlo" },
    el("div", { className: "que" }, el("strong", { textContent: a.nombre }), detalle),
    el("div", { className: "kcal num", textContent: cuenta ? kcalEnteras(a.kcal) : "—" }));
  fila.setAttribute("data-grupo", planeado ? "planeado" : "comido");
  fila.dataset.seccion = a.seccion ?? "";
  const asa = el("span", { className: "asa", title: "Arrastra para cambiarlo de sección" });
  asa.setAttribute("role", "button");
  asa.setAttribute("aria-label", `Mover ${a.nombre} a otra sección`);
  asa.addEventListener("click", (evento) => evento.stopPropagation()); // the handle never opens the card (R8)
  asa.addEventListener("pointerdown", (evento) => empezarArrastre(evento, a, fila, asa));
  fila.prepend(asa);
  if (planeado && a.dia <= hoyISO) { // a later day cannot be eaten yet (R3 of 004)
    const comido = el("button", { type: "button", className: "btn-comido", textContent: "Comido" });
    comido.addEventListener("click", (evento) => { evento.stopPropagation(); marcarComido(a, comido); });
    fila.append(comido);
  }
  const abrir = () => { editando = a.id; anadiendo = false; mensaje(""); pintarDia(ultimoDia); };
  fila.addEventListener("click", abrir);
  fila.addEventListener("keydown", (evento) => { if (evento.key === "Enter") abrir(); });
  return fila;
}

// Unit 016 (R4): a repaint does not rebuild the open card: the same node (what is typed in it) comes back,
// with the focus where it was. Only while its food is still in the day; otherwise it closes.
function fichaConservada(clave, crear) {
  if (!fichaAbierta || fichaAbierta.clave !== clave) fichaAbierta = { clave, nodo: crear() };
  return fichaAbierta.nodo;
}

function pintarLista(dia) {
  if (arrastre) { diaTrasArrastre = dia; return; } // the dragged row stays where the finger has it
  if (editando !== null && !dia.alimentos.some((a) => a.id === editando)) editando = null; // deleted elsewhere
  const foco = fichaAbierta && fichaAbierta.nodo.contains(document.activeElement) ? document.activeElement : null;
  const nodos = [];
  // unit 035 (R5, R6): the sections with some food plus the ones just created, in the order of the day; inside
  // each, the foods in the order they were logged
  const de = (clave) => dia.alimentos.filter((a) => (a.seccion ?? "") === clave).sort((x, y) => x.id - y.id);
  const conAlgo = new Set(dia.alimentos.map((a) => a.seccion ?? ""));
  for (const clave of conAlgo) seccionesNuevas.delete(clave); // once it has a food, emptying it removes it
  const visibles = [...SECCIONES.map(([clave]) => clave), ""].filter((c) => conAlgo.has(c) || seccionesNuevas.has(c));
  for (const clave of visibles) {
    const alimentos = de(clave);
    nodos.push(cabeceraSeccion(clave, alimentos), ...alimentos.map(filaAlimento));
  }
  nodos.push(pieSecciones(SECCIONES.filter(([clave]) => !visibles.includes(clave))));
  nodos.push(el("div", { id: "con-planeado", className: "total", hidden: true }));
  $("lista-dia").replaceChildren(...nodos);
  $("lista-dia").hidden = dia.alimentos.length === 0;
  $("vacio").hidden = dia.alimentos.length > 0;
  pintarConPlaneado(dia);
  $("anadir-ficha").replaceChildren(...(anadiendo ? [fichaConservada(`anadir:${dia.fecha}`, () => fichaAnadir(dia.fecha))] : []));
  const abierta = editando !== null ? `cambio:${editando}` : anadiendo ? `anadir:${dia.fecha}` : null;
  if (!fichaAbierta || fichaAbierta.clave !== abierta) fichaAbierta = null;
  if (foco && foco.isConnected && document.activeElement !== foco) foco.focus({ preventScroll: true });
  $("anadir").hidden = anadiendo;
  $("avisos").replaceChildren(...(dia.pendientes.length ? [el("p", {
    className: "aviso",
    textContent: `Falta la cantidad de: ${dia.pendientes.map((p) => p.nombre).join(", ")}. No suma hasta que la pongas.`,
  })] : []));
}

// -- unit 035: the sections of the day ------------------------------------------------------------

// The name of a section and its eaten kcal (the ones that count, as the total: never the planned or a pending one).
function cabeceraSeccion(clave, alimentos) {
  const suma = alimentos.filter((a) => a.estado !== "planeado" && !a.pendiente_cantidad && a.kcal !== null)
    .reduce((total, a) => total + a.kcal, 0);
  const cabecera = el("div", { className: "seccion-cab" },
    el("span", { className: "seccion-nombre", textContent: NOMBRE_SECCION[clave] }),
    el("span", { className: "seccion-kcal num", textContent: `${kcalEnteras(suma)} kcal` }));
  cabecera.dataset.seccion = clave;
  if (!alimentos.length) cabecera.append(el("span", { className: "seccion-vacia", textContent: "Arrastra aquí un alimento" }));
  return cabecera;
}

// «Añadir sección» (R2): offers the ones of the five the day does not have yet; with the five, says so.
function pieSecciones(faltan) {
  const pie = el("div", { className: "pie-secciones" });
  if (!eligiendoSeccion) {
    const boton = el("button", { type: "button", id: "anadir-seccion", className: "anadir-seccion", textContent: "Añadir sección" });
    boton.addEventListener("click", () => { eligiendoSeccion = faltan.length ? "elegir" : "todas"; pintarLista(ultimoDia); });
    pie.append(boton);
    return pie;
  }
  const cerrar = el("button", { type: "button", className: "cancelar-seccion", textContent: eligiendoSeccion === "todas" ? "Vale" : "Cancelar" });
  cerrar.addEventListener("click", () => { eligiendoSeccion = false; pintarLista(ultimoDia); });
  if (eligiendoSeccion === "todas" || !faltan.length) {
    pie.append(el("p", { className: "suave", textContent: "Ya están las seis secciones." }), cerrar);
    return pie;
  }
  const opciones = el("div", { className: "opciones-seccion", id: "opciones-seccion" });
  for (const [clave, nombre] of faltan) {
    const opcion = el("button", { type: "button", textContent: nombre });
    opcion.dataset.seccion = clave;
    opcion.addEventListener("click", () => { seccionesNuevas.add(clave); eligiendoSeccion = false; pintarLista(ultimoDia); });
    opciones.append(opcion);
  }
  pie.append(el("p", { className: "suave", textContent: "¿Qué sección añades?" }), opciones, cerrar);
  return pie;
}

// Dragging a row from its handle: pointer events (finger or mouse); the handle has `touch-action: none`, so
// the page does not scroll under the finger, and outside it the page scrolls as always.
function empezarArrastre(evento, a, fila, asa) {
  if (arrastre || (evento.pointerType === "mouse" && evento.button !== 0)) return;
  evento.preventDefault();
  evento.stopPropagation();
  const caja = fila.getBoundingClientRect();
  const fantasma = fila.cloneNode(true);
  fantasma.removeAttribute("data-seccion");
  fantasma.removeAttribute("data-grupo");
  fantasma.classList.add("fantasma");
  fantasma.setAttribute("aria-hidden", "true");
  Object.assign(fantasma.style, { left: `${caja.left}px`, top: `${caja.top}px`, width: `${caja.width}px` });
  $("lista-dia").append(fantasma);
  fila.classList.add("arrastrada");
  arrastre = { a, fila, asa, fantasma, destino: null, dy: evento.clientY - caja.top, puntero: evento.pointerId };
  try { asa.setPointerCapture(evento.pointerId); } catch { /* a synthetic pointer cannot be captured */ }
  document.addEventListener("pointermove", moverArrastre);
  document.addEventListener("pointerup", soltarArrastre);
  document.addEventListener("pointercancel", cancelarArrastre);
  marcarDestino(evento.clientX, evento.clientY);
}

function moverArrastre(evento) {
  if (!arrastre || evento.pointerId !== arrastre.puntero) return;
  evento.preventDefault();
  arrastre.fantasma.style.top = `${evento.clientY - arrastre.dy}px`;
  const borde = 70; // near the top or the bottom of the screen the page follows the finger
  if (evento.clientY < borde) window.scrollBy(0, -14);
  else if (evento.clientY > window.innerHeight - borde) window.scrollBy(0, 14);
  marcarDestino(evento.clientX, evento.clientY);
}

// The section under the finger, highlighted while dragging (its header and its rows).
function marcarDestino(x, y) {
  const debajo = document.elementFromPoint(x, y);
  const nodo = debajo ? debajo.closest("#lista-dia [data-seccion]") : null;
  const destino = nodo ? nodo.dataset.seccion : null;
  if (destino === arrastre.destino) return;
  arrastre.destino = destino;
  for (const viejo of $("lista-dia").querySelectorAll(".destino")) viejo.classList.remove("destino");
  if (destino === null) return;
  for (const nodoDestino of $("lista-dia").querySelectorAll("[data-seccion]")) {
    if (nodoDestino.dataset.seccion === destino) nodoDestino.classList.add("destino");
  }
}

function terminarArrastre() {
  document.removeEventListener("pointermove", moverArrastre);
  document.removeEventListener("pointerup", soltarArrastre);
  document.removeEventListener("pointercancel", cancelarArrastre);
  const hecho = arrastre;
  arrastre = null;
  hecho.fantasma.remove();
  const dia = diaTrasArrastre ?? ultimoDia;
  diaTrasArrastre = null;
  pintarLista(dia);
  return hecho;
}

function cancelarArrastre(evento) {
  if (arrastre && evento.pointerId === arrastre.puntero) terminarArrastre();
}

// Dropped on another section (R3): only its section is saved, and the day comes back with the same numbers (R4).
async function soltarArrastre(evento) {
  if (!arrastre || evento.pointerId !== arrastre.puntero) return;
  marcarDestino(evento.clientX, evento.clientY);
  const { a, destino } = terminarArrastre();
  if (destino === null || destino === (a.seccion ?? "")) return;
  const { ok, datos } = await api(`/api/alimento/${a.id}/seccion`, { seccion: destino === "" ? null : destino });
  if (!ok) {
    mensaje(`No se ha podido mover: ${a.nombre}. ${datos.error ?? ""}`.trim(), "error");
    return;
  }
  if (datos.dia.fecha === diaVisto) pintarDia(datos.dia);
}

// «Con lo planeado te quedarían …» for the level being looked at; only with something planned that counts.
function pintarConPlaneado(dia) {
  const linea = $("con-planeado");
  if (!linea) return;
  linea.hidden = !(dia.planeado_kcal > 0);
  if (linea.hidden) { linea.replaceChildren(); return; }
  const n = nivelMirado(dia);
  const m = dia.con_planeado.margenes.find((x) => x.nivel === n);
  linea.replaceChildren(
    el("span", { textContent: m.kcal < 0 ? "Con lo planeado te pasarías" : "Con lo planeado te quedarían" }), " ",
    el("strong", { className: "num", textContent: `${kcalEnteras(Math.abs(m.kcal))} kcal` }));
}

// The objective of the day in one line; "" when the day has none.
function textoObjetivo(o, gasto) {
  if (!o) return "";
  const partes = [`Objetivo del día: ${o.tipo ?? "sin tipo"}`];
  if (o.porcentaje !== null) partes[0] += ` ${signo(o.porcentaje)} %`;
  if (o.pasos !== null) partes.push(`${numero.format(o.pasos)} pasos previstos`);
  if (o.entreno) partes.push(`entreno previsto «${o.entreno}» (aún no suma kcal al gasto)`);
  return `${partes.join(" · ")}.`;
}

// -- unit 008: bar, breakdown, Garmin and today's controls -----------------------

function pintarBarra(dia) {
  const b = dia.barra;
  $("barra").classList.toggle("pasada", b.pasado_kcal > 0);
  const lim = b.limite;
  // the scale reaches the objective and, if further, the healthy-limit mark (R-23, C-39)
  const escala = Math.max(b.objetivo_kcal, lim ? lim.kcal : 0, 1);
  const comido = Math.min(b.comido_kcal / escala * 100, 100);
  $("barra-relleno").style.width = `${comido}%`;
  const planeado = dia.planeado_kcal > 0 ? Math.min(dia.planeado_kcal / escala * 100, 100 - comido) : 0;
  $("barra-planeado").hidden = !(planeado > 0);
  $("barra-planeado").style.left = `${comido}%`;
  $("barra-planeado").style.width = `${planeado}%`;
  $("barra-leyenda-planeado").hidden = !(dia.planeado_kcal > 0);
  $("barra-pct").textContent = `${b.pct} %`;
  $("barra-quedan").textContent = `comido · ${kcalEnteras(b.comido_kcal)} de ${kcalEnteras(b.objetivo_kcal)}`;
  $("barra").title = `${dia.objetivo.texto} ${mayuscula(b.texto)}.`;
  $("barra-limite").hidden = !lim;
  if (lim) {
    $("barra-limite").style.left = `calc(${lim.kcal / escala * 100}% - 2px)`;
    $("barra-limite").title = `límite saludable ${numero.format(lim.pct)} % (${kcal(lim.kcal)})`;
  }
  const aviso = lim && lim.aviso ? lim.aviso : "";
  $("barra-aviso").textContent = aviso;
  $("barra-aviso").hidden = !aviso;
  pintarBarraReal(dia, escala);
}

// Unit 017 (R-23, asked on 2026-10-22): in the same bar, in another colour, up to the objective with what is
// already done according to Garmin (its steps and the activities it has, no planned training); the rest of
// the track up to the big number is what depends on the plan. One layer on a day closed with Garmin or to come.
function pintarBarraReal(dia, escala) {
  const r = dia.barra.real;
  $("barra-real").hidden = !r;
  $("barra-leyenda-real").hidden = !r;
  const texto = $("barra-real-texto");
  if (!r) {
    texto.textContent = dia.gasto.origen === "garmin" ? "Con el gasto real de Garmin." : "";
    texto.hidden = !texto.textContent;
    return;
  }
  $("barra-real").style.width = `${Math.min(r.objetivo_kcal / escala * 100, 100)}%`;
  const h = r.hecho;
  // unit 033: it compares with the day's objective, not with the level being looked at
  const objetivo = `de tu objetivo (${dia.objetivo.porcentaje === 0 ? "mantenimiento" : etiquetaNivel(dia.objetivo.porcentaje)})`;
  const partes = [r.pasado_kcal > 0 ? `te has pasado ${kcalEnteras(r.pasado_kcal)} kcal ${objetivo}` : `te quedan ${kcalEnteras(r.quedan_kcal)} kcal ${objetivo}`];
  if (h.pasos_hechos !== null) partes.push(`${enteras.format(h.pasos_hechos)} de ${enteras.format(h.pasos_del_dia)} pasos`);
  if (h.previsto_kcal > 0 && previstosDe(dia).length > 1) { // unit 047: «Natación, Bici y Carrera»
    partes.push(`entrenos previstos (${h.previsto_nombre}) ${kcalEnteras(h.previsto_kcal)} kcal aún no están en Garmin`);
  } else if (h.previsto_kcal > 0) {
    const nombre = h.previsto_nombre ? `${h.previsto_nombre.charAt(0).toLowerCase()}${h.previsto_nombre.slice(1)} prevista` : "entreno previsto";
    partes.push(`${nombre} ${kcalEnteras(h.previsto_kcal)} kcal aún no está en Garmin`);
  }
  if (h.aviso) partes.push(h.aviso);
  texto.textContent = `Con lo hecho ahora: ${partes.join(" · ")}`;
  texto.hidden = false;
  $("barra-real").title = `con lo hecho ahora (gasto ${kcal(h.kcal)}): objetivo ${kcal(r.objetivo_kcal)}`;
}

// Unit 047: the planned trainings of the day (a breakdown from before has only `previsto`).
function previstosDe(dia) {
  const d = dia.gasto.desglose;
  if (!d) return [];
  return d.previstos ?? (d.previsto ? [d.previsto] : []);
}

function linea(etiqueta, valor) {
  return el("div", { className: "linea" }, el("span", { textContent: etiqueta }), el("span", { textContent: valor }));
}

function pintarDesglose(dia) {
  const g = dia.gasto;
  const d = g.desglose;
  if (!d) { $("desglose").replaceChildren(); return; }
  const deBasal = dia.peso && dia.peso.basal && dia.peso.basal.origen === "peso" ? "Basal (con tu peso)" : "Basal";
  const lineas = [linea(deBasal, kcalEnteras(d.basal_kcal))];
  const pasos = d.pasos_hechos === null
    ? `${numero.format(g.pasos)} pasos supuestos`
    : `${numero.format(d.pasos_hechos)} hechos + ${numero.format(d.pasos_supuestos)} supuestos`;
  lineas.push(linea(`Pasos: ${pasos} × ${porPaso.format(d.kcal_por_paso)} kcal`, kcalEnteras(d.pasos_kcal)));
  for (const h of d.entreno_hecho) lineas.push(linea(`Hecho: ${h.nombre} (${numero.format(h.minutos)} min)${h.sin_kcal ? " · sin kcal" : ""}`, kcalEnteras(h.kcal)));
  for (const p of previstosDe(dia)) lineas.push(linea(`Previsto: ${p.nombre ?? "entreno"} (${numero.format(p.minutos)} min)`, kcalEnteras(p.kcal)));
  if (g.origen === "garmin") { // unit 014: the day closed with Garmin counts the real one
    lineas.push(linea("Estimado", kcalEnteras(g.estimado_kcal)));
    const real = linea("Real de Garmin", kcalEnteras(g.real_kcal));
    real.classList.add("suma");
    lineas.push(real);
  } else {
    const total = linea("Total", kcalEnteras(g.kcal));
    total.classList.add("suma");
    lineas.push(total);
  }
  lineas.push(el("p", { className: "suave", textContent: mayuscula(g.garmin.texto) }));
  for (const a of g.avisos) lineas.push(el("p", { className: "aviso", textContent: a }));
  if (g.calibracion) {
    lineas.push(el("p", { className: g.calibracion.descartada_motivo ? "aviso" : "calibracion" },
      el("strong", { textContent: "Kcal por paso: " }), g.calibracion.texto));
  }
  $("desglose").replaceChildren(...lineas);
}

const avisar = (id, texto) => { $(id).textContent = texto; $(id).hidden = !texto; };

// A day that has not come yet (unit 034): its objective, levels and planned training can be set too.
const esFuturo = (fecha) => hoyISO !== null && fecha > hoyISO;

// The sheet «Ajustes de hoy»: today and a future day (unit 034) everything but Garmin only today; a past day only
// the level being looked at and the expenditure (R5).
function pintarHoja(dia, esHoy) {
  const ajustable = esHoy || esFuturo(dia.fecha);
  $("hoja-titulo").textContent = esHoy ? "Ajustes de hoy" : `Ajustes del ${deFecha(dia.fecha)}`;
  $("gasto-titulo").textContent = esHoy ? "Gasto de hoy" : "Gasto del día";
  $("gasto-total").textContent = `${kcalEnteras(dia.gasto.kcal)} kcal`;
  $("gasto").textContent = `${dia.gasto.origen === "garmin" ? "Gasto" : "Gasto estimado"} ${kcal(dia.gasto.kcal)}: ${dia.gasto.explicacion}.`;
  pintarDesglose(dia);
  pintarSegmentado(dia);
  for (const id of ["abrir-niveles", "objetivo-hoy", "previsto"]) $(id).hidden = !ajustable;
  $("bloque-garmin").hidden = !esHoy;
  if (!esHoy) $("garmin-estado").textContent = "";
  if (!ajustable) { $("niveles").hidden = true; return; }
  $("objetivo-titulo").textContent = esHoy ? "Objetivo de hoy" : "Objetivo del día";
  $("objetivo-pct-etiqueta").textContent = esHoy ? "% de hoy (con signo)" : "% del día (con signo)";
  $("objetivo-guardar").textContent = esHoy ? "Cambiar el de hoy" : "Cambiar el del día";
  const o = dia.objetivo;
  const cual = o.porcentaje === 0 ? "Mantenimiento" : `${signo(o.porcentaje)} %`;
  $("objetivo-valor").textContent = `${cual} · ${o.origen === "dia" ? (esHoy ? "hoy" : "del día") : o.origen === "plan" ? "plan" : "sin plan"}`;
  $("objetivo-kcal").textContent = `${kcalEnteras(o.kcal)} kcal de ${kcalEnteras(dia.gasto.kcal)} de gasto.`;
  // the server words it for today (margen.py); another day says «del día» (unit 034, R5)
  $("objetivo-actual").textContent = esHoy ? o.texto : o.texto.replace(/^Objetivo de hoy/, "Objetivo del día");
  const dicho = textoObjetivo(dia.objetivo_dia, dia.gasto);
  $("objetivo-dia").textContent = dicho;
  $("objetivo-dia").hidden = !dicho;
  $("plan").textContent = textoPlan(dia.plan);
  if (document.activeElement !== $("objetivo-pct")) $("objetivo-pct").value = numero.format(o.porcentaje).replace("−", "-");
  $("objetivo-plan").hidden = o.origen !== "dia";
  $("retraso").textContent = o.retraso ? `${mayuscula(o.retraso.texto)}.` : "";
  $("retraso").hidden = !o.retraso;
  if (document.activeElement !== $("niveles-texto")) $("niveles-texto").value = textoNiveles(dia.margenes.map((m) => m.nivel));
  $("niveles-defecto").hidden = !dia.niveles_elegidos;
  pintarPrevistos(dia, esHoy);
}

// Unit 047: one row per planned training, each with «Cambiar» (its minutes or its activity) and «Ya hecho» (today)
// or «Quitar» (a day to come), and «Añadir otro» with the box of before.
function pintarPrevistos(dia, esHoy) {
  const previstos = previstosDe(dia);
  const minutos = previstos.reduce((suma, p) => suma + Number(p.minutos), 0);
  $("previsto-valor").textContent = previstos.length === 0 ? "—"
    : `${previstos.length > 1 ? `${previstos.length} · ` : ""}${numero.format(minutos)} min`;
  $("previsto-actual").textContent = previstos.length === 0 ? "Sin entreno previsto."
    : previstos.length === 1 ? "Quítalo cuando ya esté hecho." : "Quita cada uno cuando ya esté hecho.";
  $("previsto-lista").replaceChildren(...previstos.map((p, i) => filaPrevisto(p, i, esHoy)));
  $("abrir-previsto").textContent = previstos.length ? "Añadir otro" : "Añadir";
}

function filaPrevisto(p, posicion, esHoy) {
  const editor = el("div", { className: "desplegable previsto-editor", hidden: true });
  const cambiar = el("button", { type: "button", className: "boton previsto-cambiar", textContent: "Cambiar" });
  const quitar = el("button", { type: "button", className: "boton previsto-quitar-fila", textContent: esHoy ? "Ya hecho" : "Quitar" });
  if (posicion === 0) quitar.id = "previsto-quitar"; // the «Ya hecho» of before (unit 008) is the first row's
  quitar.addEventListener("click", () => cambiarObjetivo({ previsto_quitar: posicion }, "previsto-error"));
  cambiar.addEventListener("click", () => {
    editor.hidden = !editor.hidden;
    if (!editor.hidden) abrirEditorPrevisto(editor, p, posicion);
  });
  return el("li", { className: "previsto-fila" },
    el("div", { className: "dato-fila" },
      el("span", { textContent: `${p.nombre ?? "entreno"} · ${numero.format(p.minutos)} min` }),
      el("strong", { className: "num", textContent: kcal(p.kcal) })),
    el("div", { className: "botones" }, cambiar, quitar),
    editor);
}

async function abrirEditorPrevisto(editor, p, posicion) {
  const minutos = el("input", { type: "number", inputMode: "numeric", min: "1", max: "600", step: "1", value: String(p.minutos) });
  const actividad = el("select", {}, el("option", { value: "", textContent: `Como ahora: ${p.nombre ?? "entreno"}` }));
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  guardar.addEventListener("click", () => {
    const n = comoNumero(minutos.value);
    if (n === null || Number.isNaN(n) || n <= 0) { avisar("previsto-error", "Pon los minutos que harás."); return; }
    const cambio = { posicion, minutos: n };
    if (actividad.value) cambio.actividad_id = actividad.value;
    cambiarObjetivo({ previsto_cambiar: cambio }, "previsto-error");
  });
  editor.replaceChildren(
    el("label", { className: "campo" }, el("span", { textContent: "Como este entreno (últimos 14 días)" }), actividad),
    el("label", { className: "campo" }, el("span", { textContent: "Minutos que harás" }), minutos),
    guardar);
  minutos.focus();
  const otras = (await actividadesRecientes()).filter((a) => a.actividad_id !== p.actividad_id);
  actividad.append(...otras.map(opcionReciente));
}

// «Nivel que miras» in the sheet: the levels as a selector, the same effect as the card buttons.
function pintarSegmentado(dia) {
  const n = nivelMirado(dia);
  $("segmentado").replaceChildren(...dia.margenes.map((m) => {
    const boton = el("button", { type: "button", className: m.objetivo ? "objetivo" : "",
      textContent: m.nivel === 0 ? "Mant." : `${signo(m.nivel)} %`, title: m.objetivo ? "Objetivo del día" : "" });
    boton.setAttribute("aria-pressed", String(m.nivel === n));
    boton.addEventListener("click", () => mirar(m.nivel));
    return boton;
  }));
}

async function cambiarObjetivo(cuerpo, idError) {
  avisar(idError, "");
  // unit 034: to the day being looked at (today or a future one), never always today
  const { ok, datos } = await api("/api/dia/objetivo", { ...cuerpo, fecha: diaVisto ?? hoyISO });
  if (!ok) { avisar(idError, datos.error ?? "No se ha podido cambiar."); return false; }
  pintarDia(datos.dia);
  return true;
}

let actualizandoGarmin = false;

// The button of the sheet and the one of the Garmin warning (unit 018) do the same, one at a time.
async function actualizarGarmin() {
  const boton = $("actualizar-garmin");
  if (actualizandoGarmin) return; // one at a time
  actualizandoGarmin = true;
  const delAviso = document.getElementById("aviso-sync-actualizar");
  boton.disabled = true;
  boton.textContent = "Actualizando…";
  if (delAviso) { delAviso.disabled = true; delAviso.textContent = "Actualizando…"; }
  $("garmin-estado").textContent = "";
  $("garmin-estado").className = "suave";
  const { ok, datos } = await api("/api/garmin/actualizar", {});
  actualizandoGarmin = false;
  boton.disabled = false;
  boton.textContent = "↻ Actualizar Garmin";
  const aviso = document.getElementById("aviso-sync-actualizar");
  if (aviso) { aviso.disabled = false; aviso.textContent = "Actualizar Garmin"; }
  if (!ok) { // the data that were there stay as they were
    const texto = datos.error ?? "No se ha podido conectar con Garmin.";
    $("garmin-estado").textContent = texto;
    $("garmin-estado").className = "suave error";
    const error = document.getElementById("aviso-sync-error");
    if (error) { error.textContent = texto; error.hidden = false; }
    return;
  }
  $("garmin-estado").textContent = "Garmin actualizado.";
  if (diaVisto === datos.dia.fecha) pintarDia(datos.dia);
  cargarHoy(); // unit 030: the cards and the hour of Hoy too
}

async function actividadesRecientes() {
  const { ok, datos } = await api("/api/actividades-recientes");
  return ok ? datos.actividades : [];
}

const opcionReciente = (a) => el("option", {
  value: a.actividad_id, textContent: `${mayuscula(deFecha(a.fecha))} · ${a.nombre} · ${numero.format(a.minutos)} min · ${kcal(a.kcal)}`,
});

async function cargarRecientes() {
  const lista = await actividadesRecientes();
  const opciones = lista.map(opcionReciente);
  $("previsto-actividad").replaceChildren(...(opciones.length ? opciones : [el("option", { value: "", textContent: "No hay entrenos en los últimos 14 días" })]));
  const primero = lista[0];
  if (primero && !$("previsto-minutos").value) $("previsto-minutos").value = Math.round(primero.minutos);
}

function enlazarControles() {
  $("actualizar-garmin").addEventListener("click", actualizarGarmin);
  conSigno($("objetivo-pct"));
  $("objetivo-guardar").addEventListener("click", () => {
    const n = comoNumero($("objetivo-pct").value.replace("−", "-"));
    if (n === null || Number.isNaN(n)) { avisar("objetivo-error", `Escribe el % ${diaVisto === hoyISO ? "de hoy" : "del día"} (por ejemplo −20, 0 o 10).`); return; }
    cambiarObjetivo({ porcentaje: n }, "objetivo-error");
  });
  $("objetivo-mantenimiento").addEventListener("click", () => cambiarObjetivo({ porcentaje: 0 }, "objetivo-error"));
  $("objetivo-plan").addEventListener("click", () => cambiarObjetivo({ porcentaje: null }, "objetivo-error"));
  $("niveles-guardar").addEventListener("click", () => {
    const niveles = comoNiveles($("niveles-texto").value);
    if (!niveles || niveles.some((n) => Number.isNaN(n))) { avisar("niveles-error", "Escribe de 1 a 5 % separados por ; (por ejemplo −20; −15)."); return; }
    cambiarObjetivo({ niveles }, "niveles-error");
  });
  $("niveles-defecto").addEventListener("click", () => cambiarObjetivo({ niveles: null }, "niveles-error"));
  const desplegar = (id, alAbrir) => () => { $(id).hidden = !$(id).hidden; if (!$(id).hidden && alAbrir) alAbrir(); };
  $("abrir-niveles").addEventListener("click", desplegar("niveles", () => $("niveles-texto").focus()));
  $("abrir-pct").addEventListener("click", desplegar("caja-pct", () => $("objetivo-pct").focus()));
  $("abrir-previsto").addEventListener("click", desplegar("caja-previsto", cargarRecientes));
  $("previsto-guardar").addEventListener("click", () => {
    const actividad = $("previsto-actividad").value;
    const minutos = comoNumero($("previsto-minutos").value);
    if (!actividad) { avisar("previsto-error", "Elige un entreno de los últimos 14 días."); return; }
    if (minutos === null || Number.isNaN(minutos) || minutos <= 0) { avisar("previsto-error", "Pon los minutos que harás."); return; }
    // unit 047: it adds one more; the box closes so the next «Añadir otro» starts again
    cambiarObjetivo({ previsto_anadir: { actividad_id: actividad, minutos } }, "previsto-error").then((ok) => {
      if (!ok) return;
      $("caja-previsto").hidden = true;
      $("previsto-minutos").value = "";
    });
  });
}

// Inputs of a saved-food card; `leer()` takes what is typed at that moment (R7).
// Unit 019: with `buscar` («Añadir a mano», and since unit 074 the card to change one) the name is the search box;
// once a saved thing is chosen, quantity, unit, kcal and kcal/100 g give way to only how much of it, and `leer()`
// says `guardado: {tipo, id}` (the kcal it sends are only shown: the server works them out again). Until one is
// chosen the card works as without it (bug 025 included). Unit 074 (R-213): with `conservarCantidad` (the change
// card) the chosen one starts with the quantity typed, when it is in its own unit.
function camposFicha(a, { buscar = false, conservarCantidad = false } = {}) {
  const nombre = campo("Alimento", a.nombre, "text");
  const cant = campo("Cantidad", a.cantidad, "number", null, { step: "any", min: "0", inputMode: "decimal" });
  const unidad = campo("Unidad", a.unidad, "text");
  const kc = campo("kcal", a.kcal, "number", null, { step: "any", min: "0", inputMode: "decimal" });
  const dia = campo("Día", a.dia, "date");
  const por100 = kcalPor100(null, (k) => { kc.entrada.value = String(k); });
  const alCambiarCantidad = () => por100.cambiar(comoNumero(cant.entrada.value), unidad.entrada.value.trim() || null);
  // bug 025: a new quantity scales the kcal (kcal ÷ old quantity × new one) while the unit is the same and the
  // kcal have not been typed by hand; with kcal/100 g open, that works them out instead
  const base = a.cantidad > 0 && a.kcal !== null ? { kcal: a.kcal / a.cantidad, unidad: a.unidad || "g" } : null;
  let kcalEscritas = false;
  kc.entrada.addEventListener("input", () => { kcalEscritas = true; });
  cant.entrada.addEventListener("input", () => {
    const c = comoNumero(cant.entrada.value);
    if (!base || kcalEscritas || por100.valor() !== null || (unidad.entrada.value.trim() || "g") !== base.unidad || !(c > 0)) return;
    kc.entrada.value = String(Math.round(base.kcal * c * 10) / 10);
  });
  cant.entrada.addEventListener("input", alCambiarCantidad);
  unidad.entrada.addEventListener("input", alCambiarCantidad);
  alCambiarCantidad();
  const libres = [el("div", { className: "fila" }, cant, unidad, kc), por100.caja];
  const cuanto = el("div", { className: "cuanto" }, ...libres);
  let elegido = null; // { tipo, cosa, caja }
  let lista = null;
  if (buscar) {
    const soltar = () => {
      elegido = null;
      nombre.entrada.readOnly = false;
      cuanto.replaceChildren(...libres);
      nombre.entrada.focus();
    };
    lista = buscador({
      entrada: nombre.entrada,
      alElegir: ({ tipo, cosa }) => {
        const c = comoNumero(cant.entrada.value);
        const tenia = conservarCantidad && c > 0 ? { cantidad: c, unidad: unidad.entrada.value.trim() || "g" } : {};
        elegido = { tipo, cosa, caja: cajaElegida({ tipo, cosa, ...tenia, alSoltar: soltar }) };
        nombre.entrada.readOnly = true;
        cuanto.replaceChildren(elegido.caja.caja);
        elegido.caja.foco();
      },
    });
  }
  const leer = () => {
    if (elegido) {
      const c = elegido.caja.leer();
      return { nombre: elegido.cosa.nombre, cantidad: c.cantidad, unidad: c.unidad, kcal: c.kcal, dia: dia.entrada.value,
        guardado: { tipo: elegido.tipo, id: elegido.cosa.id } };
    }
    const datos = {
      nombre: nombre.entrada.value.trim(), cantidad: comoNumero(cant.entrada.value),
      unidad: unidad.entrada.value.trim() || null, kcal: comoNumero(kc.entrada.value), dia: dia.entrada.value,
    };
    if (datos.cantidad !== null && !datos.unidad) datos.unidad = "g";
    return datos;
  };
  const nodos = [nombre, lista, cuanto, dia];
  return { nodos, leer, foco: () => nombre.entrada.focus() };
}

// What is missing before a food can be saved, said in words; "" if nothing.
function falta(datos) {
  if (!datos.nombre) return "Ponle nombre al alimento.";
  if (!datos.dia) return `Pon el día de «${datos.nombre}».`;
  if (datos.guardado) { // unit 019: chosen from the saved ones, only how much is missing; the server does the rest
    return datos.cantidad === null || Number.isNaN(datos.cantidad) || !(datos.cantidad > 0) ? `Pon cuánto has cogido de «${datos.nombre}».` : "";
  }
  if (datos.cantidad !== null && (Number.isNaN(datos.cantidad) || datos.cantidad <= 0)) return `La cantidad de «${datos.nombre}» no vale.`;
  if (datos.kcal !== null && Number.isNaN(datos.kcal)) return `Las kcal de «${datos.nombre}» no valen.`;
  if (datos.cantidad !== null && datos.kcal === null) return `Pon las kcal de «${datos.nombre}».`;
  return "";
}

// A card that saves through `enviar(datos)`; on failure it stays open with what was typed (R8).
function ficha({ campos, textoGuardar, enviar, alCancelar }) {
  const aviso = el("p", { className: "aviso", hidden: true });
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const guardar = el("button", { type: "button", className: "principal", textContent: textoGuardar });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const acciones = el("div", { className: "acciones" }, guardar, cancelar);
  cancelar.addEventListener("click", alCancelar);
  guardar.addEventListener("click", async () => {
    const datos = campos.leer();
    const motivo = falta(datos);
    if (motivo) { avisar(motivo); return; }
    guardar.disabled = true;
    avisar("");
    const error = await enviar(datos);
    guardar.disabled = false;
    if (error) avisar(error);
  });
  const tarjeta = el("article", { className: "tarjeta" }, ...campos.nodos.filter(Boolean), aviso, acciones);
  return { tarjeta, acciones, avisar };
}

// Change card of a saved food: name, quantity, unit, kcal and day; delete with confirmation. Unit 074 (R-213): the
// name searches the saved recipes and ingredients; one chosen changes this same row (same id, day and section).
function fichaCambio(a) {
  const campos = camposFicha(a, { buscar: true, conservarCantidad: true });
  const cerrar = () => { editando = null; mensaje(""); pintarDia(ultimoDia); };
  const { tarjeta, acciones, avisar } = ficha({
    campos, textoGuardar: "Guardar cambios", alCancelar: cerrar,
    enviar: async (datos) => {
      const { ok, datos: r } = await api(`/api/alimento/${a.id}`, datos);
      if (!ok) return `No se ha podido guardar. ${r.error ?? ""}`.trim();
      editando = null;
      const otroDia = r.alimento.dia !== a.dia
        ? ` Pasado a ${nombreDelDia(r.alimento.dia).toLowerCase()}${r.alimento.estado === "planeado" ? " (planeado)" : ""}.` : "";
      mensaje(`Cambiado: ${r.alimento.nombre}.${otroDia}`, "bien");
      pintarDia(r.dia);
      return "";
    },
  });
  const borrar = el("button", { type: "button", className: "peligro", textContent: "Borrar" });
  const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
    el("p", { textContent: `¿Borrar ${a.nombre}?` }));
  const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, borrar" });
  const no = el("button", { type: "button", textContent: "No" });
  pregunta.append(el("div", { className: "acciones" }, si, no));
  const preguntar = (abierta) => {
    pregunta.hidden = !abierta;
    acciones.hidden = abierta;
    borrar.hidden = abierta;
  };
  borrar.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
  no.addEventListener("click", () => preguntar(false));
  si.addEventListener("click", async () => {
    si.disabled = true;
    const { ok, datos } = await api(`/api/alimento/${a.id}/borrar`, { confirmado: true });
    si.disabled = false;
    if (!ok) { preguntar(false); avisar(`No se ha podido borrar. ${datos.error ?? ""}`.trim()); return; }
    editando = null;
    mensaje(`Borrado: ${datos.alimento.nombre}.`, "bien");
    pintarDia(datos.dia);
  });
  tarjeta.append(borrar, pregunta);
  return tarjeta;
}

// Empty card under the table to add a food by hand, without the AI, on the day being looked at.
function fichaAnadir(fecha) {
  const campos = camposFicha({ nombre: "", cantidad: null, unidad: null, kcal: null, dia: fecha }, { buscar: true });
  const { tarjeta } = ficha({
    campos, textoGuardar: "Guardar",
    alCancelar: () => { anadiendo = false; mensaje(""); pintarDia(ultimoDia); },
    enviar: async (datos) => {
      const { ok, datos: r } = await api("/api/alimento", datos);
      if (!ok) return `No se ha podido guardar. ${r.error ?? ""}`.trim();
      anadiendo = false;
      const estado = r.alimento.estado === "planeado" ? " (planeado)" : "";
      const pendiente = r.alimento.pendiente_cantidad ? " Falta la cantidad: no suma." : "";
      mensaje(`Añadido: ${r.alimento.nombre}${estado}.${pendiente}`, "bien");
      pintarDia(r.dia);
      return "";
    },
  });
  tarjeta.classList.add("nueva");
  setTimeout(campos.foco, 0);
  return tarjeta;
}

async function marcarComido(a, boton) {
  boton.disabled = true;
  const { ok, datos } = await api(`/api/alimento/${a.id}/comido`, {});
  if (!ok) { // the food and the day stay as they were (R8)
    boton.disabled = false;
    mensaje(`No se ha podido marcar comido: ${a.nombre}. ${datos.error ?? ""}`.trim(), "error");
    return;
  }
  mensaje(`Comido: ${datos.alimento.nombre}.`, "bien");
  pintarDia(datos.dia);
}

async function cargarDia(fecha, { callado = false } = {}) {
  const turno = ++peticionDia;
  const respuesta = await api(fecha ? `/api/dia?fecha=${encodeURIComponent(fecha)}` : "/api/dia");
  const { ok, datos } = respuesta;
  if (turno !== peticionDia) return;
  const { estado } = respuesta;
  if (ok) pintarDia(datos);
  else if (callado) return; // a look of its own (R3 of unit 016): a failure says nothing, the day stays
  else if (conectado === false) mensaje(""); // the «Sin conexión con el PC» notice says it already
  else if (estado === 409) mensajeAjustes(datos.error ?? "Faltan datos en Ajustes.");
  else mensaje(datos.error ?? "No he podido cargar el día.", "error");
}

function irA(fecha) {
  mensaje("");
  cargarDia(fecha);
}

// Unit 016 (R3): back to the front, and every minute while in sight, the day is asked again; its `hoy`
// says whether midnight has passed (pintarDia moves from the old today to the new one).
function mirarElDia() {
  if (document.visibilityState === "visible" && pestanaActual() === "hoy") { cargarHoy(); return; } // unit 030
  if (document.visibilityState !== "visible" || pestanaActual() !== "dia" || !diaVisto) return;
  cargarDia(diaVisto, { callado: true });
}

// -- the inputs of a card --------------------------------------------------------

function campo(etiqueta, valor, tipo, alCambiar = null, extra = {}) {
  const entrada = el("input", { type: tipo, value: valor ?? "", ...extra });
  if (alCambiar) entrada.addEventListener("change", () => alCambiar(entrada.value));
  const etiquetado = el("label", { className: `campo ${tipo}` }, el("span", { textContent: etiqueta }), entrada);
  etiquetado.entrada = entrada;
  return etiquetado;
}

// iPhone's decimal keypad has no minus key: a ± button next to every signed % field flips the sign.
function conSigno(entrada) {
  const boton = el("button", { type: "button", className: "signo", textContent: "±", title: "Cambiar el signo (déficit / superávit)" });
  boton.addEventListener("click", () => {
    const v = entrada.value.trim();
    entrada.value = /^[-−]/.test(v) ? v.replace(/^[-−]\s*/, "") : `-${v}`;
    entrada.dispatchEvent(new Event("change"));
    entrada.focus();
  });
  const caja = el("div", { className: "con-signo" });
  entrada.replaceWith(caja);
  caja.append(entrada, boton);
  return entrada;
}

const comoNumero = (v) => (v.trim() === "" ? null : Number(v.replace(",", ".")));
const comoNiveles = (v) => (v.trim() === "" ? null : v.split(/[;\s]+|,(?=\s)|,$/).filter(Boolean).map((n) => comoNumero(n.replace("−", "-"))));
const textoNiveles = (n) => (n ? n.map((x) => numero.format(x)).join("; ") : "");

$("cerrar-dia").addEventListener("click", cerrarDia);
$("anadir").addEventListener("click", () => { anadiendo = true; editando = null; mensaje(""); pintarDia(ultimoDia); });
$("anterior").addEventListener("click", () => irA(sumarDias(diaVisto, -1)));
$("siguiente").addEventListener("click", () => irA(sumarDias(diaVisto, 1)));
$("ir-hoy").addEventListener("click", () => irA(hoyISO));
enlazarControles();
enlazarHoja();
iniciarHoy({ api, alActualizarDia: (dia) => { if (diaVisto === dia.fecha) pintarDia(dia); } });
iniciarFitness({ api }); // unit 031: the Fitness tab
iniciarPlan({ api }); // unit 040: Fitness › Plan
enlazarPestanas();
vigilarAltoEntrada();
$("fecha").textContent = deFecha(fechaLocal()); // before the PC answers, or if it never does
cargarDia();
document.addEventListener("visibilitychange", mirarElDia);
// unit 080 (R-223): a weight changed in Balance (peso.js) repaints it, with the mean and the projection recomputed
document.addEventListener("peso-cambiado", () => { if (pestanaActual() === "balance") pintarBalance($("balance-contenido"), irADia); });
setInterval(mirarElDia, MINUTO_MS);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

// -- unit 010: the sheet and the tabs ----------------------------------------------------

function enlazarHoja() {
  const hoja = $("hoja");
  $("abrir-hoja").addEventListener("click", () => { if (!hoja.open) hoja.showModal(); });
  $("cerrar-hoja").addEventListener("click", () => hoja.close());
  // a tap outside the sheet lands on the <dialog> itself (its backdrop): it closes
  hoja.addEventListener("click", (evento) => { if (evento.target === hoja) hoja.close(); });
}

function pestanaActual() {
  if (location.hash === "#peso") history.replaceState(null, "", "#balance"); // unit 027: an old bookmark
  const h = location.hash.slice(1).split("/")[0]; // «#fitness/plan» is a part of Fitness
  if (!h) return "hoy"; // unit 030 (R1): the app opens in Hoy
  return SITIOS.includes(h) ? h : "dia"; // unit 029 (R6): an address that does not exist opens Nutrición › Día
}

// Unit 029 (R6) and unit 030 (decision of 2026-10-24): an address saved from before opens its place of now. No
// address, `#hoy` and the bar of Ajustes («/?ir=hoy#hoy») open Hoy, and the address says so (`/#hoy`), so
// reloading while in Hoy stays in Hoy.
function direccionDeAntes() {
  const deLaBarra = new URLSearchParams(location.search).get("ir") === "hoy";
  if (deLaBarra || !location.hash) history.replaceState(null, "", "/#hoy");
}

// Unit 017: from Balance, a day opens in Nutrición › Día (its list of foods, its margin).
function irADia(fecha) {
  diaPedido = fecha;
  if (pestanaActual() === "dia") mostrarPestana();
  else location.hash = "dia";
}

function mostrarPestana() {
  const actual = pestanaActual();
  const enNutricion = NUTRICION.includes(actual);
  for (const p of SITIOS) $(p).hidden = p !== actual;
  $("nutricion").hidden = !enNutricion;
  const pestana = enNutricion ? "nutricion" : actual;
  for (const a of document.querySelectorAll(".pestana")) {
    if (a.dataset.ir === pestana) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  for (const a of document.querySelectorAll("#partes a")) {
    if (a.dataset.parte === actual) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (actual === "hoy" && pestanaVista !== "hoy") cargarHoy();
  if (actual === "fitness") mostrarFitness(); // unit 031 and unit 040: the part of Fitness, painted on entering it
  if ($("hoja").open) $("hoja").close();
  if (actual === "dia") document.title = `TriData · ${diaVisto ? nombreDelDia(diaVisto) : "Hoy"}`;
  else document.title = `TriData · ${mayuscula(actual)}`;
  // unit 013: back in Día from another place, the day is asked again (what was logged from Recetas shows)
  if (actual === "dia" && diaPedido) { // unit 017: a day tapped in Balance
    const fecha = diaPedido;
    diaPedido = null;
    irA(fecha);
  } else if (actual === "dia" && pestanaVista !== null && pestanaVista !== "dia" && diaVisto) cargarDia(diaVisto);
  if (pestanaVista === "recetas" && actual !== "recetas") cargarGuardado({ otraVez: true }); // unit 019: the search box
  pestanaVista = actual;
  if (actual === "balance") pintarBalance($("balance-contenido"), irADia);
  if (actual === "recetas") pintarRecetas($("recetas"));
  window.scrollTo(0, 0);
}

// Unit 040: Forma or Plan, by the address (`#fitness/plan` is Plan); a part is painted when it starts to show.
function mostrarFitness() {
  const parte = location.hash.startsWith("#fitness/plan") ? "plan" : "forma";
  $("fitness-forma").hidden = parte !== "forma";
  $("fitness-plan").hidden = parte !== "plan";
  for (const a of document.querySelectorAll("#fitness-partes a")) {
    if (a.dataset.parte === parte) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (pestanaVista !== "fitness" || parteFitness !== parte) (parte === "plan" ? cargarPlan : cargarFitness)();
  parteFitness = parte;
}

function enlazarPestanas() {
  // tapping Día (or Nutrición) while already in Día goes back to today (what tapping Hoy in Hoy did, unit 029)
  const volverAHoy = () => {
    if (pestanaActual() === "dia" && hoyISO && diaVisto !== hoyISO) irA(hoyISO);
  };
  document.querySelector('#partes a[data-parte="dia"]').addEventListener("click", volverAHoy);
  document.querySelector('.pestana[data-ir="nutricion"]').addEventListener("click", volverAHoy);
  window.addEventListener("hashchange", mostrarPestana);
  direccionDeAntes();
  mostrarPestana();
}

// The fixed tabs at the bottom never cover the end of the page: <main> leaves their height free.
function vigilarAltoEntrada() {
  const poner = () => document.documentElement.style.setProperty("--alto-abajo", `${$("abajo").offsetHeight}px`);
  poner();
  if (window.ResizeObserver) new ResizeObserver(poner).observe($("abajo"));
}
