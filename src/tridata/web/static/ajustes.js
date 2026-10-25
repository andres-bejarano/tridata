// Settings page: profile and plan, each value with the date from which it applies.
// Plain ES module, no build step. The server validates and keeps the history; nothing is
// overwritten: a change is a new value "desde" a date (by default today).
// Unit 010: iOS-style rows (name, value in force, ›); tapping a row opens the same change card as
// before. A «Conexión» row says whether the PC answered. The tabs at the bottom go back to /#….
// Unit 031: «Pulso y zonas» is ONE row (HR max / rest and where they come from); its card saves the HR max, the HR
// rest, the five zones and their origin together, from a date (the server checks they hang together, R7).

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2, useGrouping: "always" });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" });
const diaMesAno = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long", year: "numeric" });
const kilos = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const diaMesCorto = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
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
const PULSO = "pulso"; // `abierto` when the «Pulso y zonas» card is open (unit 031)

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
    ponerConexion(r.ok || Boolean(datos.error));
    return { ok: r.ok, datos };
  } catch {
    ponerConexion(false);
    return { ok: false, datos: { error: "No hay conexión con el PC. Inténtalo de nuevo." } };
  }
}

// The «Conexión» row: whether /api/… answered the last time.
function ponerConexion(hay) {
  $("conexion-valor").textContent = hay ? "conectado" : "sin conexión";
  $("conexion-punto").classList.toggle("apagado", !hay);
}

function mensaje(texto, tipo = "") {
  $("mensaje").textContent = texto;
  $("mensaje").className = tipo;
}

const fecha = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return (iso.slice(0, 4) === hoyISO.slice(0, 4) ? diaMes : diaMesAno).format(d);
};
// «28 feb», and the year only when it is not this one («28 feb 2027»): it fits in a row
const fechaCorta = (iso) => {
  const corta = diaMesCorto.format(new Date(`${iso}T12:00:00`)).replace(".", "");
  return iso.slice(0, 4) === hoyISO.slice(0, 4) ? corta : `${corta} ${iso.slice(0, 4)}`;
};
const desdeTexto = (iso) => (iso === SIEMPRE ? "desde siempre" : `desde el ${fecha(iso)}`);
const pct = (n, signo) => `${signo && n > 0 ? "+" : ""}${numero.format(n).replace("-", "−")} %`;

function valorTexto(dato, valor) {
  if (valor === null || valor === undefined) return "sin fijar";
  if (dato.peso) return `${kilos.format(valor.kg)} kg · ${fechaCorta(valor.fecha)}`;
  if (dato.unidad === "%") return pct(valor, dato.signo);
  return `${numero.format(valor)} ${dato.unidad}`;
}

function campo(etiqueta, valor, tipo, extra = {}) {
  const entrada = el("input", { type: tipo, value: valor ?? "", ...extra });
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

const numerico = (modo) => ({ type: "text", inputMode: modo, autocomplete: "off" });
const crudo = (entrada) => entrada.value.trim();

// -- one setting --------------------------------------------------------------------

// One row per setting; tapping it opens (or closes) its change card under it.
function filaDato(dato) {
  const { valor, desde } = vista.vigentes[dato.clave];
  const futuros = vista.futuros.filter((f) => f.clave === dato.clave);
  const notas = [];
  if (desde && desde !== SIEMPRE) notas.push(desdeTexto(desde));
  if (dato.nota) notas.push(dato.nota);
  const que = el("span", { className: "que" }, dato.etiqueta,
    notas.length ? el("span", { textContent: notas.join(" · ") }) : null,
    ...futuros.map((f) => el("span", { className: "futuro", textContent: `${valorTexto(dato, f.valor)} a partir del ${fecha(f.desde)}` })));
  const fila = el("button", { type: "button", className: "ajuste" }, que,
    el("span", { className: "valor num", textContent: valorTexto(dato, valor) }), el("span", { className: "chev", textContent: "›" }));
  fila.setAttribute("aria-expanded", String(abierto === dato.clave));
  fila.addEventListener("click", () => {
    abierto = abierto === dato.clave ? null : dato.clave;
    carreraAbierta = null;
    mensaje("");
    pintar();
  });
  if (abierto !== dato.clave) return [fila];
  return [fila, el("article", { className: "tarjeta nueva" }, ...fichaCambio(dato, valor))];
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
    if (dato.signo) conSigno(nuevo.entrada);
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

// -- unit 031: «Pulso y zonas» --------------------------------------------------------

function textoOrigen(origen) {
  if (!origen) return null;
  return origen.tipo === "prueba" ? `de tu prueba de esfuerzo de ${origen.mes}` : "de la configuración";
}

function filasPulso() {
  const v = vista.vigentes;
  const max = v.fc_max?.valor ?? null, reposo = v.fc_reposo?.valor ?? null;
  const notas = [];
  if (max !== null) {
    const origen = textoOrigen(v.fc_origen?.valor);
    if (origen) notas.push(origen);
    if (v.fc_max.desde && v.fc_max.desde !== SIEMPRE) notas.push(desdeTexto(v.fc_max.desde));
    if (v.zonas_fc?.valor) notas.push(v.zonas_fc.valor.map((z, i) => `Z${i + 1} ${z[0]}–${z[1]}`).join(" · "));
  } else {
    notas.push("tu carga usa la configuración hasta que la confirmes");
  }
  const futuros = vista.futuros.filter((f) => f.clave === "fc_max")
    .map((f) => el("span", { className: "futuro", textContent: `FC máxima ${f.valor} a partir del ${fecha(f.desde)}` }));
  const fila = el("button", { type: "button", className: "ajuste" },
    el("span", { className: "que" }, "FC máxima / en reposo", el("span", { textContent: notas.join(" · ") }), ...futuros),
    el("span", { className: "valor num", textContent: max === null ? "sin confirmar" : `${max} / ${reposo} ppm` }),
    el("span", { className: "chev", textContent: "›" }));
  fila.setAttribute("aria-expanded", String(abierto === PULSO));
  fila.addEventListener("click", () => {
    abierto = abierto === PULSO ? null : PULSO;
    carreraAbierta = null;
    mensaje("");
    pintar();
  });
  return abierto === PULSO ? [fila, fichaPulso()] : [fila];
}

function fichaPulso() {
  const v = vista.vigentes;
  const max = campo("FC máxima (ppm)", v.fc_max?.valor, "text", numerico("numeric"));
  const reposo = campo("FC en reposo (ppm)", v.fc_reposo?.valor, "text", numerico("numeric"));
  const zonas = (v.zonas_fc?.valor ?? [[], [], [], [], []]).map((z, i) => [
    campo(`Zona ${i + 1} desde`, z[0], "text", numerico("numeric")),
    campo(`Zona ${i + 1} hasta`, z[1], "text", numerico("numeric")),
  ]);
  const origen = v.fc_origen?.valor;
  const tipo = el("select", {},
    el("option", { value: "prueba", textContent: "Prueba de esfuerzo" }),
    el("option", { value: "configuracion", textContent: "Configuración (reloj o a mano)" }));
  tipo.value = origen?.tipo ?? "prueba";
  const deDonde = el("label", { className: "campo" }, el("span", { textContent: "De dónde salen" }), tipo);
  const mes = campo("Mes de la prueba", origen?.mes ?? "", "month");
  const verMes = () => { mes.hidden = tipo.value !== "prueba"; };
  tipo.addEventListener("change", verMes);
  verMes();
  const desde = campo("Desde", hoyISO, "date");
  const aviso = el("p", { className: "aviso", hidden: true });
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  cancelar.addEventListener("click", () => { abierto = null; mensaje(""); pintar(); });
  guardar.addEventListener("click", async () => {
    if (!desde.entrada.value) { aviso.textContent = "Di desde qué día vale."; aviso.hidden = false; return; }
    const datos = {
      fc_max: crudo(max.entrada) || null,
      fc_reposo: crudo(reposo.entrada) || null,
      zonas_fc: zonas.map(([d, h]) => [crudo(d.entrada) || null, crudo(h.entrada) || null]),
      fc_origen: tipo.value === "prueba" ? { tipo: "prueba", mes: mes.entrada.value || null } : { tipo: "configuracion" },
    };
    guardar.disabled = true;
    const { ok, datos: r } = await api("/api/ajustes", { datos, desde: desde.entrada.value });
    guardar.disabled = false;
    if (!ok) { aviso.textContent = r.error ?? "No se ha podido guardar."; aviso.hidden = false; return; }
    abierto = null;
    vista = r.ajustes;
    mensaje(`Guardado: pulso y zonas ${desdeTexto(r.desde)}.`, "bien");
    pintar();
  });
  setTimeout(() => max.entrada.focus(), 0);
  return el("article", { className: "tarjeta nueva" },
    el("div", { className: "fila dos" }, max, reposo),
    el("p", { className: "suave nota", textContent: "Cinco zonas seguidas, sin huecos: la 1 empieza en tu FC en reposo y la 5 acaba en tu FC máxima." }),
    el("div", { className: "zonas-pulso" }, ...zonas.map(([d, h]) => el("div", { className: "fila dos" }, d, h))),
    el("div", { className: "fila dos" }, deDonde, mes), desde, aviso, el("div", { className: "acciones" }, guardar, cancelar));
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
  const antes = c.dias_mantenimiento === 1 ? "1 día" : `${c.dias_mantenimiento} días`;
  const fila = el("button", { type: "button", className: "ajuste" },
    el("span", { className: "que" }, c.nombre, el("span", { textContent: `${fecha(c.fecha)} · ${antes} de mantenimiento antes` })),
    el("span", { className: "chev", textContent: "›" }));
  fila.setAttribute("aria-expanded", String(carreraAbierta === c.id));
  fila.addEventListener("click", () => {
    carreraAbierta = carreraAbierta === c.id ? null : c.id;
    abierto = null;
    mensaje("");
    pintar();
  });
  return carreraAbierta === c.id ? [fila, fichaCarrera(c)] : [fila];
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
    $(seccion).replaceChildren(...DATOS.filter((d) => d.seccion === seccion).flatMap(filaDato));
  }
  $("pulso").replaceChildren(...filasPulso());
  $("plan-hoy").textContent = planDeHoy(vista.plan);
  $("pregunta-limite").replaceChildren(...(preguntaLimite ? [fichaLimite()] : []));
  $("carreras").replaceChildren(...vista.carreras.flatMap(filaCarrera));
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

// -- unit 041: Ajustes › El equipo, the model of each role (R7, G-56) ------------------------------------------------

let equipo = null; // last /api/equipo answer: { papeles, modelos, tope }
let papelAbierto = null; // id of the role whose model card is open

const dolares = (x) => `${x.toFixed(2).replace(".", ",")} $`;

function filaPapel(p) {
  const modelo = equipo.modelos.find((m) => m.id === p.modelo);
  const fila = el("button", { type: "button", className: "ajuste" },
    el("span", { className: "que" }, `${p.icono} ${p.nombre}`,
      el("span", { textContent: modelo ? `${modelo.nombre} (${modelo.descripcion})` : p.modelo })),
    el("span", { className: "chev", textContent: "›" }));
  fila.dataset.papel = p.id;
  fila.setAttribute("aria-expanded", String(papelAbierto === p.id));
  fila.addEventListener("click", () => {
    papelAbierto = papelAbierto === p.id ? null : p.id;
    mensaje("");
    pintarEquipo();
  });
  return papelAbierto === p.id ? [fila, fichaPapel(p)] : [fila];
}

function fichaPapel(p) {
  const opciones = equipo.modelos.map((m) => {
    const radio = el("input", { type: "radio", name: `modelo-${p.id}`, value: m.id, checked: m.id === p.modelo });
    return el("label", {}, radio, `${m.nombre} (${m.descripcion})`);
  });
  const aviso = el("p", { className: "aviso", hidden: true });
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  cancelar.addEventListener("click", () => { papelAbierto = null; mensaje(""); pintarEquipo(); });
  guardar.addEventListener("click", async () => {
    const elegido = opciones.map((o) => o.querySelector("input")).find((r) => r.checked);
    if (!elegido) { aviso.textContent = "Elige un modelo."; aviso.hidden = false; return; }
    guardar.disabled = true;
    let ok = false;
    let datos = {};
    try {
      const r = await fetch("/api/equipo", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ papel: p.id, modelo: elegido.value }) });
      datos = await r.json().catch(() => ({}));
      ok = r.ok;
      ponerConexion(true);
    } catch {
      ponerConexion(false);
      datos = { error: "No hay conexión con el PC. Inténtalo de nuevo." };
    }
    guardar.disabled = false;
    if (!ok) { aviso.textContent = datos.error ?? "No se ha podido guardar."; aviso.hidden = false; return; }
    equipo = datos;
    papelAbierto = null;
    const modelo = equipo.modelos.find((m) => m.id === elegido.value);
    mensaje(`Guardado: el ${p.nombre} responde con ${modelo?.nombre ?? elegido.value}.`, "bien");
    pintarEquipo();
  });
  return el("article", { className: "tarjeta nueva" }, el("div", { className: "equipo-modelos" }, ...opciones), aviso,
    el("div", { className: "acciones" }, guardar, cancelar));
}

function pintarEquipo() {
  const gasto = el("div", { id: "equipo-gasto", className: "ajuste estado" },
    el("span", { className: "que" }, "Gasto de este mes",
      el("span", { textContent: `${dolares(equipo.tope.gastado)} de un tope de ${numero.format(equipo.tope.dolares)} $` })));
  $("equipo").replaceChildren(...equipo.papeles.flatMap(filaPapel), gasto);
}

async function cargarEquipo() {
  const { ok, datos } = await api("/api/equipo");
  if (!ok) return;
  equipo = datos;
  pintarEquipo();
}

$("anadir-carrera").addEventListener("click", () => { carreraAbierta = "nueva"; abierto = null; mensaje(""); pintar(); });
cargar();
cargarEquipo();
