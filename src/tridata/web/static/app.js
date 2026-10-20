// Day page of the food log. Plain ES module, no build step.
// Nothing is saved until "Confirmar y guardar"; the server computes every total.
// A saved food is changed, moved, deleted or marked eaten by tapping its row in the day table.
// Every card reads its inputs when "Guardar" / "Confirmar" is pressed, not on `change`.

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const NIVELES = { "-10": "−10 %", "0": "Mantenimiento", "10": "+10 %" };

let revision = null; // { texto, hoy, original, alimentos }
let hoyISO = null; // today, as the server says on the first load
let diaVisto = null; // the day on screen (ISO)
let ultimoDia = null;
let editando = null; // id of the saved food whose row is open for change
let anadiendo = false; // the "add by hand" card under the table is open
let lectores = []; // one per review card: copies what is typed into the model
let peticionDia = 0; // drops a late answer when ◀ ▶ are tapped fast

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const kcal = (n) => (n === null || n === undefined ? "—" : `${numero.format(n)} kcal`);
const cantidad = (a) => (a.cantidad === null ? "sin cantidad" : `${numero.format(a.cantidad)} ${a.unidad ?? ""}`.trim());
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
    return { ok: r.ok, datos };
  } catch {
    return { ok: false, datos: { error: "No hay conexión con el PC. Inténtalo de nuevo." } };
  }
}

function mensaje(texto, tipo = "") {
  $("mensaje").textContent = texto;
  $("mensaje").className = tipo;
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
  if (hoyISO === null) hoyISO = dia.fecha;
  if (dia.fecha !== diaVisto) { editando = null; anadiendo = false; }
  diaVisto = dia.fecha;
  ultimoDia = dia;
  const esHoy = dia.fecha === hoyISO;
  $("titulo").textContent = nombreDelDia(dia.fecha);
  document.title = `TriData · ${nombreDelDia(dia.fecha)}`;
  $("fecha").textContent = deFecha(dia.fecha);
  $("ir-hoy").disabled = esHoy;
  $("t-comido").firstChild.textContent = esHoy ? "Comido hoy " : `El ${deFecha(dia.fecha)} `;
  $("vacio").textContent = esHoy ? "Aún no has apuntado nada hoy." : `No hay nada apuntado el ${deFecha(dia.fecha)}.`;
  $("margenes").replaceChildren(...dia.margenes.map((m) => el("div", { className: `margen n${m.nivel}` },
    el("span", { className: "etiqueta", textContent: NIVELES[m.nivel] ?? `${m.nivel} %` }),
    el("strong", { textContent: kcal(m.kcal) }),
  )));
  $("plan").textContent = textoPlan(dia.plan);
  $("gasto").textContent = `Gasto estimado ${kcal(dia.gasto.kcal)}: ${dia.gasto.explicacion}.`;
  $("total").textContent = kcal(dia.total_kcal);

  const filas = dia.alimentos.map((a) => {
    if (a.id === editando) return el("tr", {}, el("td", { colSpan: 3 }, fichaCambio(a)));
    const marcas = [];
    if (a.estimado) marcas.push(el("span", { className: "marca", textContent: "estimado" }));
    if (a.pendiente_cantidad) marcas.push(el("span", { className: "marca alerta", textContent: "pendiente de cantidad" }));
    if (a.estado === "planeado") marcas.push(el("span", { className: "marca", textContent: "planeado" }));
    let comido = null;
    if (a.estado === "planeado" && a.dia <= hoyISO) { // a later day cannot be eaten yet (R3)
      comido = el("button", { type: "button", className: "comido", textContent: "Comido" });
      comido.addEventListener("click", (evento) => { evento.stopPropagation(); marcarComido(a, comido); });
    }
    const sumado = a.estado === "comido" && !a.pendiente_cantidad;
    const fila = el("tr", { className: sumado ? "" : "fuera", title: "Toca para cambiarlo" },
      el("td", {}, a.nombre, ...marcas, comido),
      el("td", { textContent: cantidad(a) }),
      el("td", { className: "num", textContent: sumado ? numero.format(a.kcal) : "—" }),
    );
    fila.addEventListener("click", () => { editando = a.id; anadiendo = false; mensaje(""); pintarDia(ultimoDia); });
    return fila;
  });
  $("tabla").tBodies[0].replaceChildren(...filas);
  $("tabla").hidden = filas.length === 0;
  $("vacio").hidden = filas.length > 0;
  $("anadir-ficha").replaceChildren(...(anadiendo ? [fichaAnadir(dia.fecha)] : []));
  $("anadir").hidden = anadiendo;

  $("avisos").replaceChildren(...(dia.pendientes.length ? [el("p", {
    className: "aviso",
    textContent: `Falta la cantidad de: ${dia.pendientes.map((p) => p.nombre).join(", ")}. No suma hasta que la pongas.`,
  })] : []));
}

// Inputs of a saved-food card; `leer()` takes what is typed at that moment (R7).
function camposFicha(a) {
  const nombre = campo("Alimento", a.nombre, "text");
  const cant = campo("Cantidad", a.cantidad, "number", null, { step: "any", min: "0", inputMode: "decimal" });
  const unidad = campo("Unidad", a.unidad, "text");
  const kc = campo("kcal", a.kcal, "number", null, { step: "any", min: "0", inputMode: "decimal" });
  const dia = campo("Día", a.dia, "date");
  const leer = () => {
    const datos = {
      nombre: nombre.entrada.value.trim(), cantidad: comoNumero(cant.entrada.value),
      unidad: unidad.entrada.value.trim() || null, kcal: comoNumero(kc.entrada.value), dia: dia.entrada.value,
    };
    if (datos.cantidad !== null && !datos.unidad) datos.unidad = "g";
    return datos;
  };
  const nodos = [nombre, el("div", { className: "fila" }, cant, unidad, kc), dia];
  return { nodos, leer, foco: () => nombre.entrada.focus() };
}

// What is missing before a food can be saved, said in words; "" if nothing.
function falta(datos) {
  if (!datos.nombre) return "Ponle nombre al alimento.";
  if (!datos.dia) return `Pon el día de «${datos.nombre}».`;
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
  const tarjeta = el("article", { className: "tarjeta" }, ...campos.nodos, aviso, acciones);
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
  const campos = camposFicha({ nombre: "", cantidad: null, unidad: null, kcal: null, dia: fecha });
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

async function cargarDia(fecha) {
  const turno = ++peticionDia;
  const { ok, datos } = await api(fecha ? `/api/dia?fecha=${encodeURIComponent(fecha)}` : "/api/dia");
  if (turno !== peticionDia) return;
  if (ok) pintarDia(datos);
  else mensaje(datos.error ?? "No he podido cargar el día.", "error");
}

function irA(fecha) {
  mensaje("");
  cargarDia(fecha);
}

// -- review of the interpreted list ---------------------------------------------

function campo(etiqueta, valor, tipo, alCambiar = null, extra = {}) {
  const entrada = el("input", { type: tipo, value: valor ?? "", ...extra });
  if (alCambiar) entrada.addEventListener("change", () => alCambiar(entrada.value));
  const etiquetado = el("label", { className: `campo ${tipo}` }, el("span", { textContent: etiqueta }), entrada);
  etiquetado.entrada = entrada;
  return etiquetado;
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
  if (!revision) return;
  const tarjetas = revision.alimentos.map((a, i) => {
    // Applies a typed value to the model; only a real change clears "dudoso" / "estimado".
    const aplicar = (clave, convertir = (v) => v) => (valor) => {
      const nuevo = convertir(valor);
      if (nuevo === a[clave] || (Number.isNaN(nuevo) && Number.isNaN(a[clave]))) return false;
      a[clave] = nuevo;
      if (clave === "nombre" || clave === "kcal") a.dudoso = false; // corrected by Andrés
      if (clave === "kcal") a.estimado = false;
      if (a.cantidad !== null && !a.unidad) a.unidad = "g"; // a quantity always has a unit
      return true;
    };
    const repintar = (aplicarlo) => (valor) => { if (aplicarlo(valor)) pintarRevision(); };
    const marcas = [];
    if (a.dudoso) marcas.push(el("span", { className: "marca alerta", textContent: "dudoso: corrígelo o quítalo" }));
    if (a.estimado && !a.dudoso) marcas.push(el("span", { className: "marca", textContent: "kcal estimadas" }));
    if (a.cantidad === null && !a.dudoso) marcas.push(el("span", { className: "marca alerta", textContent: "pendiente de cantidad" }));
    if (a.dia !== revision.hoy) marcas.push(el("span", { className: "marca", textContent: `planeado: ${deFecha(a.dia)}` }));
    if (a.anadida) marcas.push(el("span", { className: "marca", textContent: "añadido a mano" }));
    const quitar = el("button", { type: "button", className: "quitar", textContent: "Quitar" });
    quitar.addEventListener("click", () => { revision.alimentos.splice(i, 1); pintarRevision(); });
    const conversiones = {
      nombre: (v) => v.trim(), cantidad: comoNumero, unidad: (v) => v.trim() || null, kcal: comoNumero, dia: (v) => v,
    };
    const numerico = { step: "any", min: "0", inputMode: "decimal" };
    const campos = {
      nombre: campo("Alimento", a.nombre, "text", repintar(aplicar("nombre", conversiones.nombre))),
      cantidad: campo("Cantidad", a.cantidad, "number", repintar(aplicar("cantidad", comoNumero)), numerico),
      unidad: campo("Unidad", a.unidad, "text", repintar(aplicar("unidad", conversiones.unidad))),
      kcal: campo("kcal", a.kcal, "number", repintar(aplicar("kcal", comoNumero)), numerico),
      dia: campo("Día", a.dia, "date", repintar(aplicar("dia"))),
    };
    // Typing keeps the model and the block reason current without repainting the cards.
    for (const [clave, c] of Object.entries(campos)) {
      c.entrada.addEventListener("input", () => { aplicar(clave, conversiones[clave])(c.entrada.value); pintarBloqueo(); });
    }
    // R9: on "Confirmar", what is in the inputs at that moment is what gets saved.
    lectores.push(() => {
      for (const [clave, c] of Object.entries(campos)) aplicar(clave, conversiones[clave])(c.entrada.value);
    });
    return el("article", { className: a.dudoso ? "tarjeta dudosa" : (a.anadida ? "tarjeta nueva" : "tarjeta") },
      el("div", { className: "marcas" }, ...marcas),
      campos.nombre,
      el("div", { className: "fila" }, campos.cantidad, campos.unidad, campos.kcal),
      campos.dia,
      a.supuestos.length ? el("p", { className: "supuestos", textContent: `Supuestos: ${a.supuestos.join("; ")}` }) : null,
      quitar,
    );
  });
  $("lista").replaceChildren(...tarjetas);
  pintarBloqueo();
}

function motivoBloqueo() {
  const lista = revision.alimentos;
  const nombres = (filtro) => lista.filter(filtro).map((a) => `«${a.nombre}»`);
  const dudosos = nombres((a) => a.dudoso);
  const sinKcal = nombres((a) => !a.dudoso && a.nombre && a.cantidad !== null && a.kcal === null);
  const malos = nombres((a) => a.nombre && ((a.cantidad !== null && !(a.cantidad > 0)) || Number.isNaN(a.kcal) || !a.dia));
  if (!lista.length) return "No queda ningún alimento en la lista.";
  if (lista.some((a) => !a.nombre)) return "Hay un alimento sin nombre: ponle nombre o quítalo para poder confirmar.";
  if (dudosos.length) return `No entiendo ${dudosos.join(", ")}: corrígelo o quítalo para poder confirmar.`;
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
  $("confirmar").setAttribute("aria-disabled", String(Boolean(motivo)));
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
  $("enviar").disabled = true;
  mensaje("Interpretando…");
  const { ok, datos } = await api("/api/interpretar", { texto });
  $("enviar").disabled = false;
  if (!ok) { // the written text stays in the box (R10)
    mensaje(datos.error ?? "No he podido interpretar lo que has escrito. Inténtalo de nuevo.", "error");
    return;
  }
  mensaje("");
  revision = {
    texto, hoy: hoyISO,
    original: JSON.stringify(datos.alimentos), alimentos: datos.alimentos,
  };
  pintarRevision();
  $("revision").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function confirmar() {
  for (const leer of lectores) leer();
  if (motivoBloqueo()) { pintarRevision(); return; }
  $("confirmar").disabled = true;
  const alimentos = revision.alimentos.map(({ anadida, ...a }) => a);
  const { ok, datos } = await api("/api/confirmar", {
    texto: revision.texto, alimentos,
    sin_cambios: JSON.stringify(revision.alimentos) === revision.original,
  });
  if (!ok) {
    mensaje(datos.error ?? "No se ha podido guardar.", "error");
    $("confirmar").disabled = false;
    return;
  }
  const partes = ["Guardado."];
  const planeados = datos.guardados.filter((a) => a.estado === "planeado");
  if (planeados.length) partes.push(`Planeado: ${planeados.map((a) => `${a.nombre} (${deFecha(a.dia)})`).join(", ")}.`);
  const pendientes = datos.guardados.filter((a) => a.pendiente_cantidad);
  if (pendientes.length) partes.push(`Falta la cantidad de ${pendientes.map((a) => a.nombre).join(", ")}: no suma.`);
  mensaje(partes.join(" "), "bien");
  revision = null;
  $("texto").value = "";
  pintarRevision();
  if (diaVisto === datos.dia.fecha) pintarDia(datos.dia);
  else cargarDia(diaVisto); // the screen stays on the day being looked at (R6)
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("entrada").addEventListener("submit", interpretar);
$("confirmar").addEventListener("click", confirmar);
$("descartar").addEventListener("click", () => { revision = null; pintarRevision(); mensaje(""); });
$("anadir-revision").addEventListener("click", anadirTarjeta);
$("anadir").addEventListener("click", () => { anadiendo = true; editando = null; mensaje(""); pintarDia(ultimoDia); });
$("anterior").addEventListener("click", () => irA(sumarDias(diaVisto, -1)));
$("siguiente").addEventListener("click", () => irA(sumarDias(diaVisto, 1)));
$("ir-hoy").addEventListener("click", () => irA(hoyISO));
cargarDia();
