// The Asistente's conversations (unit 032, REC-20 of `preguntar-equipo`). Plain ES module, no build step.
// `#asistente` is the list (newest first, «Nueva conversación», what it knows of the medical documents);
// `#asistente/N` one conversation and `#asistente/nueva` one that starts. In a conversation the box at the bottom
// (app.js's #entrada) is the chat's (S-37): what is written or dictated goes to `enviarAlChat`. Every conversation
// sees all the context of the PC; the line above says what and until when (R-115). The answer is painted as it
// arrives (POST …/mensajes answers one JSON per line: inicio, trozo…, fin | error). Without an answer the message
// stays «sin respuesta» with «Reintentar» (R-117): on the PC when the API failed, on this phone (localStorage) when
// the PC did not answer. A change the answer proposes is the card of the 021 (asistente.js), confirmed with
// /api/asistente/*; the cards still waiting are written back to the answer. Every text is painted as text.

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
  $("pensar").addEventListener("click", () => {
    const boton = $("pensar");
    boton.setAttribute("aria-pressed", String(boton.getAttribute("aria-pressed") !== "true"));
  });
}

// Called by app.js every time #asistente… shows.
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
  const filas = datos.conversaciones.map((c) => {
    const boton = el("button", { type: "button", className: "conversacion" },
      el("span", { className: "conv-cab" }, el("strong", { textContent: c.titulo }), el("span", { className: "suave", textContent: cuando(c.usada) })),
      el("span", { className: "suave conv-ultima", textContent: c.ultima }));
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
  vista = { id: null, conversacion: { titulo: "Nueva conversación" }, mensajes: [] };
  pintarChat();
  $("texto").focus();
  const mia = ++peticion;
  const { ok, datos } = await api("/api/conversaciones");
  if (ok && mia === peticion) $("conv-linea").textContent = datos.linea ?? "";
}

async function abrir(id) {
  const mia = ++peticion;
  $("conv-mensajes").replaceChildren(el("p", { className: "suave", textContent: "Cargando…" }));
  const { ok, estado, datos } = await api(`/api/conversaciones/${id}`);
  if (mia !== peticion) return;
  if (!ok) {
    if (estado === 404) { location.hash = "asistente"; return; }
    aviso(datos.error ?? "No he podido traer la conversación.", "error");
    $("conv-mensajes").replaceChildren();
    return;
  }
  vista = { id, conversacion: datos.conversacion, mensajes: datos.mensajes, linea: datos.linea };
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
}

function burbuja(m) {
  const nodo = el("div", { className: `burbuja ${m.quien}` });
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
  conversar(ruta, { texto, pensar }, texto);
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
  await conversar(ruta, { texto: m.texto }, m.texto);
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

async function conversar(ruta, cuerpo, texto) {
  enviando = true;
  aviso("");
  const propia = vista;
  const pregunta = el("div", { className: "burbuja andres" }, el("p", { className: "texto-burbuja", textContent: texto }));
  const escribe = el("p", { className: "texto-burbuja escribiendo", textContent: "…" });
  // «…» while it thinks; it becomes a message (`.burbuja`) with its first words
  const respuesta = el("div", { className: "burbuja-espera", ariaLabel: "El asistente está escribiendo" }, escribe);
  $("conv-mensajes").append(pregunta, respuesta);
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
    guardarLocal(texto);
    pintarChat();
    aviso("Sin conexión con el PC: tu mensaje se queda aquí; pulsa «Reintentar» cuando vuelva.", "error");
    return;
  }
  if (!r.ok) { // not sent (the cap, an empty message…): the text goes back to the box
    const datos = await r.json().catch(() => ({}));
    enviando = false;
    pregunta.remove();
    respuesta.remove();
    aviso(datos.error ?? "No se ha podido enviar.", "error");
    if (!$("texto").value) $("texto").value = texto;
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
    propia.mensajes.push(final.pregunta, final.mensaje);
    propia.linea = final.linea;
  } else {
    propia.mensajes.push(final.mensaje);
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
