// Day page of the food log. Plain ES module, no build step.
// Nothing is saved until "Confirmar y guardar"; the server computes every total.
// A saved food is changed by tapping its row in the day table.

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const NIVELES = { "-10": "−10 %", "0": "Mantenimiento", "10": "+10 %" };

let revision = null; // { texto, hoy, original, alimentos }
let hoyISO = null;
let ultimoDia = null;
let editando = null; // id of the saved food whose row is open for change

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const kcal = (n) => (n === null || n === undefined ? "—" : `${numero.format(n)} kcal`);
const cantidad = (a) => (a.cantidad === null ? "sin cantidad" : `${numero.format(a.cantidad)} ${a.unidad ?? ""}`.trim());
const deFecha = (iso) => fechaLarga.format(new Date(`${iso}T12:00:00`));

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

function pintarDia(dia) {
  hoyISO = dia.fecha;
  ultimoDia = dia;
  $("fecha").textContent = deFecha(dia.fecha);
  $("margenes").replaceChildren(...dia.margenes.map((m) => el("div", { className: `margen n${m.nivel}` },
    el("span", { className: "etiqueta", textContent: NIVELES[m.nivel] ?? `${m.nivel} %` }),
    el("strong", { textContent: kcal(m.kcal) }),
  )));
  $("gasto").textContent = `Gasto estimado ${kcal(dia.gasto.kcal)}: ${dia.gasto.explicacion}.`;
  $("total").textContent = kcal(dia.total_kcal);

  const filas = dia.alimentos.map((a) => {
    if (a.id === editando) return el("tr", {}, el("td", { colSpan: 3 }, fichaCambio(a)));
    const marcas = [];
    if (a.estimado) marcas.push(el("span", { className: "marca", textContent: "estimado" }));
    if (a.pendiente_cantidad) marcas.push(el("span", { className: "marca alerta", textContent: "pendiente de cantidad" }));
    if (a.estado === "planeado") marcas.push(el("span", { className: "marca", textContent: "planeado" }));
    const sumado = a.estado === "comido" && !a.pendiente_cantidad;
    const fila = el("tr", { className: sumado ? "" : "fuera", title: "Toca para cambiarlo" },
      el("td", {}, a.nombre, ...marcas),
      el("td", { textContent: cantidad(a) }),
      el("td", { className: "num", textContent: sumado ? numero.format(a.kcal) : "—" }),
    );
    fila.addEventListener("click", () => { editando = a.id; mensaje(""); pintarDia(ultimoDia); });
    return fila;
  });
  $("tabla").tBodies[0].replaceChildren(...filas);
  $("tabla").hidden = filas.length === 0;
  $("vacio").hidden = filas.length > 0;

  $("avisos").replaceChildren(...(dia.pendientes.length ? [el("p", {
    className: "aviso",
    textContent: `Falta la cantidad de: ${dia.pendientes.map((p) => p.nombre).join(", ")}. No suma hasta que la pongas.`,
  })] : []));
}

// Change card of a saved food: only name, quantity, unit and kcal; nothing changes until saved.
function fichaCambio(a) {
  const nuevo = { nombre: a.nombre, cantidad: a.cantidad, unidad: a.unidad, kcal: a.kcal };
  const poner = (clave, convertir = (v) => v) => (valor) => {
    nuevo[clave] = convertir(valor);
    if (clave === "cantidad" && nuevo.cantidad !== null && !nuevo.unidad) nuevo.unidad = "g";
  };
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar cambios" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  cancelar.addEventListener("click", () => { editando = null; mensaje(""); pintarDia(ultimoDia); });
  guardar.addEventListener("click", async () => {
    guardar.disabled = true;
    const { ok, datos } = await api(`/api/alimento/${a.id}`, nuevo);
    if (!ok) { // the saved food stays as it was; the card stays open with what was typed
      mensaje(datos.error ?? "No se ha podido guardar el cambio.", "error");
      guardar.disabled = false;
      return;
    }
    editando = null;
    mensaje(`Cambiado: ${datos.alimento.nombre}.`, "bien");
    pintarDia(datos.dia);
  });
  return el("article", { className: "tarjeta" },
    campo("Alimento", a.nombre, "text", poner("nombre")),
    el("div", { className: "fila" },
      campo("Cantidad", a.cantidad, "number", poner("cantidad", comoNumero), { step: "any", min: "0", inputMode: "decimal" }),
      campo("Unidad", a.unidad, "text", poner("unidad", (v) => v.trim() || null)),
      campo("kcal", a.kcal, "number", poner("kcal", comoNumero), { step: "any", min: "0", inputMode: "decimal" }),
    ),
    el("div", { className: "acciones" }, guardar, cancelar),
  );
}

async function cargarDia() {
  const { ok, datos } = await api("/api/dia");
  if (ok) pintarDia(datos);
  else mensaje(datos.error ?? "No he podido cargar el día.", "error");
}

// -- review of the interpreted list ---------------------------------------------

function campo(etiqueta, valor, tipo, alCambiar, extra = {}) {
  const entrada = el("input", { type: tipo, value: valor ?? "", ...extra });
  entrada.addEventListener("change", () => alCambiar(entrada.value));
  return el("label", { className: `campo ${tipo}` }, el("span", { textContent: etiqueta }), entrada);
}

const comoNumero = (v) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

function pintarRevision() {
  $("revision").hidden = !revision;
  if (!revision) return;
  const tarjetas = revision.alimentos.map((a, i) => {
    const cambiar = (clave, convertir = (v) => v) => (valor) => {
      a[clave] = convertir(valor);
      if (clave === "nombre" || clave === "kcal") a.dudoso = false; // corrected by Andrés
      if (clave === "kcal") a.estimado = false;
      if (clave === "cantidad" && a.cantidad !== null && !a.unidad) a.unidad = "g";
      pintarRevision();
    };
    const marcas = [];
    if (a.dudoso) marcas.push(el("span", { className: "marca alerta", textContent: "dudoso: corrígelo o quítalo" }));
    if (a.estimado && !a.dudoso) marcas.push(el("span", { className: "marca", textContent: "kcal estimadas" }));
    if (a.cantidad === null && !a.dudoso) marcas.push(el("span", { className: "marca alerta", textContent: "pendiente de cantidad" }));
    if (a.dia !== revision.hoy) marcas.push(el("span", { className: "marca", textContent: `planeado: ${deFecha(a.dia)}` }));
    const quitar = el("button", { type: "button", className: "quitar", textContent: "Quitar" });
    quitar.addEventListener("click", () => { revision.alimentos.splice(i, 1); pintarRevision(); });
    return el("article", { className: a.dudoso ? "tarjeta dudosa" : "tarjeta" },
      el("div", { className: "marcas" }, ...marcas),
      campo("Alimento", a.nombre, "text", cambiar("nombre")),
      el("div", { className: "fila" },
        campo("Cantidad", a.cantidad, "number", cambiar("cantidad", comoNumero), { step: "any", min: "0", inputMode: "decimal" }),
        campo("Unidad", a.unidad, "text", cambiar("unidad", (v) => v.trim() || null)),
        campo("kcal", a.kcal, "number", cambiar("kcal", comoNumero), { step: "any", min: "0", inputMode: "decimal" }),
      ),
      campo("Día", a.dia, "date", cambiar("dia")),
      a.supuestos.length ? el("p", { className: "supuestos", textContent: `Supuestos: ${a.supuestos.join("; ")}` }) : null,
      quitar,
    );
  });
  $("lista").replaceChildren(...tarjetas);

  const dudosos = revision.alimentos.filter((a) => a.dudoso).map((a) => `«${a.nombre}»`);
  const sinKcal = revision.alimentos.filter((a) => !a.dudoso && a.cantidad !== null && a.kcal === null).map((a) => `«${a.nombre}»`);
  let motivo = "";
  if (!revision.alimentos.length) motivo = "No queda ningún alimento en la lista.";
  else if (dudosos.length) motivo = `No entiendo ${dudosos.join(", ")}: corrígelo o quítalo para poder confirmar.`;
  else if (sinKcal.length) motivo = `Pon las kcal de ${sinKcal.join(", ")}.`;
  $("bloqueo").textContent = motivo;
  $("bloqueo").hidden = !motivo;
  $("confirmar").disabled = Boolean(motivo);
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
  $("confirmar").disabled = true;
  const { ok, datos } = await api("/api/confirmar", {
    texto: revision.texto, alimentos: revision.alimentos,
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
  pintarDia(datos.dia);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("entrada").addEventListener("submit", interpretar);
$("confirmar").addEventListener("click", confirmar);
$("descartar").addEventListener("click", () => { revision = null; pintarRevision(); mensaje(""); });
cargarDia();
