// The Asistente's conversations (unit 032, REC-20 of `preguntar-equipo`). Plain ES module, no build step.
// `#asistente` is the list (newest first, «Nueva conversación», what it knows of the medical documents);
// `#asistente/N` one conversation and `#asistente/nueva` one that starts. In a conversation the box at the bottom
// (app.js's #entrada) is the chat's (S-37): what is written or dictated goes to `enviarAlChat`. Every conversation
// sees all the context of the PC; the line above says what and until when (R-115). The answer is painted as it
// arrives (POST …/mensajes answers one JSON per line: inicio, trozo…, fin | error). Without an answer the message
// stays «sin respuesta» with «Reintentar» (R-117): on the PC when the API failed, on this phone (localStorage) when
// the PC did not answer. A change the answer proposes is the card of the 021 (asistente.js), confirmed with
// /api/asistente/*; the cards still waiting are written back to the answer. Every text is painted as text.
// Unit 041 (REC-23): the team. A conversation that starts lets Andrés choose who he talks to (Entrenador, Fisio,
// Nutricionista or the Asistente of always, the default); under what was said, «Pedir opinión a…» makes another role
// answer in the same conversation (POST …/opinion). Each answer carries the name of its role (not in a conversation
// where only the Asistente spoke, as before) and the line says the model.
// Unit 045 (REC-25): «Adjuntar ruta» offers the routes saved in Garmin, the runs already done and uploading a GPX
// (GET /api/rutas, POST …/ruta; a conversation that starts is created by its route). The profile is drawn on top
// of the conversation (SVG: the altitude every 100 m and the climbs shaded); with Garmin not answering, or a route
// without altitude, it says so and the GPX stays offered. Only the profile goes to the coach, never the coordinates.

import { pintarCambios } from "./asistente.js";

const $ = (id) => document.getElementById(id);
const CLAVE_PENDIENTES = "tridata.chat-pendientes"; // messages the PC never got, by conversation ("nueva": a new one)
const hora = new Intl.DateTimeFormat("es-ES", { hour: "numeric", minute: "2-digit" });
const diaSemana = new Intl.DateTimeFormat("es-ES", { weekday: "short" });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });

let api = null; // app.js's `api` (JSON in, JSON out)
let alHecho = () => {}; // app.js: a card was confirmed (the day changed)
let vista = null; // { id (null while new), conversacion, mensajes }
let enviando = false;
let peticion = 0; // drops a late answer of the list or of a conversation
let papeles = []; // unit 041: the team, with the model of each role (/api/conversaciones, /api/equipo)
let papelNuevo = "asistente"; // who a conversation that starts goes to

function el(etiqueta, props = {}, ...hijos) {
  const nodo = Object.assign(document.createElement(etiqueta), props);
  nodo.append(...hijos.filter((h) => h !== null && h !== undefined && h !== false));
  return nodo;
}

const limpia = (c) => Object.fromEntries(Object.entries(c).filter(([k]) => !k.startsWith("_")));

function aviso(texto, tipo = "") {
  const nodo = $("conv-aviso");
  nodo.textContent = texto;
  nodo.className = tipo;
  nodo.hidden = !texto;
}

// -- the address -----------------------------------------------------------------------------------------------------

function sub() {
  const [, resto] = location.hash.slice(1).split("/");
  return resto ?? "";
}

export const enConversacion = () => location.hash.startsWith("#asistente/");

export function iniciarConversaciones(opciones) {
  ({ api, alHecho } = opciones);
  $("conv-nueva").addEventListener("click", () => { location.hash = "asistente/nueva"; });
  $("conv-volver").addEventListener("click", () => { location.hash = "asistente"; });
  $("conv-ver-medicos").addEventListener("click", alternarMedicos);
  $("conv-renombrar").addEventListener("click", () => {
    $("conv-borrar-caja").hidden = true;
    $("conv-renombrar-caja").hidden = false;
    $("conv-titulo-nuevo").value = vista?.conversacion?.titulo ?? "";
    $("conv-titulo-nuevo").focus();
  });
  $("conv-renombrar-no").addEventListener("click", () => { $("conv-renombrar-caja").hidden = true; });
  $("conv-renombrar-si").addEventListener("click", renombrar);
  $("conv-borrar").addEventListener("click", () => { $("conv-renombrar-caja").hidden = true; $("conv-borrar-caja").hidden = false; });
  $("conv-borrar-no").addEventListener("click", () => { $("conv-borrar-caja").hidden = true; });
  $("conv-borrar-si").addEventListener("click", borrar);
  prepararRutas();
  $("pensar").addEventListener("click", () => {
    const boton = $("pensar");
    boton.setAttribute("aria-pressed", String(boton.getAttribute("aria-pressed") !== "true"));
  });
}

// Called by app.js every time #asistente… shows.
// -- unit 041: the team ---------------------------------------------------------------------------------------------

const papelDe = (id) => papeles.find((p) => p.id === id) ?? { id, nombre: "Asistente", icono: "💬", modelo_nombre: "" };

async function cargarEquipo() {
  if (papeles.length) return;
  const { ok, datos } = await api("/api/equipo");
  if (ok) papeles = datos.papeles;
}

// The name on an answer: always for a role of the team; for the Asistente only where another role spoke too.
function conNombre(papel) {
  if (papel && papel !== "asistente") return true;
  return Boolean(vista?.mensajes?.some((m) => m.quien === "asistente" && m.papel && m.papel !== "asistente"));
}

function nombrePapel(papel) {
  return el("p", { className: `papel-nombre papel-${papel}`, textContent: papelDe(papel).nombre });
}

function pintarPapeles() {
  const nueva = vista && vista.id === null;
  $("conv-papeles-caja").hidden = !nueva;
  $("conv-icono").textContent = papelDe(nueva ? papelNuevo : vista?.conversacion?.papel ?? "asistente").icono;
  if (!nueva) return;
  $("conv-papeles").replaceChildren(...papeles.map((p) => {
    const boton = el("button", { type: "button", className: "papel" },
      el("span", { className: "papel-icono", textContent: p.icono, ariaHidden: "true" }),
      el("span", { className: "papel-texto" }, el("strong", { textContent: p.nombre }),
        el("span", { className: "suave", textContent: p.descripcion })));
    boton.dataset.papel = p.id;
    boton.setAttribute("aria-pressed", String(p.id === papelNuevo));
    boton.addEventListener("click", () => {
      papelNuevo = p.id;
      pintarPapeles();
      pintarLineaNueva();
      $("texto").focus();
    });
    return boton;
  }));
}

function pintarLineaNueva() {
  if (vista?.lineaBase === undefined) return;
  const modelo = papelDe(papelNuevo).modelo_nombre;
  $("conv-linea").textContent = vista.lineaBase + (modelo ? ` · ${modelo}` : "");
}

// «Pedir opinión a: 🩺 Fisio · 🥗 Nutricionista · 💬 Asistente» (R-138), under what was said.
function pintarOpinion() {
  const caja = $("conv-opinion");
  const hay = vista && vista.id !== null && vista.mensajes.some((m) => m.quien === "asistente");
  caja.hidden = !hay;
  if (!hay) return;
  const otros = papeles.filter((p) => p.id !== vista.conversacion.papel);
  caja.replaceChildren(el("span", { className: "suave", textContent: "Pedir opinión a…" }), ...otros.map((p) => {
    const boton = el("button", { type: "button", className: "opinion", textContent: `${p.icono} ${p.nombre}` });
    boton.dataset.papel = p.id;
    boton.addEventListener("click", () => opinar(p.id));
    return boton;
  }));
}

async function opinar(papel) {
  if (enviando || !vista || vista.id === null) return;
  await conversar(`/api/conversaciones/${vista.id}/opinion`, { papel }, null, papel);
}

export function mostrarAsistente() {
  const s = sub();
  const enChat = Boolean(s);
  $("conv-lista-vista").hidden = enChat;
  $("conv-chat").hidden = !enChat;
  $("asistente").querySelector("h1").hidden = enChat;
  $("conv-renombrar-caja").hidden = true;
  $("conv-borrar-caja").hidden = true;
  aviso("");
  if (!enChat) { vista = null; cargarLista(); return; }
  if (s === "nueva") { abrirNueva(); return; }
  const id = Number(s);
  if (!Number.isInteger(id) || id <= 0) { location.hash = "asistente"; return; }
  abrir(id);
}

// -- the list (R-112) --------------------------------------------------------------------------------------------

function cuando(iso) {
  const d = new Date(iso);
  const hoy = new Date();
  const dias = Math.round((new Date(hoy.toDateString()) - new Date(d.toDateString())) / 86_400_000);
  if (dias <= 0) return hora.format(d);
  if (dias === 1) return "ayer";
  if (dias < 7) return diaSemana.format(d).replace(".", "");
  return diaMes.format(d).replace(".", "");
}

async function cargarLista() {
  const mia = ++peticion;
  const { ok, datos } = await api("/api/conversaciones");
  if (mia !== peticion) return;
  if (!ok) { aviso(datos.error ?? "No he podido traer tus conversaciones.", "error"); return; }
  papeles = datos.papeles ?? papeles;
  const filas = datos.conversaciones.map((c) => {
    const papel = papelDe(c.papel ?? "asistente");
    const ultima = c.papel && c.papel !== "asistente" ? `${papel.nombre} · ${c.ultima}` : c.ultima;
    const boton = el("button", { type: "button", className: "conversacion con-papel" },
      el("span", { className: "papel-icono", textContent: papel.icono, ariaHidden: "true" }),
      el("span", { className: "conv-cab" }, el("strong", { textContent: c.titulo }), el("span", { className: "suave", textContent: cuando(c.usada) })),
      el("span", { className: "suave conv-ultima", textContent: ultima }));
    boton.addEventListener("click", () => { location.hash = `asistente/${c.id}`; });
    return el("li", {}, boton);
  });
  $("conv-lista").replaceChildren(...filas);
  $("conv-lista").hidden = !filas.length;
  $("conv-vacia").hidden = Boolean(filas.length);
  pintarGasto(datos.tope);
  $("conv-tope").hidden = !datos.tope.alcanzado;
  $("conv-tope").textContent = datos.tope.alcanzado
    ? `Has llegado al tope de ${datos.tope.dolares} $ de este mes: el asistente vuelve el día 1.` : "";
}

// -- unit 039: what the month has cost of the cap ------------------------------------------------------------------

function dolares(x) {
  return x > 0 && x < 0.01 ? "< 0,01 $" : `${x.toFixed(2).replace(".", ",")} $`;
}

function pintarGasto(tope) {
  const barra = $("conv-gasto-barra");
  barra.max = tope.dolares;
  barra.low = barra.high = tope.dolares * 0.8;  // from 80 %: the meter's warning colour
  barra.value = Math.min(tope.gastado, tope.dolares);  // never past full
  $("conv-gasto").textContent = `Gasto del mes: ${dolares(tope.gastado)} de ${tope.dolares} $`;
}

// -- what it knows of the medical documents (R4) --------------------------------------------------------------------

async function alternarMedicos() {
  const caja = $("conv-medicos");
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.replaceChildren(el("p", { className: "suave", textContent: "Cargando…" }));
  pintarMedicos(await api("/api/conversaciones/medicos"));
}

function pintarMedicos({ ok, datos }) {
  const caja = $("conv-medicos");
  if (!ok) { caja.replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido mirarlo." })); return; }
  const docs = datos.documentos.map((d) => el("article", { className: "doc-medico" },
    el("p", { className: "fuerte", textContent: [d.titulo, d.fecha].filter(Boolean).join(" · ") }),
    el("ul", { className: "valores" }, ...d.valores.map((v) => el("li", {
      textContent: `${v.prueba}: ${v.valor}${v.unidad ? ` ${v.unidad}` : ""}${v.rango ? ` (${v.rango})` : ""}`,
    }))),
    ...d.notas.map((n) => el("p", { className: "suave", textContent: n }))));
  const releer = el("button", { type: "button", textContent: "Volver a leerlas" });
  releer.addEventListener("click", async () => {
    releer.disabled = true;
    releer.textContent = "Leyendo tus PDF…";
    pintarMedicos(await api("/api/conversaciones/medicos/releer", {}));
  });
  caja.replaceChildren(
    datos.aviso ? el("p", { className: "aviso", textContent: datos.aviso }) : null,
    docs.length ? null : el("p", { className: "suave", textContent: "Aún no ha leído ningún documento: los leerá con tu primer mensaje." }),
    ...docs,
    datos.carpeta ? releer : null);
}

// -- one conversation --------------------------------------------------------------------------------------------

function pendientes() {
  try { return JSON.parse(localStorage.getItem(CLAVE_PENDIENTES) || "{}"); } catch { return {}; }
}

function guardarPendientes(todos) {
  try { localStorage.setItem(CLAVE_PENDIENTES, JSON.stringify(todos)); } catch { /* private mode: kept on screen */ }
}

const claveDe = (id) => String(id ?? "nueva");

async function abrirNueva() {
  papelNuevo = "asistente";
  vista = { id: null, conversacion: { titulo: "Nueva conversación", papel: "asistente" }, mensajes: [], ruta: null };
  pintarChat();
  $("texto").focus();
  const mia = ++peticion;
  const { ok, datos } = await api("/api/conversaciones");
  if (!ok || mia !== peticion) return;
  papeles = datos.papeles ?? papeles;
  vista.lineaBase = datos.linea ?? "";
  pintarPapeles();
  pintarLineaNueva();
}

async function abrir(id) {
  const mia = ++peticion;
  $("conv-mensajes").replaceChildren(el("p", { className: "suave", textContent: "Cargando…" }));
  const [{ ok, estado, datos }] = await Promise.all([api(`/api/conversaciones/${id}`), cargarEquipo()]);
  if (mia !== peticion) return;
  if (!ok) {
    if (estado === 404) { location.hash = "asistente"; return; }
    aviso(datos.error ?? "No he podido traer la conversación.", "error");
    $("conv-mensajes").replaceChildren();
    return;
  }
  vista = { id, conversacion: datos.conversacion, mensajes: datos.mensajes, linea: datos.linea, ruta: datos.ruta ?? null };
  pintarChat();
  abajo();
}

function abajo() {
  window.scrollTo({ top: document.documentElement.scrollHeight });
}

function pintarChat() {
  $("conv-titulo").textContent = vista.conversacion.titulo;
  $("conv-renombrar").hidden = vista.id === null;
  $("conv-borrar").hidden = vista.id === null;
  if (vista.linea) $("conv-linea").textContent = vista.linea;
  const locales = (pendientes()[claveDe(vista.id)] ?? []).map((p) => ({ ...p, quien: "andres", local: true, sin_respuesta: true }));
  $("conv-mensajes").replaceChildren(...vista.mensajes.map(burbuja), ...locales.map(burbuja));
  pintarPapeles();
  pintarOpinion();
  pintarRuta();
}

function burbuja(m) {
  const nodo = el("div", { className: `burbuja ${m.quien}` });
  if (m.quien === "asistente" && conNombre(m.papel)) nodo.append(nombrePapel(m.papel ?? "asistente"));
  nodo.append(el("p", { className: "texto-burbuja", textContent: m.texto }));
  if (m.quien === "andres" && m.sin_respuesta) {
    const reintentar = el("button", { type: "button", className: "reintentar", textContent: "Reintentar" });
    reintentar.addEventListener("click", () => (m.local ? reenviarLocal(m) : reintentarEnElPC(m)));
    nodo.append(el("div", { className: "sin-respuesta-fila" }, el("span", { className: "sin-respuesta", textContent: "sin respuesta" }), reintentar));
  }
  if (m.quien === "asistente" && m.tarjetas && m.tarjetas.length) {
    const caja = el("div", { className: "cambios" });
    pintarCambios(caja, m.tarjetas, {
      api,
      alHecho: (datos) => { aviso(datos.hecho, "bien"); alHecho(datos); },
      alDecidir: () => api(`/api/conversaciones/mensaje/${m.id}/tarjetas`, { tarjetas: m.tarjetas.map(limpia) }),
    });
    return el("div", { className: "con-cambios" }, nodo, caja);
  }
  return nodo;
}

// -- sending (R-113), the answer as it arrives (Q-15) and «Reintentar» (R-117) -------------------------------------

// What app.js calls when the box is sent inside a conversation. False if it was not taken (one at a time).
export function enviarAlChat(texto) {
  if (enviando || !vista) return false;
  const pensar = $("pensar").getAttribute("aria-pressed") === "true";
  $("pensar").setAttribute("aria-pressed", "false");
  const ruta = vista.id === null ? "/api/conversaciones" : `/api/conversaciones/${vista.id}/mensajes`;
  conversar(ruta, vista.id === null ? { texto, pensar, papel: papelNuevo } : { texto, pensar }, texto);
  return true;
}

async function reintentarEnElPC(m) {
  if (enviando) return;
  vista.mensajes = vista.mensajes.filter((x) => x.id !== m.id);
  await conversar(`/api/conversaciones/${vista.id}/reintentar`, { mensaje_id: m.id }, m.texto);
}

async function reenviarLocal(m) {
  if (enviando) return;
  const todos = pendientes();
  todos[claveDe(vista.id)] = (todos[claveDe(vista.id)] ?? []).filter((p) => p.lid !== m.lid);
  guardarPendientes(todos);
  const ruta = vista.id === null ? "/api/conversaciones" : `/api/conversaciones/${vista.id}/mensajes`;
  await conversar(ruta, vista.id === null ? { texto: m.texto, papel: papelNuevo } : { texto: m.texto }, m.texto);
}

function guardarLocal(texto) {
  const todos = pendientes();
  const clave = claveDe(vista.id);
  todos[clave] = [...(todos[clave] ?? []), { lid: `${Date.now()}`, texto }];
  guardarPendientes(todos);
}

async function* lineas(respuesta) {
  const lector = respuesta.body.getReader();
  const decodificador = new TextDecoder();
  let resto = "";
  for (;;) {
    const { value, done } = await lector.read();
    if (done) break;
    resto += decodificador.decode(value, { stream: true });
    let corte;
    while ((corte = resto.indexOf("\n")) >= 0) {
      const linea = resto.slice(0, corte).trim();
      resto = resto.slice(corte + 1);
      if (linea) yield JSON.parse(linea);
    }
  }
  if (resto.trim()) yield JSON.parse(resto);
}

// `texto` null: «Pedir opinión a…» (`papel` answers; nothing of Andrés is written).
async function conversar(ruta, cuerpo, texto, papel = null) {
  enviando = true;
  aviso("");
  const propia = vista;
  const quien = papel ?? (propia.id === null ? papelNuevo : propia.conversacion.papel) ?? "asistente";
  const pregunta = texto === null ? null : el("div", { className: "burbuja andres" }, el("p", { className: "texto-burbuja", textContent: texto }));
  const escribe = el("p", { className: "texto-burbuja escribiendo", textContent: "…" });
  // «…» while it thinks; it becomes a message (`.burbuja`) with its first words
  const respuesta = el("div", { className: "burbuja-espera", ariaLabel: `${papelDe(quien).nombre} está escribiendo` },
    conNombre(quien) || papel ? nombrePapel(quien) : null, escribe);
  $("conv-mensajes").append(...[pregunta, respuesta].filter(Boolean));
  $("conv-papeles-caja").hidden = true;
  $("conv-opinion").hidden = true;
  abajo();
  let r;
  try {
    r = await fetch(ruta, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
  } catch {
    r = null;
  }
  if (!r) { // the PC did not answer: kept on this phone, «sin respuesta»
    enviando = false;
    if (vista !== propia) return;
    if (texto === null) { pintarChat(); aviso("Sin conexión con el PC: pide la opinión cuando vuelva.", "error"); return; }
    guardarLocal(texto);
    pintarChat();
    aviso("Sin conexión con el PC: tu mensaje se queda aquí; pulsa «Reintentar» cuando vuelva.", "error");
    return;
  }
  if (!r.ok) { // not sent (the cap, an empty message…): the text goes back to the box
    const datos = await r.json().catch(() => ({}));
    enviando = false;
    pregunta?.remove();
    respuesta.remove();
    aviso(datos.error ?? "No se ha podido enviar.", "error");
    if (texto !== null && !$("texto").value) $("texto").value = texto;
    if (vista === propia) pintarChat();
    return;
  }
  let dicho = "";
  let final = null;
  try {
    for await (const ev of lineas(r)) {
      if (ev.tipo === "inicio") {
        if (propia.id === null) { // a new conversation: it has its address now
          propia.id = ev.conversacion.id;
          if (vista === propia) history.replaceState(null, "", `#asistente/${propia.id}`);
        }
        propia.conversacion = ev.conversacion;
        if (vista === propia) $("conv-titulo").textContent = ev.conversacion.titulo;
      } else if (ev.tipo === "trozo") {
        dicho += ev.texto;
        escribe.textContent = dicho;
        escribe.classList.remove("escribiendo");
        respuesta.className = "burbuja asistente";
        abajo();
      } else {
        final = ev;
      }
    }
  } catch {
    final = null;
  }
  enviando = false;
  if (vista !== propia) return;
  if (!final) { await abrir(propia.id); return; } // the stream broke: what the PC kept
  propia.conversacion = final.conversacion ?? propia.conversacion;
  if (final.tipo === "fin") {
    propia.mensajes.push(...[final.pregunta, final.mensaje].filter(Boolean));
    propia.linea = final.linea;
  } else {
    if (final.mensaje) propia.mensajes.push(final.mensaje);
    aviso(final.error, "error");
  }
  pintarChat();
  abajo();
}

// -- dictating in a conversation: the text goes to the box, to read it and send it -------------------------------

export async function dictarAlChat(audio, tipo) {
  aviso("Transcribiendo…");
  let r;
  try {
    r = await fetch("/api/conversaciones/dictado", { method: "POST", headers: { "Content-Type": tipo || "audio/mp4" }, body: audio });
  } catch {
    aviso("Sin conexión con el PC: escribe la pregunta o grábala cuando vuelva.", "error");
    return;
  }
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) { aviso(datos.error ?? "No se ha podido transcribir.", "error"); return; }
  aviso("");
  const caja = $("texto");
  caja.value = caja.value ? `${caja.value} ${datos.texto}` : datos.texto;
  caja.dispatchEvent(new Event("input"));
  caja.focus();
}

// -- title and delete (R-118, R-120) ---------------------------------------------------------------------------------

async function renombrar() {
  const titulo = $("conv-titulo-nuevo").value;
  const { ok, datos } = await api(`/api/conversaciones/${vista.id}/titulo`, { titulo });
  if (!ok) { aviso(datos.error ?? "No se ha podido cambiar el título.", "error"); return; }
  vista.conversacion = datos.conversacion;
  $("conv-renombrar-caja").hidden = true;
  $("conv-titulo").textContent = datos.conversacion.titulo;
}

async function borrar() {
  const { ok, datos } = await api(`/api/conversaciones/${vista.id}/borrar`, { confirmado: true });
  if (!ok) { aviso(datos.error ?? "No se ha podido borrar.", "error"); return; }
  const todos = pendientes();
  delete todos[claveDe(vista.id)];
  guardarPendientes(todos);
  vista = null;
  location.hash = "asistente";
}

// -- unit 045: «Adjuntar ruta» and its profile ------------------------------------------------------------------------

const SVG = "http://www.w3.org/2000/svg";
const FUENTES = { garmin: "ruta guardada en Garmin", actividad: "de una actividad hecha", gpx: "GPX subido" };
const km = (x) => `${Number.isInteger(x) ? x : x.toFixed(1).replace(".", ",")} km`;

function svg(etiqueta, atributos = {}) {
  const nodo = document.createElementNS(SVG, etiqueta);
  for (const [k, v] of Object.entries(atributos)) nodo.setAttribute(k, String(v));
  return nodo;
}

function prepararRutas() {
  if ($("conv-ruta-zona")) return;
  const zona = el("div", { id: "conv-ruta-zona", className: "conv-ruta-zona" },
    el("section", { id: "conv-ruta", className: "tarjeta conv-ruta", hidden: true }),
    el("button", { type: "button", id: "conv-adjuntar-ruta", className: "enlace-boton conv-adjuntar", textContent: "📍 Adjuntar ruta" }),
    el("div", { id: "conv-rutas", className: "tarjeta conv-rutas", hidden: true }));
  $("conv-mensajes").before(zona);
  $("conv-adjuntar-ruta").addEventListener("click", alternarRutas);
}

async function alternarRutas() {
  const caja = $("conv-rutas");
  if (!caja.hidden) { caja.hidden = true; return; }
  caja.hidden = false;
  caja.replaceChildren(el("p", { className: "suave", textContent: "Mirando tus rutas…" }));
  const { ok, datos } = await api("/api/rutas");
  pintarRutas(ok ? datos : { garmin: [], actividades: [], aviso: datos.error ?? "No he podido traer tus rutas." });
}

function opcionRuta(titulo, detalle, alElegir) {
  const boton = el("button", { type: "button", className: "opcion-ruta" },
    el("strong", { textContent: titulo }), el("span", { className: "suave", textContent: detalle }));
  boton.addEventListener("click", alElegir);
  return el("li", {}, boton);
}

function pintarRutas(datos, error = "") {
  const caja = $("conv-rutas");
  const fichero = el("input", { type: "file", accept: ".gpx,application/gpx+xml", id: "conv-ruta-gpx" });
  fichero.addEventListener("change", async () => {
    const f = fichero.files?.[0];
    if (f) adjuntar({ gpx: await f.text(), nombre: f.name }, datos);
  });
  const desnivel = (m) => (m ? ` · +${m} m` : "");
  caja.replaceChildren(
    error ? el("p", { className: "aviso", role: "alert", textContent: error }) : null,
    datos.aviso ? el("p", { className: "aviso", textContent: datos.aviso }) : null,
    datos.garmin.length ? el("h3", { className: "titulo-bloque", textContent: "En tu Garmin" }) : null,
    datos.garmin.length ? el("ul", { className: "lista-rutas" }, ...datos.garmin.map((r) =>
      opcionRuta(r.nombre, `${r.distancia_km !== null ? km(r.distancia_km) : ""}${desnivel(r.sube_m)}`,
        () => adjuntar({ garmin: r.id, nombre: r.nombre }, datos)))) : null,
    datos.actividades.length ? el("h3", { className: "titulo-bloque", textContent: "Carreras que ya hiciste" }) : null,
    datos.actividades.length ? el("ul", { className: "lista-rutas" }, ...datos.actividades.map((a) =>
      opcionRuta(a.nombre, `${diaMes.format(new Date(`${a.fecha}T12:00`)).replace(".", "")} · ${km(a.distancia_km)}${desnivel(a.sube_m)}`,
        () => adjuntar({ actividad: a.id }, datos)))) : null,
    el("label", { className: "subir-gpx" }, el("span", { className: "fuerte", textContent: "Subir un GPX" }),
      el("span", { className: "suave", textContent: "El fichero de la ruta (Garmin Connect o Strava, «Exportar GPX»)." }), fichero));
}

async function adjuntar(cuerpo, datos) {
  if (!vista) return;
  const propia = vista;
  const caja = $("conv-rutas");
  caja.querySelectorAll("button, input").forEach((x) => { x.disabled = true; });
  const nueva = propia.id === null;
  const ruta = nueva ? "/api/conversaciones/ruta" : `/api/conversaciones/${propia.id}/ruta`;
  const r = await api(ruta, nueva ? { ...cuerpo, papel: papelNuevo } : cuerpo);
  if (vista !== propia) return;
  if (!r.ok) { pintarRutas(datos, r.datos.error ?? "No he podido adjuntar la ruta."); return; }
  if (nueva) {
    propia.id = r.datos.conversacion.id;
    history.replaceState(null, "", `#asistente/${propia.id}`);
  }
  Object.assign(propia, { conversacion: r.datos.conversacion, mensajes: r.datos.mensajes, linea: r.datos.linea, ruta: r.datos.ruta });
  caja.hidden = true;
  pintarChat();
}

function pintarRuta() {
  const caja = $("conv-ruta");
  if (!caja) return;
  const p = vista?.ruta;
  caja.hidden = !p;
  if (!p) { caja.replaceChildren(); return; }
  caja.replaceChildren(
    el("p", { className: "ruta-titulo" }, el("strong", { textContent: "📍 Ruta adjunta: " }), p.nombre),
    el("p", { className: "suave ruta-datos", textContent: `${FUENTES[p.fuente] ?? p.fuente} · ${km(p.distancia_km)} · +${p.sube_m} m / −${p.baja_m} m` }),
    dibujo(p),
    el("p", { className: "suave ruta-nota", textContent: "Al entrenador va el perfil por km y tramos, nunca las coordenadas." }));
}

// The profile: the altitude every 100 m as an area, the climbs shaded behind it, a mark every 5 km.
function dibujo(p) {
  const ancho = 340, alto = 120, abajo = 18, arriba = 6;
  const alturas = p.altitudes;
  const min = Math.min(...alturas), max = Math.max(...alturas);
  const rango = Math.max(max - min, 20);
  const x = (k) => (k / p.distancia_km) * ancho;
  const y = (m) => alto - abajo - ((m - min) / rango) * (alto - abajo - arriba);
  const paso = p.paso_m / 1000;
  const puntos = alturas.map((m, i) => `${x(Math.min(i * paso, p.distancia_km)).toFixed(1)},${y(m).toFixed(1)}`);
  const dibujo = svg("svg", { viewBox: `0 0 ${ancho} ${alto}`, class: "ruta-perfil", role: "img",
    "aria-label": `Perfil de ${p.nombre}: ${p.tramos.filter((t) => t.terreno === "subida").length} subidas` });
  for (const t of p.tramos.filter((t) => t.terreno === "subida")) {
    dibujo.append(svg("rect", { class: "ruta-subida", x: x(t.desde_km).toFixed(1), y: arriba,
      width: Math.max(1, x(t.hasta_km) - x(t.desde_km)).toFixed(1), height: alto - abajo - arriba }));
  }
  dibujo.append(svg("polygon", { class: "ruta-area", points: [`0,${alto - abajo}`, ...puntos, `${x(p.distancia_km).toFixed(1)},${alto - abajo}`].join(" ") }));
  dibujo.append(svg("polyline", { class: "ruta-linea", points: puntos.join(" ") }));
  dibujo.append(svg("line", { class: "ruta-eje", x1: 0, y1: alto - abajo, x2: ancho, y2: alto - abajo }));
  const cada = p.distancia_km > 30 ? 10 : 5;
  for (let k = 0; k <= p.distancia_km + 0.01; k += cada) {
    const etiqueta = svg("text", { class: "ruta-km", x: Math.min(x(k), ancho - 2).toFixed(1), y: alto - 4,
      "text-anchor": k === 0 ? "start" : x(k) > ancho - 20 ? "end" : "middle" });
    etiqueta.textContent = `${k} km`;
    dibujo.append(etiqueta);
  }
  return dibujo;
}
