// Day page of the food log. Plain ES module, no build step.
// Nothing is saved until "Confirmar y guardar"; the server computes every total.
// A saved food is changed, moved, deleted or marked eaten by tapping its row in the day table.
// Every card reads its inputs when "Guardar" / "Confirmar" is pressed, not on `change`.
// One message can bring several pieces (unit 007): weight, objective of the day, foods and
// doubtful phrases. They are reviewed together and saved together, or nothing is saved.
// Unit 008: the bar at the top, the full expenditure with Garmin and its breakdown, the
// «Actualizar Garmin» button, today's objective, levels and planned training, and, in the
// review, «si te lo comes» and «¿cuánto me cabe?» (a simulation: nothing is saved).

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const porPaso = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 3 }); // 0,04 kcal/paso must not read 0
const kilos = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 }); // 0,25 kg/semana must not read 0,3
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const signo = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + numero.format(Math.abs(n));
const etiquetaNivel = (n) => (n === 0 ? "Mantenimiento" : `${signo(n)} %`);
const PESO_MIN = 30;
const PESO_MAX = 250;

let revision = null; // { texto, hoy, original, alimentos, peso, objetivo, dudosas }
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
  $("margenes").replaceChildren(...dia.margenes.map((m) => el("div", { className: `margen n${m.nivel}${m.objetivo ? " objetivo" : ""}` },
    el("span", { className: "etiqueta", textContent: etiquetaNivel(m.nivel) }),
    el("strong", { textContent: kcal(m.kcal) }),
    m.objetivo ? el("span", { className: "marca-objetivo", textContent: "objetivo del día" }) : null,
  )));
  pintarPeso(dia);
  const objetivo = textoObjetivo(dia.objetivo_dia, dia.gasto);
  $("objetivo-dia").textContent = objetivo;
  $("objetivo-dia").hidden = !objetivo;
  $("plan").textContent = textoPlan(dia.plan);
  $("gasto").textContent = `Gasto estimado ${kcal(dia.gasto.kcal)}: ${dia.gasto.explicacion}.`;
  pintarBarra(dia);
  pintarDesglose(dia);
  pintarControles(dia, esHoy);
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

// The weight line of the day being looked at: weight, 7-day mean, basal and projection (unit 007).
function pintarPeso(dia) {
  const p = dia.peso;
  if (!p) { $("peso-dia").textContent = ""; $("peso-media").textContent = ""; $("peso-proyeccion").textContent = ""; return; }
  const quien = dia.fecha === hoyISO ? "Peso de hoy" : `Peso del ${deFecha(dia.fecha)}`;
  const nota = p.del_dia && p.del_dia.nota ? ` (${p.del_dia.nota})` : "";
  $("peso-dia").textContent = p.del_dia ? `${quien} ${kilos.format(p.del_dia.kg)} kg${nota}` : `${quien}: sin apuntar`;
  const media = p.media_7_dias
    ? `Media de 7 días ${kilos.format(p.media_7_dias.kg)} kg (${p.media_7_dias.dias_con_peso} ${p.media_7_dias.dias_con_peso === 1 ? "día" : "días"} con peso)`
    : `Media de 7 días: ${p.media_texto}`;
  const b = p.basal;
  const basal = b.kcal === null ? "basal: sin dato" : `basal ${kcal(b.kcal)} (${b.origen === "peso" ? "con tu peso" : "el de Ajustes"})`;
  $("peso-media").textContent = `${media} · ${basal}.`;
  const y = p.proyeccion;
  let proyeccion = `Proyección: ${p.proyeccion_texto ?? ""}.`;
  if (y && y.faltan_kg !== null) {
    const ritmo = y.kg_por_semana === null ? "la fecha ya ha pasado"
      : `hace falta ${kilos.format(Math.abs(y.kg_por_semana))} kg por semana`;
    const verbo = y.faltan_kg >= 0 ? "faltan" : "sobran";
    proyeccion = `Objetivo ${kilos.format(y.objetivo_kg)} kg el ${deFecha(y.fecha)}: ${verbo} ${kilos.format(Math.abs(y.faltan_kg))} kg, ${ritmo}.`;
  } else if (y) {
    proyeccion = `Objetivo ${numero.format(y.objetivo_kg)} kg el ${deFecha(y.fecha)}: ${p.proyeccion_texto}.`;
  }
  $("peso-proyeccion").textContent = proyeccion;
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

function pintarBarra(dia, b = dia.barra, titulo = null) {
  if (!b) { $("barra").hidden = true; return; }
  $("barra").hidden = false;
  $("barra").classList.toggle("pasada", b.pasado_kcal > 0);
  const lim = b.limite;
  // the scale reaches the objective and, if further, the healthy-limit mark (R-23, C-39)
  const escala = Math.max(b.objetivo_kcal, lim ? lim.kcal : 0, 1);
  $("barra-relleno").style.width = `${Math.min(b.comido_kcal / escala * 100, 100)}%`;
  $("barra-pct").textContent = `${b.pct} %`;
  $("barra-quedan").textContent = `del objetivo · ${b.texto}`;
  $("barra-objetivo").textContent = titulo ?? `${dia.objetivo.texto} Objetivo ${kcal(b.objetivo_kcal)} de ${kcal(dia.gasto.kcal)} de gasto.`;
  $("barra-limite").hidden = !lim;
  if (lim) {
    $("barra-limite").style.left = `calc(${lim.kcal / escala * 100}% - 2px)`;
    $("barra-limite").title = `límite saludable ${numero.format(lim.pct)} % (${kcal(lim.kcal)})`;
  }
  const aviso = lim && lim.aviso ? lim.aviso : "";
  $("barra-aviso").textContent = aviso;
  $("barra-aviso").hidden = !aviso;
}

function linea(etiqueta, valor) {
  return el("div", { className: "linea" }, el("span", { textContent: etiqueta }), el("span", { textContent: valor }));
}

function pintarDesglose(dia) {
  const g = dia.gasto;
  const d = g.desglose;
  if (!d) { $("desglose").replaceChildren(); return; }
  const lineas = [linea("Basal", kcal(d.basal_kcal))];
  const pasos = d.pasos_hechos === null
    ? `${numero.format(g.pasos)} pasos supuestos`
    : `${numero.format(d.pasos_hechos)} hechos + ${numero.format(d.pasos_supuestos)} supuestos`;
  lineas.push(linea(`Pasos: ${pasos} × ${porPaso.format(d.kcal_por_paso)} kcal`, kcal(d.pasos_kcal)));
  for (const h of d.entreno_hecho) lineas.push(linea(`Hecho: ${h.nombre} (${numero.format(h.minutos)} min)${h.sin_kcal ? " · sin kcal" : ""}`, kcal(h.kcal)));
  if (d.previsto) lineas.push(linea(`Previsto: ${d.previsto.nombre ?? "entreno"} (${numero.format(d.previsto.minutos)} min)`, kcal(d.previsto_kcal)));
  lineas.push(el("p", { className: "suave", textContent: mayuscula(g.garmin.texto) }));
  for (const a of g.avisos) lineas.push(el("p", { className: "aviso", textContent: a }));
  $("desglose").replaceChildren(...lineas);
}

const avisar = (id, texto) => { $(id).textContent = texto; $(id).hidden = !texto; };

function pintarControles(dia, esHoy) {
  $("controles-hoy").hidden = !esHoy;
  $("actualizar-garmin").hidden = !esHoy;
  if (!esHoy) { $("garmin-estado").textContent = ""; return; }
  const o = dia.objetivo;
  $("objetivo-actual").textContent = o.texto;
  if (document.activeElement !== $("objetivo-pct")) $("objetivo-pct").value = numero.format(o.porcentaje);
  $("objetivo-plan").hidden = o.origen !== "dia";
  $("retraso").textContent = o.retraso ? `${mayuscula(o.retraso.texto)}.` : "";
  $("retraso").hidden = !o.retraso;
  if (document.activeElement !== $("niveles-texto")) $("niveles-texto").value = textoNiveles(dia.margenes.map((m) => m.nivel));
  $("niveles-defecto").hidden = !dia.niveles_elegidos;
  const p = dia.gasto.desglose && dia.gasto.desglose.previsto;
  $("previsto-actual").textContent = p
    ? `Previsto: ${p.nombre ?? "entreno"}, ${numero.format(p.minutos)} min (${kcal(p.kcal)}). Quítalo cuando ya esté hecho.`
    : "Sin entreno previsto.";
  $("previsto-quitar").hidden = !p;
}

async function cambiarObjetivo(cuerpo, idError) {
  avisar(idError, "");
  const { ok, datos } = await api("/api/dia/objetivo", cuerpo);
  if (!ok) { avisar(idError, datos.error ?? "No se ha podido cambiar."); return false; }
  pintarDia(datos.dia);
  return true;
}

async function actualizarGarmin() {
  const boton = $("actualizar-garmin");
  if (boton.disabled) return; // one at a time
  boton.disabled = true;
  boton.textContent = "Actualizando…";
  $("garmin-estado").textContent = "";
  $("garmin-estado").className = "suave";
  const { ok, datos } = await api("/api/garmin/actualizar", {});
  boton.disabled = false;
  boton.textContent = "Actualizar Garmin";
  if (!ok) { // the data that were there stay as they were
    $("garmin-estado").textContent = datos.error ?? "No se ha podido conectar con Garmin.";
    $("garmin-estado").className = "suave error";
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
  $("previsto").addEventListener("toggle", () => { if ($("previsto").open) cargarRecientes(); });
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
  if (!revision) { $("simulacion").hidden = true; return; }
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
    const campos = {
      nombre: campo("Alimento", a.nombre, "text", repintar(aplicar("nombre", conversiones.nombre))),
      cantidad: campo("Cantidad", a.cantidad, "number", repintar(aplicar("cantidad", comoNumero)), numerico),
      unidad: campo("Unidad", a.unidad, "text", repintar(aplicar("unidad", conversiones.unidad))),
      kcal: campo("kcal", a.kcal, "number", repintar(aplicar("kcal", comoNumero)), numerico),
      dia: campo("Día", a.dia, "date", repintar(aplicar("dia"))),
    };
    // Typing keeps the model and the block reason current without repainting the cards.
    for (const [clave, c] of Object.entries(campos)) {
      c.entrada.addEventListener("input", () => { aplicar(clave, conversiones[clave])(c.entrada.value); pintarBloqueo(); programarSimulacion(); });
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
      el("div", { className: "acciones" }, cuanto, quitar), respuesta,
    );
  });
  $("lista").replaceChildren(...tarjetas);
  $("piezas").replaceChildren(...tarjetasPiezas());
  pintarBloqueo();
  programarSimulacion();
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
    texto, hoy: hoyISO, alimentos: datos.alimentos, peso: datos.peso ?? null,
    objetivo: datos.objetivo_dia ?? null, dudosas: datos.dudosas ?? [],
  };
  revision.original = huella(revision);
  $("confirmar").disabled = false; // a new review starts usable, also after an earlier confirmation
  pintarRevision();
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
    texto: revision.texto, alimentos, peso: revision.peso, objetivo_dia: revision.objetivo,
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
enlazarControles();
cargarDia();
