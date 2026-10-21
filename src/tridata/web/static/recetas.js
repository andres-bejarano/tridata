// The Recetas tab (unit 011). Plain ES module, no build step. app.js only tells it when the tab is shown.
// Three sections, as in the 010 mockup: Recetas, Taras (the pots and tupperware, with their tare) and
// Alimentos fijos. Tapping a row opens its card to change or delete it; «＋ Añadir …» opens an empty one.
// A recipe's ingredients are written or dictated in its own box and interpreted like a meal
// (/api/interpretar, /api/dictado: the same interpreter, nothing saved), then reviewed here. The server
// does every sum that is saved (fixed foods with THEIR kcal, kcal/100 g); this only shows and sends.
// Without the PC nothing is saved here: it says so (the offline queue is only for the Hoy screen).
// Unit 013: a saved recipe or fixed food opens with «¿Cuánto has cogido?», its kcal at once (worked out
// here with what /api/recetas brought, only to show them) and «Apuntar en hoy», which sends just the
// quantity: the server works the kcal out again with what is saved. A recipe not weighed yet shows its
// kcal of the whole recipe on top and a big «Pesar ahora», which weighs the batch in use whenever.
// Unit 016: kcal are shown whole (calculator, «Apuntado en hoy», the whole recipe); kcal/100 g, kcal of a fixed
// food and every input keep their decimal (Q-9, C-65).

import { fechaLocal } from "./cola.js";
import { grabar, puedeGrabar } from "./dictado.js";

const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, useGrouping: "always" });
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const kcalEnteras = (n) => enteras.format(Math.round(n) === 0 ? 0 : n); // unit 016 (R8): only shown
const fechaCorta = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", year: "numeric" });
const SIN_PC = "No hay conexión con el PC: no se ha guardado nada.";

let seccion = null;
let datos = { recetas: [], fijos: [], ollas: [] };
let abierta = null; // { tipo: "receta" | "fijo" | "olla", id: number | null } — one card at a time
let turno = 0;

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined && hijo !== false) nodo.append(hijo);
  return nodo;
}

const $ = (sel) => seccion.querySelector(sel);
const plural = (n, uno, varios) => `${numero.format(n)} ${n === 1 ? uno : varios}`;
const comoNumero = (v) => (String(v ?? "").trim() === "" ? null : Number(String(v).replace(",", ".")));
const deFecha = (iso) => fechaCorta.format(new Date(`${iso}T12:00:00`));

async function api(ruta, cuerpo) {
  const opciones = cuerpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo),
  };
  try {
    const r = await fetch(ruta, opciones);
    const respuesta = await r.json().catch(() => ({}));
    return { ok: r.ok, estado: r.status, datos: respuesta };
  } catch {
    return { ok: false, estado: 0, datos: { error: SIN_PC } };
  }
}

function mensaje(texto, tipo = "") {
  const m = $("#recetas-mensaje");
  m.textContent = texto;
  m.className = tipo;
}

function campo(etiqueta, valor, tipo = "text", extra = {}) {
  const entrada = tipo === "textarea"
    ? el("textarea", { rows: 3, value: valor ?? "", ...extra })
    : el("input", { type: tipo, value: valor ?? "", ...extra });
  const texto = el("span", { textContent: etiqueta });
  const etiquetado = el("label", { className: `campo ${tipo}` }, texto, entrada);
  etiquetado.entrada = entrada;
  etiquetado.texto = texto;
  return etiquetado;
}

let selecciones = 0;

// A <label for> (not wrapping): a wrapping label would give the <select> its options' text as its name.
function seleccion(etiqueta, opciones, valor) {
  const id = `receta-sel-${++selecciones}`;
  const entrada = el("select", { id }, ...opciones.map(([v, t]) => el("option", { value: v, textContent: t })));
  entrada.value = valor ?? "";
  const etiquetado = el("div", { className: "campo select" }, el("label", { htmlFor: id, textContent: etiqueta }), entrada);
  etiquetado.entrada = entrada;
  return etiquetado;
}

const numerico = { inputMode: "decimal", autocomplete: "off" };

// -- drawing the tab ------------------------------------------------------------------------

export async function pintarRecetas(contenedor) {
  seccion = contenedor;
  const mio = ++turno;
  const { ok, datos: respuesta } = await api("/api/recetas");
  if (mio !== turno) return;
  if (!ok) { mensaje(respuesta.error ?? "No he podido cargar tus recetas.", "error"); return; }
  datos = respuesta;
  pintar();
}

function poner(respuesta) {
  datos = { recetas: respuesta.recetas, fijos: respuesta.fijos, ollas: respuesta.ollas };
}

function pintar() {
  pintarApartado("recetas", datos.recetas, filaReceta, "receta", "Aún no hay recetas.", "＋ Añadir receta");
  pintarApartado("taras", datos.ollas, filaOlla, "olla", "Aún no hay taras.", "＋ Añadir tara");
  pintarApartado("fijos", datos.fijos, filaFijo, "fijo", "Aún no hay alimentos fijos.", "＋ Añadir alimento fijo");
}

function pintarApartado(cual, lista, fila, tipo, vacio, textoAnadir) {
  const nodos = lista.map((cosa) => (abierta && abierta.tipo === tipo && abierta.id === cosa.id ? ficha(tipo, cosa) : fila(cosa)));
  const nueva = abierta && abierta.tipo === tipo && abierta.id === null;
  if (!lista.length && !nueva) nodos.push(el("p", { className: "suave vacio-apartado", textContent: vacio }));
  if (nueva) nodos.push(ficha(tipo, null));
  else {
    const anadir = el("button", { type: "button", className: "anadir", textContent: textoAnadir });
    anadir.addEventListener("click", () => abrir(tipo, null));
    nodos.push(anadir);
  }
  $(`#apartado-${cual} .elementos`).replaceChildren(...nodos);
}

function abrir(tipo, id) {
  abierta = { tipo, id };
  mensaje("");
  pintar();
  const primero = seccion.querySelector(".ficha input, .ficha textarea");
  if (primero && id === null) primero.focus();
}

function cerrar(texto = "") {
  abierta = null;
  pintar();
  if (texto) mensaje(texto, "bien");
}

function elemento(nombre, detalle, valor, alAbrir) {
  const boton = el("button", { type: "button", className: "elemento", title: "Toca para cambiarlo" },
    el("span", { className: "que" }, el("strong", { textContent: nombre }), el("span", { textContent: detalle })),
    valor ? el("span", { className: "valor num", textContent: valor }) : null,
    el("span", { className: "chev", textContent: "›", ariaHidden: "true" }));
  boton.addEventListener("click", alAbrir);
  return boton;
}

function textoEstado(v) {
  if (!v) return "";
  if (v.estado === "definitiva") return `${numero.format(v.kcal_100g)} kcal/100 g · definitiva`;
  if (v.estado === "provisional") {
    const aprox = v.kcal_100g_aprox !== null ? ` · ~${numero.format(v.kcal_100g_aprox)} kcal/100 g` : "";
    return `provisional · ${kcalEnteras(v.kcal_total)} kcal la receta entera${aprox}`;
  }
  return "sin kcal/100 g: corrige la receta";
}

function filaReceta(r) {
  const partes = r.por_revisar ? ["por revisar: ábrela y guárdala"] : [textoEstado(r.en_uso)];
  if (r.versiones.length > 1) partes.push(plural(r.versiones.length, "tanda", "tandas"));
  if (r.en_uso && r.en_uso.estimados) partes.push(plural(r.en_uso.estimados, "estimado", "estimados"));
  const fila = elemento(r.nombre, partes.join(" · "), "", () => abrir("receta", r.id));
  if (r.por_revisar) fila.classList.add("revisar");
  return fila;
}

const porTexto = (por) => (por === "100g" ? "100 g" : "unidad");

function filaFijo(f) {
  const partes = [`${numero.format(f.kcal)} kcal/${porTexto(f.por)}`];
  if (f.alias.length) partes.push(`«${f.alias.join("», «")}»`);
  if (f.nota) partes.push(f.nota);
  return elemento(f.nombre, partes.join(" · "), "", () => abrir("fijo", f.id));
}

function filaOlla(o) {
  return elemento(o.nombre, "tara (vacía)", `${numero.format(o.tara_g)} g`, () => abrir("olla", o.id));
}

// -- cards ------------------------------------------------------------------------------------

function ficha(tipo, cosa) {
  if (tipo === "fijo") return fichaFijo(cosa);
  if (tipo === "olla") return fichaOlla(cosa);
  return fichaReceta(cosa);
}

// The parts every card shares: a notice, Guardar / Cancelar and, for a saved thing, Borrar asking first.
function armazon({ clase, nodos, textoGuardar, guardar, borrar, nombre, aviso = el("p", { className: "aviso", hidden: true }) }) {
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const boton = el("button", { type: "button", className: "principal", textContent: textoGuardar });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  cancelar.addEventListener("click", () => cerrar());
  boton.addEventListener("click", async () => {
    boton.disabled = true;
    avisar("");
    const error = await guardar(avisar);
    boton.disabled = false;
    if (error) avisar(error);
  });
  const acciones = el("div", { className: "acciones" }, boton, cancelar);
  const tarjeta = el("article", { className: `tarjeta nueva ficha ${clase}` }, ...nodos, aviso, acciones);
  if (borrar) tarjeta.append(...preguntaBorrar(nombre, borrar, acciones, avisar));
  return { tarjeta, acciones, avisar };
}

function preguntaBorrar(nombre, borrar, acciones, avisar, textoBoton = "Borrar", detalle = "") {
  const boton = el("button", { type: "button", className: "peligro", textContent: textoBoton });
  const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
    el("p", { textContent: `¿Borrar ${nombre}?${detalle}` }));
  const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, borrar" });
  const no = el("button", { type: "button", textContent: "No" });
  pregunta.append(el("div", { className: "acciones" }, si, no));
  const preguntar = (abiertaPregunta) => { pregunta.hidden = !abiertaPregunta; acciones.hidden = abiertaPregunta; boton.hidden = abiertaPregunta; };
  boton.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
  no.addEventListener("click", () => preguntar(false)); // nothing is sent: nothing is deleted
  si.addEventListener("click", async () => {
    si.disabled = true;
    const error = await borrar();
    si.disabled = false;
    if (error) { preguntar(false); avisar(error); }
  });
  return [boton, pregunta];
}

async function enviar(ruta, cuerpo, texto) {
  const { ok, datos: r } = await api(ruta, cuerpo);
  if (!ok) return r.error ?? "No se ha podido guardar.";
  poner(r);
  cerrar(texto);
  return "";
}

const alias = (texto) => texto.split(",").map((a) => a.trim()).filter(Boolean);

function fichaFijo(f) {
  const nombre = campo("Nombre", f ? f.nombre : "");
  const kcal = campo("kcal", f ? numero.format(f.kcal).replace(/\./g, "") : "", "text", numerico);
  const por = seleccion("Por", [["100g", "por 100 g"], ["unidad", "por unidad"]], f ? f.por : "100g");
  const alias_ = campo("Alias (separa con comas)", f ? f.alias.join(", ") : "");
  const nota = campo("Nota", f ? f.nota : "", "text", { placeholder: "p. ej. «no la fruta»" });
  const ruta = f ? `/api/fijos/${f.id}` : "/api/fijos";
  const { tarjeta } = armazon({
    clase: "ficha-fijo", nombre: f && f.nombre, textoGuardar: "Guardar",
    nodos: [nombre, el("div", { className: "fila dos" }, kcal, por), alias_, nota],
    guardar: () => {
      const valor = comoNumero(kcal.entrada.value);
      if (!nombre.entrada.value.trim()) return "Ponle nombre.";
      if (valor === null || Number.isNaN(valor)) return "Pon sus kcal.";
      return enviar(ruta, { nombre: nombre.entrada.value, kcal: valor, por: por.entrada.value,
        alias: alias(alias_.entrada.value), nota: nota.entrada.value }, `Guardado: ${nombre.entrada.value.trim()}.`);
    },
    borrar: f && (() => enviar(`/api/fijos/${f.id}/borrar`, { confirmado: true }, `Borrado: ${f.nombre}.`)),
  });
  if (f) tarjeta.prepend(calculadora("fijo", f));
  return tarjeta;
}

function fichaOlla(o) {
  const nombre = campo("Nombre", o ? o.nombre : "", "text", { placeholder: "p. ej. «olla grande»" });
  const tara = campo("Tara (g)", o ? String(o.tara_g) : "", "text", { ...numerico, placeholder: "lo que pesa vacía" });
  const ruta = o ? `/api/ollas/${o.id}` : "/api/ollas";
  return armazon({
    clase: "ficha-olla", nombre: o && o.nombre, textoGuardar: "Guardar",
    nodos: [nombre, tara],
    guardar: () => {
      const valor = comoNumero(tara.entrada.value);
      if (!nombre.entrada.value.trim()) return "Ponle nombre.";
      if (valor === null || Number.isNaN(valor)) return "Pon lo que pesa vacía, en gramos.";
      return enviar(ruta, { nombre: nombre.entrada.value, tara_g: valor }, `Guardada: ${nombre.entrada.value.trim()}.`);
    },
    borrar: o && (() => enviar(`/api/ollas/${o.id}/borrar`, { confirmado: true }, `Borrada: ${o.nombre}.`)),
  }).tarjeta;
}

// -- the recipe card ------------------------------------------------------------------------------

const MARCAS = { guardado: "alimento fijo", estimado: "estimado", dudoso: "dudoso: corrígelo o quítalo" };

// An interpreted food as an ingredient: doubtful, a fixed food (the server recomputes it) or estimated.
function comoIngrediente(a) {
  let estado = "estimado";
  if (a.dudoso || a.kcal === null) estado = "dudoso";
  else if (a.origen && a.origen.startsWith("de tu alimento fijo")) estado = "guardado";
  return { nombre: a.nombre, cantidad: a.cantidad, unidad: a.unidad, kcal: estado === "dudoso" ? null : a.kcal, estado };
}

function fichaReceta(r) {
  const v = r ? r.en_uso : null;
  const modelo = { ingredientes: v ? v.ingredientes.map((i) => ({ ...i })) : [], teniaIngredientes: Boolean(v && v.ingredientes.length) };
  const nombre = campo("Nombre", r ? r.nombre : "", "text", { placeholder: "p. ej. «garbanzos de la abuela»" });
  const alias_ = campo("Alias (separa con comas)", r ? r.alias.join(", ") : "");
  const nota = campo("Nota", r ? r.nota : "");
  const caja = campo("Ingredientes (escríbelos o díctalos)", "", "textarea",
    { placeholder: "garbanzo seco 600 g, aceite 50 g, cebolla 125 g" });
  const interpretar = el("button", { type: "button", className: "principal", textContent: "Interpretar ingredientes" });
  const dictar = el("button", { type: "button", className: "dictar", textContent: "🎤 Dictar" });
  dictar.setAttribute("aria-label", "Dictar lo que lleva");
  const lista = el("div", { className: "ingredientes" });
  const total = el("p", { className: "total-receta" });
  const ollas = [["", "Sin olla: escribo el peso neto"], ...datos.ollas.map((o) => [String(o.id), `${o.nombre} (${numero.format(o.tara_g)} g)`])];
  const olla = seleccion("Olla", ollas, v && v.olla_id && datos.ollas.some((o) => o.id === v.olla_id) ? String(v.olla_id) : "");
  const peso = campo("Olla llena (g)", "", "text", numerico);
  if (v) peso.entrada.value = v.peso_lleno_g !== null && olla.entrada.value ? String(v.peso_lleno_g) : (v.peso_neto_g !== null ? String(v.peso_neto_g) : "");
  if (v && v.peso_lleno_g === null && v.peso_neto_g !== null) olla.entrada.value = "";
  const aprox = campo("kcal/100 g aproximadas (si no la pesas)", v && v.kcal_100g_aprox !== null ? String(v.kcal_100g_aprox) : "", "text", numerico);
  const calculo = el("p", { className: "suave calculo" });
  const aviso = el("p", { className: "aviso", hidden: true });

  const sumar = () => modelo.ingredientes.reduce((s, i) => s + (i.estado !== "dudoso" && i.kcal !== null && !Number.isNaN(i.kcal) ? i.kcal : 0), 0);
  function pintarTotal() {
    const estimados = modelo.ingredientes.filter((i) => i.estado === "estimado").length;
    const dudosos = modelo.ingredientes.filter((i) => i.estado === "dudoso").length;
    const partes = [`Total: ${kcalEnteras(sumar())} kcal la receta entera`];
    if (estimados) partes.push(plural(estimados, "ingrediente estimado", "ingredientes estimados"));
    if (dudosos) partes.push(`${plural(dudosos, "dudoso", "dudosos")}: sin kcal/100 g hasta corregirlo`);
    total.textContent = modelo.ingredientes.length || !v ? partes.join(" · ") : `Total: ${kcalEnteras(v.kcal_total ?? 0)} kcal la receta entera (de deficit.md)`;
    total.hidden = !modelo.ingredientes.length && !v;
    pintarCalculo();
  }
  function pintarCalculo() {
    const conOlla = Boolean(olla.entrada.value);
    peso.texto.textContent = conOlla ? "Olla llena (g)" : "Peso neto (g)";
    const g = comoNumero(peso.entrada.value);
    const tara = conOlla ? datos.ollas.find((o) => String(o.id) === olla.entrada.value).tara_g : 0;
    const neto = g === null || Number.isNaN(g) ? null : g - tara;
    const kcal = modelo.ingredientes.length ? sumar() : (v ? v.kcal_total : null);
    if (neto === null || !kcal) calculo.textContent = "Sin pesar queda provisional: se apunta por fracciones («un cuarto de …») o con las kcal/100 g aproximadas.";
    else if (neto <= 0) calculo.textContent = "La olla llena no puede pesar menos que su tara.";
    else calculo.textContent = `Saldrá a unas ${numero.format(Math.round(kcal / neto * 1000) / 10)} kcal/100 g (peso neto ${numero.format(neto)} g).`;
  }
  function pintarIngredientes() {
    lista.replaceChildren(...modelo.ingredientes.map((i, n) => {
      const nombreI = campo("Ingrediente", i.nombre);
      const cant = campo("Cantidad", i.cantidad, "text", numerico);
      const unidad = campo("Unidad", i.unidad);
      const kcal = campo("kcal", i.kcal, "text", numerico);
      const leer = () => {
        const nuevo = comoNumero(kcal.entrada.value);
        if (nuevo !== i.kcal && nuevo !== null && !Number.isNaN(nuevo) && i.estado === "dudoso") i.estado = "estimado";
        if (nombreI.entrada.value.trim() !== i.nombre && i.estado === "dudoso" && nuevo !== null) i.estado = "estimado";
        Object.assign(i, { nombre: nombreI.entrada.value.trim(), cantidad: comoNumero(cant.entrada.value), unidad: unidad.entrada.value.trim() || null, kcal: nuevo });
        pintarTotal();
      };
      for (const c of [nombreI, cant, unidad, kcal]) c.entrada.addEventListener("input", leer);
      kcal.entrada.addEventListener("change", pintarIngredientes);
      const quitar = el("button", { type: "button", className: "quitar", textContent: "Quitar" });
      quitar.addEventListener("click", () => { modelo.ingredientes.splice(n, 1); pintarIngredientes(); });
      const marca = el("span", { className: `marca${i.estado === "dudoso" ? " alerta" : ""}`, textContent: MARCAS[i.estado] });
      return el("div", { className: `ingrediente${i.estado === "dudoso" ? " dudosa" : ""}` },
        el("div", { className: "marcas" }, marca), nombreI, el("div", { className: "fila" }, cant, unidad, kcal), quitar);
    }));
    pintarTotal();
  }
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };

  async function anadirInterpretados(respuesta) {
    const nuevos = (respuesta.alimentos || []).map(comoIngrediente);
    if (!nuevos.length) { avisar("No he encontrado ingredientes en ese texto."); return; }
    modelo.ingredientes.push(...nuevos);
    caja.entrada.value = "";
    pintarIngredientes();
  }
  interpretar.addEventListener("click", async () => {
    const texto = caja.entrada.value.trim();
    if (!texto) { avisar("Escribe o dicta los ingredientes con sus gramos."); return; }
    interpretar.disabled = true;
    avisar("");
    const { ok, datos: respuesta } = await api("/api/interpretar", { texto });
    interpretar.disabled = false;
    if (!ok) { avisar(respuesta.error ?? "No he podido interpretar los ingredientes."); return; }
    anadirInterpretados(respuesta);
  });
  let grabacion = null;
  dictar.addEventListener("click", async () => {
    if (grabacion) { grabacion.parar(); return; }
    if (!puedeGrabar()) { avisar("Este navegador no puede grabar audio: escríbelos."); return; }
    try {
      grabacion = await grabar({
        alSegundo: (s) => { dictar.textContent = `■ Parar (${s} s)`; },
        alTerminar: async ({ audio, tipo }) => {
          grabacion = null;
          dictar.textContent = "Enviando…";
          const id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
          let r = null;
          try {
            r = await fetch(`/api/dictado?id=${id}&fecha=${fechaLocal()}`, { method: "POST", headers: { "Content-Type": tipo }, body: audio });
          } catch { /* the PC is off */ }
          dictar.textContent = "🎤 Dictar";
          if (!r) { avisar(SIN_PC); return; }
          const respuesta = await r.json().catch(() => ({}));
          if (!r.ok) { if (respuesta.texto) caja.entrada.value = respuesta.texto; avisar(respuesta.error ?? "No se ha podido dictar."); return; }
          caja.entrada.value = respuesta.texto;
          anadirInterpretados(respuesta.interpretacion);
        },
      });
    } catch {
      grabacion = null;
      avisar("No se ha podido usar el micrófono: escríbelos.");
    }
  });
  olla.entrada.addEventListener("change", pintarCalculo);
  peso.entrada.addEventListener("input", pintarCalculo);

  // What is sent: the server recomputes the fixed foods and the kcal/100 g.
  function cuerpo() {
    const c = { nombre: nombre.entrada.value, alias: alias(alias_.entrada.value), nota: nota.entrada.value,
      kcal_100g_aprox: comoNumero(aprox.entrada.value), olla_id: olla.entrada.value ? Number(olla.entrada.value) : null };
    const g = comoNumero(peso.entrada.value);
    if (olla.entrada.value) c.peso_lleno_g = g; else c.peso_neto_g = g;
    if (modelo.ingredientes.length || modelo.teniaIngredientes || !r) {
      c.ingredientes = modelo.ingredientes.map((i) => ({ nombre: i.nombre, cantidad: i.cantidad, unidad: i.unidad,
        kcal: i.kcal, dudoso: i.estado === "dudoso", guardado: i.estado === "guardado" ? i.nombre : null }));
    }
    return c;
  }
  function falta(c) {
    if (!c.nombre.trim()) return "Ponle nombre a la receta.";
    if ([c.peso_lleno_g, c.peso_neto_g, c.kcal_100g_aprox].some((n) => Number.isNaN(n))) return "Revisa los números del peso o de las kcal/100 g.";
    if (c.ingredientes && c.ingredientes.some((i) => !i.nombre || Number.isNaN(i.cantidad) || Number.isNaN(i.kcal))) return "Revisa los ingredientes: hay uno sin nombre o con un número que no vale.";
    return "";
  }
  async function guardarComo(ruta, extra, texto) {
    const c = { ...cuerpo(), ...extra };
    const motivo = falta(c);
    if (motivo) return motivo;
    const { ok, estado, datos: respuesta } = await api(ruta, c);
    if (ok) { poner(respuesta); cerrar(texto); return ""; }
    if (estado === 409 && respuesta.otra_tanda) { // the name is a recipe already: offer another batch (R12)
      const tanda = el("button", { type: "button", textContent: "Guardar como otra tanda" });
      tanda.addEventListener("click", async () => { avisar(await guardarComo("/api/recetas", { otra_tanda: true }, `Guardada otra tanda de ${c.nombre.trim()}.`)); });
      avisar(respuesta.error);
      aviso.append(" ", tanda);
      return "";
    }
    return respuesta.error ?? "No se ha podido guardar.";
  }

  const nodos = [nombre, caja, el("div", { className: "acciones" }, interpretar, dictar), lista, total,
    el("div", { className: "fila dos" }, olla, peso), aprox, calculo, alias_, nota];
  const { tarjeta, acciones } = armazon({
    clase: "ficha-receta", aviso, textoGuardar: r ? "Guardar cambios" : "Guardar receta", nodos,
    guardar: () => (r ? guardarComo(`/api/recetas/${r.id}`, {}, `Guardada: ${r.nombre}.`)
      : guardarComo("/api/recetas", {}, `Guardada: ${nombre.entrada.value.trim()}.`)),
  });
  if (r) {
    const tanda = el("button", { type: "button", className: "ancho", textContent: "Guardar como otra tanda" });
    tanda.addEventListener("click", async () => {
      tanda.disabled = true;
      avisar(await guardarComo("/api/recetas", { nombre: r.nombre, otra_tanda: true }, `Guardada otra tanda de ${r.nombre}.`));
      tanda.disabled = false;
    });
    acciones.after(tanda);
    tarjeta.append(versiones(r, avisar));
    const detalle = r.versiones.length > 1 ? ` Se borran sus ${r.versiones.length} tandas; lo ya apuntado no cambia.` : " Lo ya apuntado no cambia.";
    tarjeta.append(...preguntaBorrar(r.nombre, () => enviar(`/api/recetas/${r.id}/borrar`, { confirmado: true }, `Borrada: ${r.nombre}.`),
      acciones, avisar, "Borrar receta", detalle));
  }
  if (r) {
    tarjeta.prepend(calculadora("receta", r));
    if (sinPesar(r)) tarjeta.prepend(pesarAhora(r));
  }
  pintarIngredientes();
  return tarjeta;
}

function versiones(r, avisar) {
  const filas = r.versiones.slice().reverse().map((v) => {
    const fila = el("div", { className: "version" },
      el("span", { className: "que" }, el("strong", { textContent: `Tanda ${v.numero}` }),
        el("span", { textContent: `${deFecha(v.fecha)} · ${textoEstado(v)}${v.olla ? ` · ${v.olla}` : ""}` })));
    if (v.en_uso) fila.append(el("span", { className: "marca", textContent: "en uso" }));
    else {
      const usar = el("button", { type: "button", className: "cambiar", textContent: "Usar esta" });
      usar.addEventListener("click", async () => {
        usar.disabled = true;
        const { ok, datos: respuesta } = await api(`/api/recetas/${r.id}/version`, { numero: v.numero });
        usar.disabled = false;
        if (!ok) { avisar(respuesta.error ?? "No se ha podido cambiar de tanda."); return; }
        poner(respuesta);
        cerrar(`Desde ahora se usa la tanda ${v.numero} de ${r.nombre}.`);
      });
      fila.append(usar);
    }
    return fila;
  });
  return el("div", { className: "versiones" }, el("p", { className: "titulo-pieza", textContent: "Tandas" }), ...filas);
}

// -- unit 013: how much was taken, logged today, and weighing later ---------------------------------

const redondear = (n) => Math.round(n * 10) / 10;
const sinPesar = (r) => !r.por_revisar && r.en_uso && r.en_uso.estado === "provisional";

// The kcal of `n` of a saved thing, only to show them (G-31, G-32): the server works them out again.
function cuenta(tipo, cosa, n) {
  if (tipo === "fijo") {
    return { kcal: redondear(cosa.por === "unidad" ? n * cosa.kcal : n * cosa.kcal / 100), provisional: false };
  }
  const v = cosa.en_uso;
  if (!v || v.estado === "sin_kcal") return { problema: "Corrige la receta: tiene un ingrediente dudoso y no se puede calcular." };
  if (v.kcal_100g !== null) return { kcal: redondear(n * v.kcal_100g / 100), provisional: false };
  if (v.kcal_100g_aprox !== null) return { kcal: redondear(n * v.kcal_100g_aprox / 100), provisional: true };
  return { problema: "Sin pesar: pésala o pon sus kcal/100 g aproximadas." };
}

function calculadora(tipo, cosa) {
  const porUnidad = tipo === "fijo" && cosa.por === "unidad";
  const cuanto = campo(`¿Cuánto has cogido? (${porUnidad ? "unidades" : "g"})`, "", "text", numerico);
  const resultado = el("p", { className: "resultado num", textContent: "— kcal" });
  resultado.setAttribute("aria-live", "polite");
  const aviso = el("p", { className: "aviso-calc", hidden: true });
  const apuntar = el("button", { type: "button", className: "principal", textContent: "Apuntar en hoy" });
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const leer = () => {
    const n = comoNumero(cuanto.entrada.value);
    if (n === null || Number.isNaN(n) || n <= 0) return { n, problema: "Escribe cuánto has cogido.", vacio: true };
    return { n, ...cuenta(tipo, cosa, n) };
  };
  const pintarResultado = () => {
    const c = leer();
    resultado.textContent = c.problema ? "— kcal" : `${kcalEnteras(c.kcal)} kcal${c.provisional ? " · provisional" : ""}`;
    avisar(c.problema && !c.vacio ? c.problema : "");
  };
  cuanto.entrada.addEventListener("input", pintarResultado);
  apuntar.addEventListener("click", async () => {
    const c = leer();
    if (c.problema) { avisar(c.problema); return; }
    apuntar.disabled = true;
    avisar("");
    const ruta = `/api/${tipo === "fijo" ? "fijos" : "recetas"}/${cosa.id}/apuntar`;
    const { ok, datos: r } = await api(ruta, { cantidad: c.n });
    apuntar.disabled = false;
    if (!ok) { avisar(r.error ?? "No se ha podido apuntar: no se ha guardado nada."); return; }
    const a = r.alimento;
    const unidad = a.unidad === "unidad" ? (a.cantidad === 1 ? "unidad" : "unidades") : a.unidad;
    cerrar(`Apuntado en hoy: ${a.nombre}, ${numero.format(a.cantidad)} ${unidad}, ${kcalEnteras(a.kcal)} kcal${a.provisional ? " (provisional)" : ""}.`);
  });
  const caja = el("div", { className: "calculadora" });
  if (tipo === "receta" && cosa.en_uso && cosa.en_uso.estado === "definitiva") {
    caja.append(el("p", { className: "estado-receta num", textContent: textoEstado(cosa.en_uso) }));
  }
  caja.append(el("div", { className: "fila-calc" }, cuanto, resultado), aviso, apuntar);
  return caja;
}

// «Pesar ahora» (R-59): the pot and the full pot, or the net weight, sent ALONE to the route that corrects
// the batch in use, so its ingredients, its date and the rest stay as they are.
function pesarAhora(r) {
  const v = r.en_uso;
  const boton = el("button", { type: "button", className: "principal grande", textContent: "Pesar ahora" });
  const ollas = [["", "Sin olla: escribo el peso neto"], ...datos.ollas.map((o) => [String(o.id), `${o.nombre} (${numero.format(o.tara_g)} g)`])];
  const olla = seleccion("Olla", ollas, datos.ollas.length ? String(datos.ollas[0].id) : "");
  const peso = campo("Olla llena (g)", "", "text", numerico);
  const calculo = el("p", { className: "suave calculo" });
  const aviso = el("p", { className: "aviso-calc", hidden: true });
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar el peso" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const panel = el("div", { className: "pesar", hidden: true }, el("div", { className: "fila dos" }, olla, peso), calculo, aviso,
    el("div", { className: "acciones" }, guardar, cancelar));
  const pintarCalculo = () => {
    const conOlla = Boolean(olla.entrada.value);
    peso.texto.textContent = conOlla ? "Olla llena (g)" : "Peso neto (g)";
    const g = comoNumero(peso.entrada.value);
    const tara = conOlla ? datos.ollas.find((o) => String(o.id) === olla.entrada.value).tara_g : 0;
    const neto = g === null || Number.isNaN(g) ? null : g - tara;
    if (neto === null) calculo.textContent = "";
    else if (neto <= 0) calculo.textContent = "La olla llena no puede pesar menos que su tara.";
    else calculo.textContent = `Saldrá a unas ${numero.format(Math.round(v.kcal_total / neto * 1000) / 10)} kcal/100 g (peso neto ${numero.format(neto)} g).`;
  };
  olla.entrada.addEventListener("change", pintarCalculo);
  peso.entrada.addEventListener("input", pintarCalculo);
  pintarCalculo();
  boton.addEventListener("click", () => { panel.hidden = false; boton.hidden = true; avisar(""); peso.entrada.focus(); });
  cancelar.addEventListener("click", () => { panel.hidden = true; boton.hidden = false; avisar(""); });
  guardar.addEventListener("click", async () => {
    const g = comoNumero(peso.entrada.value);
    if (g === null || Number.isNaN(g)) { avisar(olla.entrada.value ? "Escribe lo que pesa la olla llena, en gramos." : "Escribe el peso neto, en gramos."); return; }
    const cuerpo = olla.entrada.value ? { olla_id: Number(olla.entrada.value), peso_lleno_g: g } : { olla_id: null, peso_neto_g: g };
    guardar.disabled = true;
    avisar("");
    const { ok, datos: respuesta } = await api(`/api/recetas/${r.id}`, cuerpo);
    guardar.disabled = false;
    if (!ok) { avisar(respuesta.error ?? "No se ha podido guardar el peso."); return; }
    poner(respuesta);
    pintar(); // the card stays open, now weighed
    const ahora = respuesta.receta.en_uso;
    mensaje(`Pesada: ${r.nombre}, ${textoEstado(ahora)}.`, "bien");
  });
  return el("div", { className: "pesar-ahora" },
    el("p", { className: "entera num", textContent: `${kcalEnteras(v.kcal_total)} kcal la receta entera · sin pesar` }),
    boton, panel);
}
