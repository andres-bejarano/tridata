// Day page of the food log. Plain ES module, no build step.
// Nothing is saved until "Confirmar y guardar"; the server computes every total.
// A saved food is changed, moved, deleted or marked eaten by tapping its row in the day table.
// Every card reads its inputs when "Guardar" / "Confirmar" is pressed, not on `change`.
// One message can bring several pieces (unit 007): weight, objective of the day, foods and
// doubtful phrases. They are reviewed together and saved together, or nothing is saved.
// Unit 008: the bar at the top, the full expenditure with Garmin and its breakdown, the
// «Actualizar Garmin» button, today's objective, levels and planned training, and, in the
// review, «si te lo comes» and «¿cuánto me cabe?» (a simulation: nothing is saved).
// Unit 006: «Grabar» dictates instead of writing. The recording is kept on the phone (cola.js)
// until the PC sends back its text and list, which wait as «pendientes de revisar» until they
// are confirmed or discarded. The service worker (sw.js) lets the page open with the PC off.
// Unit 010 (presentation only, no rule changes): Hoy, Peso and Recetas are sections of this page
// chosen by #hoy / #peso / #recetas; the «Te quedan» card shows in big the level being looked at
// (remembered on this phone) with the others as buttons; everything that adjusts the day lives in
// the sheet «⚙ Ajustar el día» (<dialog id="hoja">); the day is a list Comido / Planeado. The Peso
// tab is drawn by peso.js, always for today. Unit 027: the Peso tab is inside Balance (balance.js calls peso.js);
// an old #peso opens #balance.
// Unit 011: the Recetas tab is drawn by recetas.js. A food that comes from a saved recipe or fixed
// food says so («de tu receta …», «provisional») in the review and in the day list; one that is only
// alike comes as a visible proposal with «Usar estimación», which puts Claude's estimate back.
// Unit 014: under the day list, the state of the day (open, food closed, waiting for Garmin, closed with
// Garmin) with its balance and the week, the trail of Garmin's corrections, and «Cerrar el día» while it is
// open (never on a later day). Today shows the relevant corrections in #avisos until «Entendido». The
// breakdown says when the kcal per step were calibrated with Garmin (or why that was discarded).
// Unit 016: every kcal on screen in whole numbers (the server keeps them exact; the kcal inputs too); the
// page asks the day again when it comes back to the front and every minute in sight, and past midnight it
// moves from the old today to the new one; an open card survives a repaint; confirming sends the day of the
// list and reinterpreting a list of the queue sends the day it was recorded.
// Unit 019: in «Añadir a mano» and in each review card the name is the search box of buscador.js; choosing a
// saved recipe or ingredient leaves only how much (grams, units, or the whole recipe / ½ / ⅓ / ¼) with its kcal at
// once, and the food goes with `guardado: {tipo, id}` so the server logs it with ITS kcal. «Cambiar» goes back to
// the free fields. A fraction reads «la receta entera», «½ receta»…; an origin «de tu alimento fijo …» logged
// before the rename reads «de tu ingrediente …» (the base is not rewritten).

import * as cola from "./cola.js";
import { MAX_SEGUNDOS, grabar, puedeGrabar } from "./dictado.js";
import { pintarBalance } from "./balance.js";
import { buscador, cajaElegida, cargarGuardado, cosaGuardada, cuenta, leerOrigen, origenDe, textoReceta, unidadPropia } from "./buscador.js";
import { pintarRecetas } from "./recetas.js";

const $ = (id) => document.getElementById(id);
// kcal with a thousands point always («1.040»): es-ES does not group 4 digits by default
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, useGrouping: "always" });
// unit 016 (R8): kcal are shown whole («1.790», «17»); only shown: what is saved and the inputs stay exact
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const kcalEnteras = (n) => enteras.format(Math.round(n) === 0 ? 0 : n); // never «−0»
const porPaso = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 3 }); // 0,04 kcal/paso must not read 0
const kilos = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 }); // 0,25 kg/semana must not read 0,3
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const signo = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + numero.format(Math.abs(n));
const etiquetaNivel = (n) => (n === 0 ? "Mantenimiento" : `${signo(n)} %`);
const conSignoKcal = (n) => (Math.round(n) < 0 ? `−${kcalEnteras(-n)}` : kcalEnteras(Math.max(n, 0)));
const signoKcal = (n) => (Math.round(n) > 0 ? "+" : Math.round(n) < 0 ? "−" : "") + kcalEnteras(Math.abs(n));
const MINUTO_MS = 60_000; // unit 016 (R3): how often the day is asked again while it is in sight
const PESTANAS = ["hoy", "balance", "recetas"]; // unit 027: Peso is inside Balance
const CLAVE_NIVEL = "tridata.nivel-que-miras"; // a screen preference of this phone, never the objective
const PESO_MIN = 30;
const PESO_MAX = 250;

let revision = null; // { texto, hoy, original, alimentos, peso, objetivo, dudosas, origen?, dicho? }
let hoyISO = null; // today, as the server says in every day it sends (unit 016: it moves at midnight)
let diaVisto = null; // the day on screen (ISO)
let ultimoDia = null;
let editando = null; // id of the saved food whose row is open for change
let anadiendo = false; // the "add by hand" card under the table is open
let fichaAbierta = null; // unit 016 (R4): { clave, nodo } of the open card, kept between repaints
let lectores = []; // one per review card: copies what is typed into the model
let peticionDia = 0; // drops a late answer when ◀ ▶ are tapped fast
let conectado = null; // whether the PC answered the last request (null: not known yet)
let grabacion = null; // { parar } while recording
let esperada = null; // id of the recording just made: its list opens by itself when it comes back
let textoDeLista = null; // a list whose interpretation failed (502), its text put in the box, and its day
const porCien = new WeakMap(); // unit 013: the kcal/100 g typed on a review card, by food (never saved)
let pestanaVista = null; // the tab on screen, to reload the day when coming back to Hoy (unit 013)
let diaPedido = null; // unit 017: a day tapped in Balance, opened when Hoy shows up

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
// unit 019: a food chosen from the saved ones (an object; Claude's `guardado` is a name, not a choice)
const esElegido = (a) => Boolean(a.guardado) && typeof a.guardado === "object";
const deFecha = (iso) => fechaLarga.format(new Date(`${iso}T12:00:00`)).replace(",", "");
const sumarDias = (iso, n) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const mayuscula = (t) => t.charAt(0).toUpperCase() + t.slice(1);

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
  if (volvio) { cargarDia(diaVisto); cola.enviarPendientes(); }
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
  if (dia.fecha !== diaVisto) { editando = null; anadiendo = false; }
  diaVisto = dia.fecha;
  ultimoDia = dia;
  // unit 018 (R12): until a day is painted ◀ ▶ have no day to move from; then the page says which one it shows
  for (const id of ["anterior", "siguiente", "ir-hoy"]) $(id).disabled = false;
  document.body.dataset.dia = dia.fecha;
  const esHoy = dia.fecha === hoyISO;
  $("titulo").textContent = nombreDelDia(dia.fecha);
  if (pestanaActual() === "hoy") document.title = `TriData · ${nombreDelDia(dia.fecha)}`;
  $("fecha").textContent = deFecha(dia.fecha);
  $("ir-hoy").hidden = esHoy;
  $("t-comido-dia").textContent = esHoy ? "Tu día" : mayuscula(deFecha(dia.fecha));
  $("vacio").textContent = esHoy ? "Aún no has apuntado nada hoy." : `No hay nada apuntado el ${deFecha(dia.fecha)}.`;
  $("total").textContent = `${kcalEnteras(dia.total_kcal)} kcal comidas`;
  const previsto = dia.gasto.desglose && dia.gasto.desglose.previsto;
  $("ajustar-resumen").textContent = `gasto ${kcalEnteras(dia.gasto.kcal)}${previsto ? " · entreno" : ""} ›`;
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

// The day as a list in two groups, Comido and Planeado; tapping a row opens its change card (R4).
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
  if (editando !== null && !dia.alimentos.some((a) => a.id === editando)) editando = null; // deleted elsewhere
  const foco = fichaAbierta && fichaAbierta.nodo.contains(document.activeElement) ? document.activeElement : null;
  const comidos = dia.alimentos.filter((a) => a.estado !== "planeado");
  const planeados = dia.alimentos.filter((a) => a.estado === "planeado");
  const nodos = [];
  if (comidos.length) nodos.push(el("div", { className: "subtitulo", textContent: "Comido" }), ...comidos.map(filaAlimento));
  if (planeados.length) nodos.push(el("div", { className: "subtitulo", textContent: "Planeado" }), ...planeados.map(filaAlimento));
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
  const partes = [r.pasado_kcal > 0 ? `te has pasado ${kcalEnteras(r.pasado_kcal)} kcal` : `te quedan ${kcalEnteras(r.quedan_kcal)} kcal`];
  if (h.pasos_hechos !== null) partes.push(`${enteras.format(h.pasos_hechos)} de ${enteras.format(h.pasos_del_dia)} pasos`);
  if (h.previsto_kcal > 0) {
    const nombre = h.previsto_nombre ? `${h.previsto_nombre.charAt(0).toLowerCase()}${h.previsto_nombre.slice(1)} prevista` : "entreno previsto";
    partes.push(`${nombre} ${kcalEnteras(h.previsto_kcal)} kcal aún no está en Garmin`);
  }
  if (h.aviso) partes.push(h.aviso);
  texto.textContent = `Con lo hecho ahora: ${partes.join(" · ")}`;
  texto.hidden = false;
  $("barra-real").title = `con lo hecho ahora (gasto ${kcal(h.kcal)}): objetivo ${kcal(r.objetivo_kcal)}`;
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
  if (d.previsto) lineas.push(linea(`Previsto: ${d.previsto.nombre ?? "entreno"} (${numero.format(d.previsto.minutos)} min)`, kcalEnteras(d.previsto_kcal)));
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

// The sheet «Ajustes de hoy»: on another day only the level being looked at and the expenditure (R5).
function pintarHoja(dia, esHoy) {
  $("hoja-titulo").textContent = esHoy ? "Ajustes de hoy" : `Ajustes del ${deFecha(dia.fecha)}`;
  $("gasto-titulo").textContent = esHoy ? "Gasto de hoy" : "Gasto del día";
  $("gasto-total").textContent = `${kcalEnteras(dia.gasto.kcal)} kcal`;
  $("gasto").textContent = `${dia.gasto.origen === "garmin" ? "Gasto" : "Gasto estimado"} ${kcal(dia.gasto.kcal)}: ${dia.gasto.explicacion}.`;
  pintarDesglose(dia);
  pintarSegmentado(dia);
  for (const id of ["abrir-niveles", "objetivo-hoy", "previsto", "bloque-garmin"]) $(id).hidden = !esHoy;
  if (!esHoy) { $("niveles").hidden = true; $("garmin-estado").textContent = ""; return; }
  const o = dia.objetivo;
  const cual = o.porcentaje === 0 ? "Mantenimiento" : `${signo(o.porcentaje)} %`;
  $("objetivo-valor").textContent = `${cual} · ${o.origen === "dia" ? "hoy" : o.origen === "plan" ? "plan" : "sin plan"}`;
  $("objetivo-kcal").textContent = `${kcalEnteras(o.kcal)} kcal de ${kcalEnteras(dia.gasto.kcal)} de gasto.`;
  $("objetivo-actual").textContent = o.texto;
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
  const p = dia.gasto.desglose && dia.gasto.desglose.previsto;
  $("previsto-valor").textContent = p ? `${numero.format(p.minutos)} min` : "—";
  $("previsto-actual").textContent = p
    ? `Previsto: ${p.nombre ?? "entreno"}, ${numero.format(p.minutos)} min (${kcal(p.kcal)}). Quítalo cuando ya esté hecho.`
    : "Sin entreno previsto.";
  $("previsto-quitar").hidden = !p;
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
  const { ok, datos } = await api("/api/dia/objetivo", cuerpo);
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
}

async function cargarRecientes() {
  const { ok, datos } = await api("/api/actividades-recientes");
  const lista = ok ? datos.actividades : [];
  const opciones = lista.map((a) => el("option", {
    value: a.actividad_id, textContent: `${mayuscula(deFecha(a.fecha))} · ${a.nombre} · ${numero.format(a.minutos)} min · ${kcal(a.kcal)}`,
  }));
  $("previsto-actividad").replaceChildren(...(opciones.length ? opciones : [el("option", { value: "", textContent: "No hay entrenos en los últimos 14 días" })]));
  const primero = lista[0];
  if (primero && !$("previsto-minutos").value) $("previsto-minutos").value = Math.round(primero.minutos);
}

function enlazarControles() {
  $("actualizar-garmin").addEventListener("click", actualizarGarmin);
  conSigno($("objetivo-pct"));
  $("objetivo-guardar").addEventListener("click", () => {
    const n = comoNumero($("objetivo-pct").value.replace("−", "-"));
    if (n === null || Number.isNaN(n)) { avisar("objetivo-error", "Escribe el % de hoy (por ejemplo −20, 0 o 10)."); return; }
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
    cambiarObjetivo({ previsto: { actividad_id: actividad, minutos } }, "previsto-error");
  });
  $("previsto-quitar").addEventListener("click", () => cambiarObjetivo({ previsto: null }, "previsto-error"));
}

// -- unit 008: «si te lo comes» and «¿cuánto me cabe?» over the reviewed list (saves nothing) --

let turnoSimulacion = 0;
let esperaSimulacion = null;
let caben = [];

const paraSimular = () => (revision ? revision.alimentos.filter((a) => a.nombre && a.dia && !Number.isNaN(a.kcal)
  && !Number.isNaN(a.cantidad) && (a.cantidad === null || a.cantidad > 0) && (a.kcal === null || a.kcal >= 0)) : []);

function programarSimulacion() {
  clearTimeout(esperaSimulacion);
  esperaSimulacion = setTimeout(simularRevision, 250);
}

async function simularRevision() {
  if (!revision) { $("simulacion").hidden = true; return; }
  const turno = ++turnoSimulacion;
  const alimentos = paraSimular().map(({ anadida, ...a }) => a);
  const { ok, datos } = await api("/api/simular", { alimentos });
  if (turno !== turnoSimulacion || !revision) return;
  if (!ok) { $("simulacion").hidden = true; return; }
  caben = alimentos.map((a, i) => ({ nombre: a.nombre, ...datos.caben[i] }));
  const b = datos.barra;
  const margenes = datos.margenes.map((m) => `${etiquetaNivel(m.nivel)}: ${kcal(m.kcal)}`).join(" · ");
  $("simulacion").replaceChildren(
    el("p", { className: "titulo-pieza", textContent: `Si te lo comes (${kcal(datos.kcal_lista)} de hoy)` }),
    el("p", { textContent: `La barra quedaría en ${b.pct} % · ${b.texto}.` }),
    el("p", { className: "suave", textContent: `Te quedaría ${margenes}. Es una simulación: no se guarda nada.` }),
  );
  $("simulacion").hidden = false;
}

// Inputs of a saved-food card; `leer()` takes what is typed at that moment (R7).
// Unit 019: with `buscar` («Añadir a mano», not the card to change one) the name is the search box; once a saved
// thing is chosen, quantity, unit, kcal and kcal/100 g give way to only how much of it, and `leer()` says
// `guardado: {tipo, id}` (the kcal it sends are only shown: the server works them out again).
function camposFicha(a, { buscar = false } = {}) {
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
        elegido = { tipo, cosa, caja: cajaElegida({ tipo, cosa, alSoltar: soltar }) };
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

// Change card of a saved food: name, quantity, unit, kcal and day; delete with confirmation.
function fichaCambio(a) {
  const campos = camposFicha(a);
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
  if (document.visibilityState !== "visible" || pestanaActual() !== "hoy" || !diaVisto) return;
  cargarDia(diaVisto, { callado: true });
}

// -- review of the interpreted list ---------------------------------------------

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

const tarjetaVacia = () => ({
  nombre: "", cantidad: null, unidad: null, dia: hoyISO, kcal: null, proteina_g: null, hidratos_g: null,
  grasa_g: null, estimado: false, dudoso: false, supuestos: [], anadida: true,
});

function pintarRevision() {
  for (const leer of lectores) leer(); // repainting must not lose what is typed and not yet applied
  lectores = [];
  $("revision").hidden = !revision;
  preguntarDescarte(false);
  if (!revision) { $("simulacion").hidden = true; return; }
  $("dicho").textContent = revision.dicho ? `Has dicho: «${revision.dicho}»` : "";
  $("dicho").hidden = !revision.dicho;
  const otroDia = revision.origen && revision.hoy !== hoyISO;
  $("dia-grabacion").textContent = otroDia ? `Grabado el ${deFecha(revision.hoy)}: se apunta en ese día` : "";
  $("dia-grabacion").hidden = !otroDia;
  const tarjetas = revision.alimentos.map((a, i) => {
    // Applies a typed value to the model; only a real change clears "dudoso" / "estimado".
    const aplicar = (clave, convertir = (v) => v) => (valor) => {
      const nuevo = convertir(valor);
      if (nuevo === a[clave] || (Number.isNaN(nuevo) && Number.isNaN(a[clave]))) return false;
      const antes = a[clave];
      a[clave] = nuevo;
      if (clave === "nombre" || clave === "kcal") a.dudoso = false; // corrected by Andrés
      if (clave === "kcal") { a.estimado = false; a.origen = null; a.provisional = false; a.propuesta = null; }
      // unit 011: a saved recipe or fixed food keeps its kcal per gram or unit when the quantity changes
      if (clave === "cantidad" && a.origen && antes > 0 && nuevo > 0 && a.kcal !== null) {
        a.kcal = Math.round(a.kcal / antes * nuevo * 10) / 10;
      }
      if (a.cantidad !== null && !a.unidad) a.unidad = "g"; // a quantity always has a unit
      return true;
    };
    const repintar = (aplicarlo) => (valor) => { if (aplicarlo(valor)) pintarRevision(); };
    const marcas = () => {
      const m = [];
      if (a.dudoso) m.push(el("span", { className: "marca alerta", textContent: "dudoso: corrígelo o quítalo" }));
      if (a.estimado && !a.dudoso) m.push(el("span", { className: "marca", textContent: "kcal estimadas" }));
      // unit 013 (R-8): pending only without a quantity and without kcal; kcal by hand count
      if (a.cantidad === null && a.kcal === null && !a.dudoso) m.push(el("span", { className: "marca alerta", textContent: "pendiente de cantidad" }));
      if (kcalAMano(a) && !a.dudoso) m.push(el("span", { className: "marca", textContent: "kcal a mano" }));
      // unit 016 (R5): a later day is planned; an earlier one is eaten that day, only its date
      if (a.dia > revision.hoy) m.push(el("span", { className: "marca", textContent: `planeado: ${deFecha(a.dia)}` }));
      else if (a.dia && a.dia < revision.hoy) m.push(el("span", { className: "marca", textContent: deFecha(a.dia) }));
      if (a.anadida) m.push(el("span", { className: "marca", textContent: "añadido a mano" }));
      if (a.origen) m.push(el("span", { className: "marca origen", textContent: leerOrigen(a.origen) }));
      if (a.provisional) m.push(el("span", { className: "marca", textContent: "provisional" }));
      return m;
    };
    const cajaMarcas = el("div", { className: "marcas" }, ...marcas());
    const propuesta = a.propuesta ? tarjetaPropuesta(a) : null;
    const quitar = el("button", { type: "button", className: "quitar", textContent: "Quitar" });
    quitar.addEventListener("click", () => { revision.alimentos.splice(i, 1); pintarRevision(); });
    const respuesta = el("p", { className: "cabe", hidden: true });
    const cuanto = el("button", { type: "button", className: "cuanto", textContent: "¿Cuánto me cabe?" });
    cuanto.addEventListener("click", async () => {
      await simularRevision();
      const c = caben.find((x) => x.nombre === a.nombre);
      respuesta.textContent = c ? mayuscula(c.texto) : "Pon la cantidad y las kcal para saberlo.";
      respuesta.hidden = false;
    });
    const conversiones = {
      nombre: (v) => v.trim(), cantidad: comoNumero, unidad: (v) => v.trim() || null, kcal: comoNumero, dia: (v) => v,
    };
    const numerico = { step: "any", min: "0", inputMode: "decimal" };
    const actualizar = () => { cajaMarcas.replaceChildren(...marcas()); pintarBloqueo(); programarSimulacion(); };
    const nombre = campo("Alimento", a.nombre, "text", repintar(aplicar("nombre", conversiones.nombre)));
    // unit 019: the name is the search box; what is chosen keeps its kcal (shown here, worked out on the server)
    const lista = buscador({ entrada: nombre.entrada, alElegir: ({ tipo, cosa }) => elegirEnRevision(a, tipo, cosa) });
    const cosa = esElegido(a) ? cosaGuardada(a.guardado) : null;
    let campos;
    let cajaCuanto;
    let por100 = null;
    if (cosa) {
      nombre.entrada.readOnly = true;
      campos = { nombre, dia: campo("Día", a.dia, "date", repintar(aplicar("dia"))) };
      const caja = cajaElegida({
        tipo: a.guardado.tipo, cosa, cantidad: a.cantidad, unidad: a.unidad,
        alCambiar: (c) => { Object.assign(a, { cantidad: c.cantidad, unidad: c.unidad, kcal: c.kcal, provisional: c.provisional }); actualizar(); },
        alSoltar: () => {
          for (const leer of lectores) leer();
          lectores = [];
          Object.assign(a, { guardado: null, origen: null, provisional: false });
          pintarRevision();
        },
      });
      cajaCuanto = caja.caja;
    } else {
      campos = {
        nombre,
        cantidad: campo("Cantidad", a.cantidad, "number", repintar(aplicar("cantidad", comoNumero)), numerico),
        unidad: campo("Unidad", a.unidad, "text", repintar(aplicar("unidad", conversiones.unidad))),
        kcal: campo("kcal", a.kcal, "number", repintar(aplicar("kcal", comoNumero)), numerico),
        dia: campo("Día", a.dia, "date", repintar(aplicar("dia"))),
      };
      // unit 013 (R-56): kcal/100 g, kept per food while the review lives (never sent, never saved)
      por100 = kcalPor100(porCien.get(a) ?? null, (k) => {
        campos.kcal.entrada.value = String(k);
        aplicar("kcal", comoNumero)(campos.kcal.entrada.value);
        actualizar();
      }, (v) => { if (v === null) porCien.delete(a); else porCien.set(a, v); });
      cajaCuanto = el("div", { className: "cuanto" }, el("div", { className: "fila" }, campos.cantidad, campos.unidad, campos.kcal), por100.caja);
    }
    // Typing keeps the model, the marks and the block reason current without repainting the cards.
    for (const [clave, c] of Object.entries(campos)) {
      c.entrada.addEventListener("input", () => {
        if (c.entrada.readOnly) return;
        aplicar(clave, conversiones[clave])(c.entrada.value);
        if (por100 && (clave === "cantidad" || clave === "unidad")) por100.cambiar(a.cantidad, a.unidad);
        actualizar();
      });
    }
    if (por100) por100.cambiar(a.cantidad, a.unidad);
    // R9: on "Confirmar", what is in the inputs at that moment is what gets saved.
    lectores.push(() => {
      for (const [clave, c] of Object.entries(campos)) aplicar(clave, conversiones[clave])(c.entrada.value);
    });
    return el("article", { className: a.dudoso ? "tarjeta dudosa" : (a.anadida ? "tarjeta nueva" : "tarjeta") },
      cajaMarcas,
      propuesta,
      campos.nombre,
      lista,
      cajaCuanto,
      campos.dia,
      a.supuestos.length && !a.propuesta ? el("p", { className: "supuestos", textContent: `Supuestos: ${a.supuestos.join("; ")}` }) : null,
      el("div", { className: "acciones" }, cuanto, quitar), respuesta,
    );
  });
  $("lista").replaceChildren(...tarjetas);
  $("piezas").replaceChildren(...tarjetasPiezas());
  pintarBloqueo();
  programarSimulacion();
}

// Unit 019 (R5): a saved recipe or ingredient chosen on a review card. What is typed in the other cards is kept;
// this card's quantity stays only if it fits what was chosen (grams of a weighed recipe, the unit of an ingredient).
function elegirEnRevision(a, tipo, cosa) {
  for (const leer of lectores) leer();
  lectores = []; // …and this card's old inputs are not read back over the choice
  const propia = tipo === "fijo" ? unidadPropia(cosa) : "g";
  const sirve = a.cantidad > 0 && a.unidad === propia && (tipo === "fijo" || !cuenta(tipo, cosa, a.cantidad, "g").problema);
  const c = sirve ? cuenta(tipo, cosa, a.cantidad, propia) : null;
  Object.assign(a, {
    nombre: cosa.nombre, guardado: { tipo, id: cosa.id }, cantidad: sirve ? a.cantidad : null, unidad: sirve ? propia : null,
    kcal: c ? c.kcal : null, estimado: false, dudoso: false, supuestos: [], propuesta: null, kcal_estimada: null,
    coincidencia: null, origen: origenDe(tipo, cosa), provisional: c ? c.provisional : false,
    proteina_g: null, hidratos_g: null, grasa_g: null,
  });
  pintarRevision();
  const tarjeta = $("lista").children[revision.alimentos.indexOf(a)];
  const foco = tarjeta && tarjeta.querySelector(".elegida input, .elegida button.fraccion");
  if (foco) foco.focus();
}

// Unit 011 (R4): «you said X: I propose your recipe Y», and the way back to Claude's estimate.
function tarjetaPropuesta(a) {
  const usar = el("button", { type: "button", className: "cambiar", textContent: "Usar estimación" });
  usar.addEventListener("click", () => {
    for (const leer of lectores) leer(); // keep what is typed in the other cards…
    lectores = []; // …and do not read this card's inputs back over the estimate
    Object.assign(a, { nombre: a.propuesta.dicho, kcal: a.kcal_estimada, estimado: true, origen: null, provisional: false,
      propuesta: null, kcal_estimada: null, supuestos: [], guardado: null, coincidencia: null });
    pintarRevision();
  });
  return el("div", { className: "propuesta" },
    el("p", { textContent: `Has dicho «${a.propuesta.dicho}»: te propongo «${a.propuesta.guardado}». ¿No es eso?` }), usar);
}

// Cards of the other pieces: weight (numeric keyboard), objective of the day, doubtful phrases.
function tarjetaPieza(titulo, clase, ...hijos) {
  return el("article", { className: `tarjeta ${clase}` }, el("p", { className: "titulo-pieza", textContent: titulo }), ...hijos);
}

function botonQuitar(alQuitar) {
  const quitar = el("button", { type: "button", className: "quitar", textContent: "Quitar" });
  quitar.addEventListener("click", () => { alQuitar(); pintarRevision(); });
  return quitar;
}

// Inputs that copy what is typed into `modelo[clave]` on input and before confirming (R9 of 002).
function enlazar(modelo, campos, conversiones) {
  const leer = () => { for (const [clave, c] of Object.entries(campos)) modelo[clave] = conversiones[clave](c.entrada.value); };
  for (const c of Object.values(campos)) c.entrada.addEventListener("input", () => { leer(); pintarBloqueo(); });
  lectores.push(leer);
}

const comoTexto = (v) => v.trim() || null;
const comoNiveles = (v) => (v.trim() === "" ? null : v.split(/[;\s]+|,(?=\s)|,$/).filter(Boolean).map((n) => comoNumero(n.replace("−", "-"))));
const textoNiveles = (n) => (n ? n.map((x) => numero.format(x)).join("; ") : "");

function tarjetasPiezas() {
  const tarjetas = [];
  const p = revision.peso;
  if (p) {
    const campos = {
      kg: campo("Peso (kg)", p.kg, "text", null, { inputMode: "decimal", autocomplete: "off" }),
      dia: campo("Día", p.dia, "date"),
      nota: campo("Nota", p.nota, "text"),
    };
    campos.kg.entrada.value = p.kg === null || Number.isNaN(p.kg) ? "" : kilos.format(p.kg);
    enlazar(p, campos, { kg: comoNumero, dia: (v) => v, nota: comoTexto });
    const marcas = p.dia !== revision.hoy ? [el("span", { className: "marca", textContent: deFecha(p.dia) })] : [];
    tarjetas.push(tarjetaPieza("Peso del día", "pieza peso",
      el("div", { className: "marcas" }, ...marcas),
      el("div", { className: "fila dos" }, campos.kg, campos.dia), campos.nota,
      botonQuitar(() => { revision.peso = null; })));
  }
  const o = revision.objetivo;
  if (o) {
    const campos = {
      tipo: campo("Tipo de día", o.tipo, "text"),
      porcentaje: campo("% objetivo (con signo)", o.porcentaje, "text", null, { inputMode: "decimal" }),
      niveles: campo("Niveles (%; separa con ;)", textoNiveles(o.niveles), "text"),
      pasos: campo("Pasos previstos", o.pasos, "number", null, { inputMode: "numeric", min: "0", step: "1" }),
      entreno: campo("Entreno previsto (aún no suma kcal)", o.entreno, "text"),
    };
    conSigno(campos.porcentaje.entrada);
    enlazar(o, campos, { tipo: comoTexto, porcentaje: (v) => comoNumero(v.replace("−", "-")), niveles: comoNiveles, pasos: comoNumero, entreno: comoTexto });
    tarjetas.push(tarjetaPieza("Objetivo del día", "pieza objetivo",
      el("div", { className: "fila dos" }, campos.tipo, campos.porcentaje),
      el("div", { className: "fila dos" }, campos.niveles, campos.pasos), campos.entreno,
      botonQuitar(() => { revision.objetivo = null; })));
  }
  revision.dudosas.forEach((frase, i) => {
    tarjetas.push(tarjetaPieza("No sé dónde ponerlo", "dudosa",
      el("span", { className: "marca alerta", textContent: "dudoso: quítalo para poder confirmar" }),
      el("p", { className: "frase", textContent: `«${frase}»` }),
      botonQuitar(() => { revision.dudosas.splice(i, 1); })));
  });
  return tarjetas;
}

function motivoBloqueo() {
  const lista = revision.alimentos;
  const nombres = (filtro) => lista.filter(filtro).map((a) => `«${a.nombre}»`);
  const dudosos = nombres((a) => a.dudoso);
  const sinKcal = nombres((a) => !a.dudoso && a.nombre && a.cantidad !== null && a.kcal === null);
  const malos = nombres((a) => a.nombre && ((a.cantidad !== null && !(a.cantidad > 0)) || Number.isNaN(a.kcal) || !a.dia));
  const sinCuanto = nombres((a) => esElegido(a) && !(a.cantidad > 0)); // unit 019: chosen, only how much is missing
  const { peso, objetivo, dudosas } = revision;
  if (dudosas.length) return `No sé dónde poner ${dudosas.map((d) => `«${d}»`).join(", ")}: quítalo para poder confirmar.`;
  if (!lista.length && !peso && !objetivo) return "No queda nada que guardar.";
  if (peso && (peso.kg === null || Number.isNaN(peso.kg) || peso.kg < PESO_MIN || peso.kg > PESO_MAX)) return `El peso tiene que estar entre ${PESO_MIN} y ${PESO_MAX} kg.`;
  if (peso && !peso.dia) return "Pon el día del peso.";
  if (peso && peso.dia > revision.hoy) return "El peso no puede ser de un día que aún no ha llegado.";
  if (objetivo && Number.isNaN(objetivo.porcentaje)) return "El % del objetivo del día no es un número.";
  if (objetivo && objetivo.niveles && objetivo.niveles.some((n) => Number.isNaN(n))) return "Algún nivel del día no es un número.";
  if (objetivo && (Number.isNaN(objetivo.pasos) || (objetivo.pasos !== null && !Number.isInteger(objetivo.pasos)))) return "Los pasos previstos tienen que ser un número entero.";
  if (!lista.length) return "";
  if (lista.some((a) => !a.nombre)) return "Hay un alimento sin nombre: ponle nombre o quítalo para poder confirmar.";
  if (dudosos.length) return `No entiendo ${dudosos.join(", ")}: corrígelo o quítalo para poder confirmar.`;
  if (sinCuanto.length) return `Pon cuánto has cogido de ${sinCuanto.join(", ")}.`;
  if (malos.length) return `Revisa la cantidad, las kcal o el día de ${malos.join(", ")}.`;
  if (sinKcal.length) return `Pon las kcal de ${sinKcal.join(", ")}.`;
  return "";
}

// The button only LOOKS disabled while something is missing: a tap still reads the cards (R9),
// in case the last input never told the page what was typed, and then says what is missing.
function pintarBloqueo() {
  const motivo = motivoBloqueo();
  $("bloqueo").textContent = motivo;
  $("bloqueo").hidden = !motivo;
  $("confirmar").classList.toggle("bloqueado", Boolean(motivo));
  // it stays operable (a tap reads the cards and says what is missing): the reason describes it
  if (motivo) $("confirmar").setAttribute("aria-describedby", "bloqueo");
  else $("confirmar").removeAttribute("aria-describedby");
}

function anadirTarjeta() {
  revision.alimentos.push(tarjetaVacia());
  pintarRevision();
  const nombres = $("lista").querySelectorAll("article:last-child input[type=text]");
  if (nombres.length) nombres[0].focus();
}

async function interpretar(evento) {
  evento.preventDefault();
  const texto = $("texto").value;
  if (!texto.trim()) return;
  irAPestanaHoy(); // the review lives in Hoy
  $("enviar").disabled = true;
  mensaje("Interpretando…");
  // unit 016 (R2): the text of a list of the queue is interpreted with the day it was recorded
  const fecha = textoDeLista ? textoDeLista.fecha : null;
  const { ok, datos } = await api("/api/interpretar", fecha ? { texto, fecha } : { texto });
  $("enviar").disabled = false;
  if (!ok) { // the written text stays in the box (R10)
    mensaje(datos.error ?? "No he podido interpretar lo que has escrito. Inténtalo de nuevo.", "error");
    return;
  }
  mensaje("");
  revision = {
    texto, hoy: fecha ?? hoyISO, alimentos: datos.alimentos, peso: datos.peso ?? null,
    objetivo: datos.objetivo_dia ?? null, dudosas: datos.dudosas ?? [],
  };
  if (textoDeLista) { // a dictated list Claude could not interpret: it is closed with this review
    revision.origen = textoDeLista.id;
    revision.dicho = textoDeLista.texto;
    textoDeLista = null;
  }
  revision.original = huella(revision);
  $("confirmar").disabled = false; // a new review starts usable, also after an earlier confirmation
  pintarRevision();
  verRevision();
}

// The review sits in Hoy between the card and the list; the screen scrolls to it.
function verRevision() {
  $("revision").scrollIntoView({ behavior: "smooth", block: "start" });
}

// What was interpreted, to tell later whether Andrés changed anything before confirming.
const huella = (r) => JSON.stringify([r.alimentos, r.peso, r.objetivo, r.dudosas]);

async function confirmar() {
  for (const leer of lectores) leer();
  if (motivoBloqueo()) { pintarRevision(); return; }
  $("confirmar").disabled = true;
  const alimentos = revision.alimentos.map(({ anadida, ...a }) => a);
  const { ok, datos } = await api("/api/confirmar", {
    texto: revision.texto, dia: revision.hoy, alimentos, peso: revision.peso, objetivo_dia: revision.objetivo,
    dudosas: revision.dudosas, sin_cambios: huella(revision) === revision.original,
  });
  if (!ok) {
    mensaje(datos.error ?? "No se ha podido guardar.", "error");
    $("confirmar").disabled = false;
    return;
  }
  const partes = ["Guardado."];
  if (datos.peso) partes.push(`Peso ${kilos.format(datos.peso.kg)} kg (${nombreDelDia(datos.peso.dia).toLowerCase()}).`);
  if (datos.objetivo_dia) partes.push(`Objetivo del día: ${datos.objetivo_dia.tipo ?? "guardado"}.`);
  const planeados = datos.guardados.filter((a) => a.estado === "planeado");
  if (planeados.length) partes.push(`Planeado: ${planeados.map((a) => `${a.nombre} (${deFecha(a.dia)})`).join(", ")}.`);
  const pendientes = datos.guardados.filter((a) => a.pendiente_cantidad);
  if (pendientes.length) partes.push(`Falta la cantidad de ${pendientes.map((a) => a.nombre).join(", ")}: no suma.`);
  mensaje(partes.join(" "), "bien");
  const origen = revision.origen;
  if (!origen || $("texto").value === revision.dicho) $("texto").value = "";
  revision = null;
  pintarRevision();
  if (diaVisto === datos.dia.fecha) pintarDia(datos.dia);
  else cargarDia(diaVisto); // the screen stays on the day being looked at (R6)
  if (origen) { await cola.borrarLista(origen); if (await abrirSiguienteLista()) return; }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("entrada").addEventListener("submit", interpretar);
$("confirmar").addEventListener("click", confirmar);
$("descartar").addEventListener("click", descartar);
$("descarte-no").addEventListener("click", () => preguntarDescarte(false));
$("descarte-si").addEventListener("click", async () => {
  const origen = revision && revision.origen;
  preguntarDescarte(false);
  revision = null;
  pintarRevision();
  mensaje("Lista descartada: no se ha guardado nada.");
  if (origen) { await cola.borrarLista(origen); await abrirSiguienteLista(); }
});
$("revisar").addEventListener("click", () => abrirSiguienteLista());
$("grabar").addEventListener("click", alternarGrabacion);
$("anadir-revision").addEventListener("click", anadirTarjeta);
$("cerrar-dia").addEventListener("click", cerrarDia);
$("anadir").addEventListener("click", () => { anadiendo = true; editando = null; mensaje(""); pintarDia(ultimoDia); });
$("anterior").addEventListener("click", () => irA(sumarDias(diaVisto, -1)));
$("siguiente").addEventListener("click", () => irA(sumarDias(diaVisto, 1)));
$("ir-hoy").addEventListener("click", () => irA(hoyISO));
enlazarControles();
enlazarHoja();
enlazarPestanas();
vigilarAltoEntrada();
$("fecha").textContent = deFecha(cola.fechaLocal()); // before the PC answers, or if it never does
cargarDia();
document.addEventListener("visibilitychange", mirarElDia);
setInterval(mirarElDia, MINUTO_MS);
cola.iniciar(alCambiarCola);
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
  const h = location.hash.slice(1);
  return PESTANAS.includes(h) ? h : "hoy";
}

// Unit 017: from Balance, a day opens in Hoy (its list of foods, its margin).
function irADia(fecha) {
  diaPedido = fecha;
  if (pestanaActual() === "hoy") mostrarPestana();
  else location.hash = "hoy";
}

function irAPestanaHoy() {
  if (pestanaActual() !== "hoy") location.hash = "hoy";
}

function mostrarPestana() {
  const actual = pestanaActual();
  for (const p of PESTANAS) $(p).hidden = p !== actual;
  for (const a of document.querySelectorAll(".pestana")) {
    if (a.dataset.ir === actual) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  $("entrada").hidden = actual === "recetas"; // the box is for Hoy and Balance (unit 027: where Peso had it)
  if ($("hoja").open) $("hoja").close();
  if (actual === "hoy") document.title = `TriData · ${diaVisto ? nombreDelDia(diaVisto) : "Hoy"}`;
  else document.title = `TriData · ${mayuscula(actual)}`;
  // unit 013: back in Hoy from another tab, the day is asked again (what was logged from Recetas shows)
  if (actual === "hoy" && diaPedido) { // unit 017: a day tapped in Balance
    const fecha = diaPedido;
    diaPedido = null;
    irA(fecha);
  } else if (actual === "hoy" && pestanaVista !== null && pestanaVista !== "hoy" && diaVisto) cargarDia(diaVisto);
  if (pestanaVista === "recetas" && actual !== "recetas") cargarGuardado({ otraVez: true }); // unit 019: the search box
  pestanaVista = actual;
  if (actual === "balance") pintarBalance($("balance-contenido"), irADia);
  if (actual === "recetas") pintarRecetas($("recetas"));
  window.scrollTo(0, 0);
}

function enlazarPestanas() {
  // tapping Hoy while already in Hoy goes back to today (the «Hoy» button that was next to ▶)
  document.querySelector('.pestana[data-ir="hoy"]').addEventListener("click", () => {
    if (pestanaActual() === "hoy" && hoyISO && diaVisto !== hoyISO) irA(hoyISO);
  });
  window.addEventListener("hashchange", mostrarPestana);
  mostrarPestana();
}

// -- unit 006: dictation and the recordings queue ---------------------------------

const reloj = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
const horaCorta = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

async function alternarGrabacion() {
  if (grabacion) { grabacion.parar(); return; }
  if (!puedeGrabar()) { mensaje("Este navegador no puede grabar audio.", "error"); return; }
  const boton = $("grabar");
  const tiempo = $("grabar-tiempo");
  try {
    grabacion = await grabar({
      alSegundo: (s) => { tiempo.textContent = reloj(s); tiempo.hidden = false; },
      alTerminar: async ({ audio, tipo, segundos }) => {
        grabacion = null;
        tiempo.hidden = true;
        boton.setAttribute("aria-label", "Grabar lo que has comido");
        boton.classList.remove("grabando");
        if (segundos >= MAX_SEGUNDOS) mensaje("La grabación ha llegado a 2 minutos y ha parado sola.");
        try {
          esperada = await cola.guardarGrabacion(audio, tipo);
          if (conectado !== false) mensaje("Grabación guardada en el móvil. Enviándola al PC…");
        } catch {
          mensaje("No se ha podido guardar la grabación en el móvil.", "error");
        }
      },
    });
    boton.classList.add("grabando");
    boton.setAttribute("aria-label", "Parar la grabación");
    mensaje("Grabando… toca el micrófono otra vez al acabar (como mucho 2 minutos).");
  } catch (error) {
    grabacion = null;
    const sinPermiso = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
    mensaje(sinPermiso ? "Sin permiso para el micrófono: dáselo a esta web en los ajustes de Safari." : "No se ha podido usar el micrófono.", "error");
  }
}

// The notices of the queue: recordings not sent, not transcribable ones, lists to review.
async function alCambiarCola({ conexion }) {
  if (conexion !== null) ponerConexion(conexion);
  if (conexion === false) esperada = null; // it will come back later: it waits with the others
  const e = await cola.estado();
  $("sin-enviar").textContent = e.sinEnviar ? `${plural(e.sinEnviar, "grabación sin enviar", "grabaciones sin enviar")}: se enviarán solas cuando el PC responda.` : "";
  $("sin-enviar").hidden = !e.sinEnviar;
  $("no-transcritas").replaceChildren(...e.noTranscribibles.map(tarjetaNoTranscrita));
  const abierta = revision && revision.origen;
  const quedan = e.listas.filter((l) => l.id !== abierta && (!textoDeLista || l.id !== textoDeLista.id));
  $("pendientes").hidden = !quedan.length;
  $("pendientes-texto").textContent = plural(quedan.length, "lista pendiente de revisar", "listas pendientes de revisar");
  if (esperada && !revision && !textoDeLista) {
    if (e.listas.some((l) => l.id === esperada)) { mensaje(""); await abrirLista(esperada); }
    else {
      const fallida = e.noTranscribibles.find((g) => g.id === esperada);
      if (fallida) { esperada = null; mensaje(fallida.error || "No se ha podido transcribir la grabación.", "error"); }
    }
  }
}

function tarjetaNoTranscrita(g) {
  const quitar = el("button", { type: "button", className: "peligro", textContent: "Descartar" });
  const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, descartar" });
  const no = el("button", { type: "button", textContent: "No" });
  const pregunta = el("div", { className: "acciones", hidden: true }, si, no);
  quitar.addEventListener("click", () => { pregunta.hidden = false; quitar.hidden = true; });
  no.addEventListener("click", () => { pregunta.hidden = true; quitar.hidden = false; });
  si.addEventListener("click", () => cola.borrarGrabacion(g.id));
  return el("article", { className: "tarjeta dudosa" },
    el("p", { className: "fuerte", textContent: `Grabación del ${horaCorta.format(new Date(g.creada))}: no se ha podido transcribir.` }),
    el("p", { className: "suave", textContent: g.error || "" }),
    quitar, pregunta);
}

// Opens the oldest list waiting (or `id`) in the usual review. A list Claude could not interpret
// puts its text in the box, to tap «Interpretar». False if there is none.
async function abrirSiguienteLista() {
  const { listas } = await cola.estado();
  if (!listas.length) return false;
  await abrirLista(listas[0].id);
  return true;
}

async function abrirLista(id) {
  const lista = (await cola.estado()).listas.find((l) => l.id === id);
  if (!lista) return;
  if (esperada === id) esperada = null;
  if (revision && revision.origen === id) return;
  if (!lista.interpretacion) {
    textoDeLista = { id: lista.id, texto: lista.texto, fecha: lista.fecha };
    irAPestanaHoy();
    revision = null;
    pintarRevision();
    $("texto").value = lista.texto;
    mensaje(`Has dicho: «${lista.texto}». El PC no pudo interpretarlo: revisa el texto y pulsa «Interpretar».`, "error");
    $("texto").focus();
    await alCambiarCola({ conexion: null });
    return;
  }
  const i = lista.interpretacion;
  revision = {
    texto: lista.texto, hoy: lista.fecha, alimentos: i.alimentos, peso: i.peso ?? null,
    objetivo: i.objetivo_dia ?? null, dudosas: i.dudosas ?? [], origen: lista.id, dicho: lista.texto,
  };
  revision.original = huella(revision);
  $("confirmar").disabled = false;
  irAPestanaHoy();
  pintarRevision();
  await alCambiarCola({ conexion: null });
  verRevision();
}

function preguntarDescarte(abierta) {
  $("confirmar-descarte").hidden = !abierta;
  $("confirmar").parentElement.hidden = abierta;
}

function descartar() {
  if (!revision) return;
  $("confirmar-descarte").querySelector("p").textContent = revision.origen
    ? "¿Descartar esta lista? Lo que dijiste no se guardará." : "¿Descartar esta lista? No se guardará nada.";
  preguntarDescarte(true);
  $("descarte-si").focus();
}

// The fixed box and tabs at the bottom never cover the end of the page: <main> leaves their height free.
function vigilarAltoEntrada() {
  const poner = () => document.documentElement.style.setProperty("--alto-abajo", `${$("abajo").offsetHeight}px`);
  poner();
  if (window.ResizeObserver) new ResizeObserver(poner).observe($("abajo"));
}
