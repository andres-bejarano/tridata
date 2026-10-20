// Settings page: profile and plan, each value with the date from which it applies.
// Plain ES module, no build step. The server validates and keeps the history; nothing is
// overwritten: a change is a new value "desde" a date (by default today).

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" });
const diaMesAno = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long", year: "numeric" });
const SIEMPRE = "0001-01-01";

// One row per setting. `peso` is the weight goal (kg + target date).
const DATOS = [
  { seccion: "perfil", clave: "edad", etiqueta: "Edad", unidad: "años", modo: "numeric" },
  { seccion: "perfil", clave: "altura_cm", etiqueta: "Altura", unidad: "cm", modo: "decimal" },
  { seccion: "perfil", clave: "pasos_por_defecto", etiqueta: "Actividad por defecto", unidad: "pasos", modo: "numeric" },
  { seccion: "perfil", clave: "basal_kcal", etiqueta: "Basal a mano", unidad: "kcal", modo: "decimal",
    nota: "Vale mientras no hayas apuntado ningún peso." },
  { seccion: "perfil", clave: "kcal_por_paso", etiqueta: "kcal por paso", unidad: "kcal/paso", modo: "decimal" },
  { seccion: "perfil", clave: "limite_saludable_pct", etiqueta: "Límite saludable", unidad: "%", modo: "decimal",
    nota: "El mismo para déficit y para superávit." },
  { seccion: "plan", clave: "objetivo_pct", etiqueta: "Objetivo del plan", unidad: "%", modo: "decimal", signo: true,
    nota: "Respecto al mantenimiento: negativo es déficit, positivo superávit." },
  { seccion: "plan", clave: "objetivo_peso", etiqueta: "Objetivo de peso", peso: true },
];

let vista = null; // last /api/ajustes answer
let hoyISO = null;
let abierto = null; // key of the setting whose change card is open
let carreraAbierta = null; // id of the race being changed, or "nueva"
let preguntaLimite = null; // { desde } while the healthy-limit question is shown

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

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

const fecha = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return (iso.slice(0, 4) === hoyISO.slice(0, 4) ? diaMes : diaMesAno).format(d);
};
const desdeTexto = (iso) => (iso === SIEMPRE ? "desde siempre" : `desde el ${fecha(iso)}`);
const pct = (n, signo) => `${signo && n > 0 ? "+" : ""}${numero.format(n).replace("-", "−")} %`;

function valorTexto(dato, valor) {
  if (valor === null || valor === undefined) return "sin fijar";
  if (dato.peso) return `${numero.format(valor.kg)} kg para el ${fecha(valor.fecha)}`;
  if (dato.unidad === "%") return pct(valor, dato.signo);
  return `${numero.format(valor)} ${dato.unidad}`;
}

function campo(etiqueta, valor, tipo, extra = {}) {
  const entrada = el("input", { type: tipo, value: valor ?? "", ...extra });
  const etiquetado = el("label", { className: `campo ${tipo}` }, el("span", { textContent: etiqueta }), entrada);
  etiquetado.entrada = entrada;
  return etiquetado;
}

const numerico = (modo) => ({ type: "text", inputMode: modo, autocomplete: "off" });
const crudo = (entrada) => entrada.value.trim();

// -- one setting --------------------------------------------------------------------

function filaDato(dato) {
  const { valor, desde } = vista.vigentes[dato.clave];
  const futuros = vista.futuros.filter((f) => f.clave === dato.clave);
  const cabecera = el("div", { className: "dato" },
    el("div", {},
      el("span", { className: "etiqueta", textContent: dato.etiqueta }),
      el("strong", { textContent: valorTexto(dato, valor) }),
      desde ? el("span", { className: "suave desde", textContent: desdeTexto(desde) }) : null,
    ),
  );
  const tarjeta = el("article", { className: "tarjeta" }, cabecera);
  for (const f of futuros) {
    tarjeta.append(el("p", { className: "futuro", textContent: `${valorTexto(dato, f.valor)} a partir del ${fecha(f.desde)}` }));
  }
  if (dato.nota) tarjeta.append(el("p", { className: "suave nota", textContent: dato.nota }));
  if (abierto === dato.clave) {
    tarjeta.classList.add("nueva");
    tarjeta.append(...fichaCambio(dato, valor));
  } else {
    const cambiar = el("button", { type: "button", className: "cambiar", textContent: "Cambiar" });
    cambiar.addEventListener("click", () => { abierto = dato.clave; carreraAbierta = null; mensaje(""); pintar(); });
    cabecera.append(cambiar);
  }
  return tarjeta;
}

function fichaCambio(dato, valor) {
  const nodos = [];
  let leer;
  if (dato.peso) {
    const kg = campo("Peso objetivo (kg)", valor?.kg, "text", numerico("decimal"));
    const para = campo("Para el día", valor?.fecha, "date");
    nodos.push(el("div", { className: "fila dos" }, kg, para));
    leer = () => ({ kg: crudo(kg.entrada) || null, fecha: para.entrada.value || null });
  } else {
    const nuevo = campo(`${dato.etiqueta} (${dato.unidad})`, valor, "text", numerico(dato.modo));
    nodos.push(nuevo);
    leer = () => crudo(nuevo.entrada) || null;
    setTimeout(() => nuevo.entrada.focus(), 0);
  }
  const desde = campo("Desde", hoyISO, "date");
  nodos.push(desde);
  const aviso = el("p", { className: "aviso", hidden: true });
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  cancelar.addEventListener("click", () => { abierto = null; mensaje(""); pintar(); });
  guardar.addEventListener("click", async () => {
    if (!desde.entrada.value) { aviso.textContent = "Di desde qué día vale."; aviso.hidden = false; return; }
    guardar.disabled = true;
    const { ok, datos } = await api("/api/ajustes", { datos: { [dato.clave]: leer() }, desde: desde.entrada.value });
    guardar.disabled = false;
    if (!ok) { aviso.textContent = datos.error ?? "No se ha podido guardar."; aviso.hidden = false; return; }
    abierto = null;
    vista = datos.ajustes;
    preguntaLimite = datos.preguntar_limite ? { desde: datos.desde } : null;
    mensaje(`Guardado: ${dato.etiqueta.toLowerCase()} ${desdeTexto(datos.desde)}.`, "bien");
    pintar();
  });
  nodos.push(aviso, el("div", { className: "acciones" }, guardar, cancelar));
  return nodos;
}

// A non-zero plan target and no healthy limit: ask; a blank answer assumes nothing (R6).
function fichaLimite() {
  const limite = campo("Límite saludable (%)", "", "text", numerico("decimal"));
  const desde = campo("Desde", preguntaLimite.desde, "date");
  const aviso = el("p", { className: "aviso", hidden: true });
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar límite" });
  const ahoraNo = el("button", { type: "button", textContent: "Ahora no" });
  const cerrar = (texto) => { preguntaLimite = null; mensaje(texto, "bien"); pintar(); };
  ahoraNo.addEventListener("click", () => cerrar("Objetivo guardado, sin límite saludable."));
  guardar.addEventListener("click", async () => {
    const valor = crudo(limite.entrada);
    if (!valor) { cerrar("Objetivo guardado, sin límite saludable."); return; }
    guardar.disabled = true;
    const { ok, datos } = await api("/api/ajustes", {
      datos: { limite_saludable_pct: valor }, desde: desde.entrada.value || preguntaLimite.desde,
    });
    guardar.disabled = false;
    if (!ok) { aviso.textContent = datos.error ?? "No se ha podido guardar."; aviso.hidden = false; return; }
    vista = datos.ajustes;
    cerrar(`Guardado: límite saludable ${desdeTexto(datos.desde)}.`);
  });
  setTimeout(() => limite.entrada.focus(), 0);
  return el("article", { className: "tarjeta pregunta" },
    el("p", { className: "fuerte", textContent: "No tienes límite saludable. ¿Hasta qué % de déficit o de superávit te parece saludable?" }),
    el("p", { className: "suave nota", textContent: "Si lo dejas en blanco, el objetivo queda guardado sin límite: no se supone ninguno." }),
    el("div", { className: "fila dos" }, limite, desde), aviso, el("div", { className: "acciones" }, guardar, ahoraNo));
}

// -- races ----------------------------------------------------------------------------

function fichaCarrera(c) {
  const nombre = campo("Carrera", c?.nombre, "text");
  const dia = campo("Fecha", c?.fecha, "date");
  const dias = campo("Días de mantenimiento antes", c?.dias_mantenimiento ?? 2, "text", numerico("numeric"));
  const aviso = el("p", { className: "aviso", hidden: true });
  const avisar = (t) => { aviso.textContent = t; aviso.hidden = !t; };
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const acciones = el("div", { className: "acciones" }, guardar, cancelar);
  cancelar.addEventListener("click", () => { carreraAbierta = null; mensaje(""); pintar(); });
  guardar.addEventListener("click", async () => {
    guardar.disabled = true;
    const cuerpo = { nombre: nombre.entrada.value, fecha: dia.entrada.value, dias_mantenimiento: crudo(dias.entrada) };
    const { ok, datos } = await api(c ? `/api/carrera/${c.id}` : "/api/carrera", cuerpo);
    guardar.disabled = false;
    if (!ok) { avisar(datos.error ?? "No se ha podido guardar."); return; }
    carreraAbierta = null;
    vista = datos.ajustes;
    mensaje(`${c ? "Cambiada" : "Añadida"}: ${datos.carrera.nombre}, el ${fecha(datos.carrera.fecha)}.`, "bien");
    pintar();
  });
  const tarjeta = el("article", { className: "tarjeta nueva" }, nombre, el("div", { className: "fila dos" }, dia, dias),
    aviso, acciones);
  if (c) { // removing asks first
    const quitar = el("button", { type: "button", className: "peligro", textContent: "Quitar carrera" });
    const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
      el("p", { textContent: `¿Quitar ${c.nombre}?` }));
    const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, quitar" });
    const no = el("button", { type: "button", textContent: "No" });
    pregunta.append(el("div", { className: "acciones" }, si, no));
    const preguntar = (abierta) => { pregunta.hidden = !abierta; acciones.hidden = abierta; quitar.hidden = abierta; };
    quitar.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
    no.addEventListener("click", () => preguntar(false));
    si.addEventListener("click", async () => {
      si.disabled = true;
      const { ok, datos } = await api(`/api/carrera/${c.id}/quitar`, { confirmado: true });
      si.disabled = false;
      if (!ok) { preguntar(false); avisar(datos.error ?? "No se ha podido quitar."); return; }
      carreraAbierta = null;
      vista = datos.ajustes;
      mensaje(`Quitada: ${datos.carrera.nombre}.`, "bien");
      pintar();
    });
    tarjeta.append(quitar, pregunta);
  } else {
    setTimeout(() => nombre.entrada.focus(), 0);
  }
  return tarjeta;
}

function filaCarrera(c) {
  if (carreraAbierta === c.id) return fichaCarrera(c);
  const antes = c.dias_mantenimiento === 1 ? "1 día" : `${c.dias_mantenimiento} días`;
  const cambiar = el("button", { type: "button", className: "cambiar", textContent: "Cambiar" });
  cambiar.addEventListener("click", () => { carreraAbierta = c.id; abierto = null; mensaje(""); pintar(); });
  return el("article", { className: "tarjeta" }, el("div", { className: "dato" },
    el("div", {},
      el("span", { className: "etiqueta", textContent: fecha(c.fecha) }),
      el("strong", { textContent: c.nombre }),
      el("span", { className: "suave desde", textContent: `mantenimiento ${antes} antes y el día de la carrera` }),
    ), cambiar));
}

// -- the page -------------------------------------------------------------------------

function planDeHoy(plan) {
  if (plan.objetivo_pct === null) return "Hoy: sin plan fijado.";
  const motivo = plan.motivo.startsWith("carrera ") ? ` (mantenimiento por la ${plan.motivo})` : "";
  const limite = plan.limite_saludable_pct === null ? "sin límite saludable" : `límite saludable ${pct(plan.limite_saludable_pct)}`;
  return `Hoy: ${plan.objetivo_pct === 0 ? "mantenimiento, 0 %" : pct(plan.objetivo_pct, true)}${motivo} · ${limite}.`;
}

function pintar() {
  for (const seccion of ["perfil", "plan"]) {
    $(seccion).replaceChildren(...DATOS.filter((d) => d.seccion === seccion).map(filaDato));
  }
  $("plan-hoy").textContent = planDeHoy(vista.plan);
  $("pregunta-limite").replaceChildren(...(preguntaLimite ? [fichaLimite()] : []));
  $("carreras").replaceChildren(...vista.carreras.map(filaCarrera));
  $("sin-carreras").hidden = vista.carreras.length > 0 || carreraAbierta === "nueva";
  $("carrera-nueva").replaceChildren(...(carreraAbierta === "nueva" ? [fichaCarrera(null)] : []));
  $("anadir-carrera").hidden = carreraAbierta === "nueva";
}

async function cargar() {
  const { ok, datos } = await api("/api/ajustes");
  if (!ok) { mensaje(datos.error ?? "No he podido cargar los ajustes.", "error"); return; }
  vista = datos;
  hoyISO = datos.fecha;
  pintar();
}

$("anadir-carrera").addEventListener("click", () => { carreraAbierta = "nueva"; abierto = null; mensaje(""); pintar(); });
cargar();
