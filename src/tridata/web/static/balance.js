// The Balance tab (unit 017). Plain ES module like peso.js, no build step, no chart library.
// The week from Monday to Sunday (◀ ▶ by weeks), what is above the objective and «Repartir lo que llevo»
// (saves nothing until «Aplicar»), the weight-and-deficit chart of grafica.js with its three ranges and the
// list of every week. The server does every sum (nutricion/balance.py); this only draws and asks.
// Unit 018: a fourth button, «Fechas…», draws the same chart between two dates (/api/balance?desde=&hasta=); its
// form (`formularioFechas`) and `delAl` are shared with the Peso tab. «repartido» only on the days of the spread.

import { graficaPesoDeficit, leyendaPesoDeficit } from "./grafica.js";

const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const unDecimal = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const kilos = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nombreDia = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric" });
const mesLargo = new Intl.DateTimeFormat("es-ES", { month: "long" });
const mesCorto = new Intl.DateTimeFormat("es-ES", { month: "short" });
const RANGOS = [["4s", "4 semanas"], ["3m", "3 meses"], ["todo", "Todo"]];

let turno = 0; // drops a late answer when the tab is painted twice quickly
let semanaVista = null; // the Monday on screen (ISO), null = this week
let rango = "4s"; // the pressed button: "4s" | "3m" | "todo" | "fechas"
let ultimo = null; // { contenedor, irADia }
let fechas = null; // unit 018: { desde, hasta } last drawn between dates (kept in this module, never saved)
let enElFormulario = null; // unit 018: { desde, hasta } as typed, kept when going to another range and back
let hoyBalance = null;
let datosGrafica = null; // what the chart shows now

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined && hijo !== false) nodo.append(hijo);
  return nodo;
}

const comoFecha = (iso) => new Date(`${iso}T12:00:00`);
const sumarDias = (iso, n) => { const d = comoFecha(iso); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const mayuscula = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const signo = (n) => (Math.round(n) > 0 ? "+" : Math.round(n) < 0 ? "−" : "");
const kcalSigno = (n) => `${signo(n)}${enteras.format(Math.abs(Math.round(n)))}`; // whole kcal, never «−0»
const pctSigno = (n) => (n === null || n === undefined ? "—"
  : `${n > 0 ? "+" : n < 0 ? "−" : ""}${unDecimal.format(Math.abs(n))} %`);
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
const mes = (iso, fmt) => fmt.format(comoFecha(iso)).replace(".", "");

// «del 19 al 25 de octubre» / «del 28 de septiembre al 4 de octubre»; short: «del 12 al 18 oct».
export function delAl(desde, hasta, corto = false) {
  const fmt = corto ? mesCorto : mesLargo;
  const de = corto ? " " : " de ";
  const d1 = comoFecha(desde).getDate(), d2 = comoFecha(hasta).getDate();
  if (mes(desde, fmt) === mes(hasta, fmt)) return `del ${d1} al ${d2}${de}${mes(hasta, fmt)}`;
  return `del ${d1}${de}${mes(desde, fmt)} al ${d2}${de}${mes(hasta, fmt)}`;
}

async function pedir(ruta, cuerpo) {
  try {
    const r = await fetch(ruta, cuerpo === undefined ? undefined
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    let datos = {};
    try { datos = await r.json(); } catch { datos = {}; }
    return { ok: r.ok, estado: r.status, datos };
  } catch {
    return { ok: false, estado: 0, datos: { error: "No hay conexión con el PC." } };
  }
}

// Draws the whole tab into `contenedor`; `irADia(fecha)` opens that day in Hoy.
export async function pintarBalance(contenedor, irADia) {
  ultimo = { contenedor, irADia };
  const yo = ++turno;
  // unit 018 (the R12 family): while the tab is asked again, what it showed before cannot be tapped
  for (const b of contenedor.querySelectorAll("button")) b.disabled = true;
  const ruta = semanaVista ? `/api/balance/semana?fecha=${semanaVista}` : "/api/balance/semana";
  const [s, g, l] = await Promise.all([pedir(ruta), pedir(rutaGrafica()), pedir("/api/balance/semanas")]);
  if (yo !== turno) return;
  if (!s.ok || !g.ok || !l.ok) {
    contenedor.replaceChildren(el("p", { className: "aviso", textContent: "No he podido cargar tu balance: no hay conexión con el PC." }));
    return;
  }
  if (s.datos.vacio) {
    contenedor.replaceChildren(el("p", { className: "suave vacio-balance", textContent: "Aún no hay días con comida para enseñar." }));
    return;
  }
  const tarjetaSemana = el("article", { id: "balance-semana", className: "tarjeta semana-balance" });
  pintarSemana(tarjetaSemana, s.datos, irADia);
  hoyBalance = s.datos.hoy;
  const tarjetaGrafica = el("article", { id: "balance-grafica", className: "tarjeta grafica" });
  contenedor.replaceChildren(tarjetaSemana, tarjetaGrafica, el("h2", { textContent: "Tus semanas" }), listaSemanas(l.datos.semanas, contenedor));
  pintarGrafica(tarjetaGrafica, g.datos);
}

function rutaGrafica() {
  if (rango === "fechas" && fechas) return `/api/balance?desde=${fechas.desde}&hasta=${fechas.hasta}`;
  return `/api/balance?rango=${rango === "fechas" ? "4s" : rango}`;
}

function repintar() {
  if (ultimo) pintarBalance(ultimo.contenedor, ultimo.irADia);
}

// -- the week ----------------------------------------------------------------------------------------

function textoObjetivo(f) {
  if (f.objetivo_pct === undefined) return "";
  if (f.repartido) return `${pctSigno(f.objetivo_pct)} · repartido`; // unit 018: only the days of the spread
  if (f.motivo && f.motivo.startsWith("carrera ")) return `${pctSigno(f.objetivo_pct)} · carrera`;
  return pctSigno(f.objetivo_pct);
}

function barraDia(f, maximo) {
  const lado = f.balance_kcal < 0 ? "deficit" : "superavit";
  // a day that counts is never a sliver: at least a quarter of its half, so +150 next to −1.240 still reads
  const alto = Math.max(25, Math.min(100, Math.abs(f.balance_kcal) / maximo * 100));
  const relleno = el("span", { className: "barra-dia-relleno" });
  relleno.style.height = `${alto / 2}%`;
  return el("span", { className: `barra-dia ${lado}`, ariaHidden: "true" }, el("span", { className: "barra-dia-cero" }), relleno);
}

function filaDia(f, hoyISO, maximo, irADia) {
  const esHoy = f.dia === hoyISO;
  const nombre = mayuscula(nombreDia.format(comoFecha(f.dia)).replace(",", ""));
  const titulo = esHoy ? `Hoy · ${nombre.toLowerCase()}` : nombre;
  const boton = el("button", { type: "button", className: "fila-dia" });
  boton.dataset.dia = f.dia;
  let detalle = "", cifra = "", barra = null;
  if (f.estado === "sin_ajustes") {
    detalle = "faltan ajustes ese día";
  } else if (f.cuenta || (esHoy && f.comido_kcal > 0)) {
    detalle = `${enteras.format(Math.round(f.comido_kcal))} comidas · gasto ${enteras.format(Math.round(f.gasto_kcal))} ${f.gasto_origen === "garmin" ? "real" : "estimado"}`;
    cifra = f.cuenta ? [`${kcalSigno(f.balance_kcal)} kcal`, ` (${pctSigno(f.balance_pct)})`]
      : [`${kcalSigno(f.balance_kcal)} kcal`, " · provisional"];
    barra = barraDia(f, maximo);
    if (!f.cuenta) boton.classList.add("provisional");
  } else if (f.dia < hoyISO) {
    detalle = "sin comida apuntada";
  }
  const objetivo = f.dia >= hoyISO && f.estado !== "sin_ajustes" ? el("span", { className: "objetivo-fila", textContent: textoObjetivo(f) }) : null;
  boton.append(
    el("span", { className: "que" }, el("strong", { textContent: titulo }), detalle ? el("span", { textContent: detalle }) : null, objetivo),
    el("span", { className: "cifra-dia num" }, ...(cifra ? [el("span", { textContent: cifra[0] }), el("small", { textContent: cifra[1] })] : [])),
    barra ?? el("span", { className: "barra-dia vacia", ariaHidden: "true" }));
  boton.addEventListener("click", () => irADia(f.dia));
  return boton;
}

function resumenSemana(s) {
  const m = s.semana;
  if (!m || !m.dias) return "Aún no hay ningún día con la comida cerrada esta semana.";
  return `Acumulado ${kcalSigno(s.acumulado_kcal)} kcal · media ${kcalSigno(m.balance_medio_kcal)} kcal/día (${pctSigno(m.balance_pct)}) · ${plural(m.dias, "día", "días")}`;
}

function pintarSemana(tarjeta, s, irADia) {
  const hoyISO = s.hoy;
  const anterior = el("button", { type: "button", id: "semana-anterior", className: "flecha", ariaLabel: "Semana anterior", textContent: "◀" });
  const siguiente = el("button", { type: "button", id: "semana-siguiente", className: "flecha", ariaLabel: "Semana siguiente", textContent: "▶" });
  anterior.disabled = Boolean(s.primer_dia) && s.desde <= s.primer_dia;
  siguiente.disabled = s.es_actual;
  anterior.addEventListener("click", () => { semanaVista = sumarDias(s.desde, -7); repintar(); });
  siguiente.addEventListener("click", () => { const sig = sumarDias(s.desde, 7); semanaVista = sig > hoyISO ? null : sig; repintar(); });
  const titulo = s.es_actual ? `Esta semana · ${delAl(s.desde, s.hasta)}` : `Semana ${delAl(s.desde, s.hasta)}`;
  // the scale of the small bars is that of the days that count (today's provisional one is only capped)
  const maximo = Math.max(300, ...s.filas.filter((f) => f.cuenta).map((f) => Math.abs(f.balance_kcal)));
  const nodos = [
    el("nav", { className: "semana-cab", ariaLabel: "Cambiar de semana" }, anterior, el("h2", { id: "balance-titulo", textContent: titulo }), siguiente),
    el("div", { className: "filas-semana" }, ...s.filas.map((f) => filaDia(f, hoyISO, maximo, irADia))),
    el("p", { id: "balance-resumen", className: "resumen-semana num", textContent: resumenSemana(s) }),
  ];
  if (!s.es_actual) {
    const volver = el("button", { type: "button", id: "volver-a-esta", className: "boton ancho", textContent: "Volver a esta semana" });
    volver.addEventListener("click", () => { semanaVista = null; repintar(); });
    nodos.push(volver);
  } else {
    nodos.push(...reparto(s));
  }
  tarjeta.replaceChildren(...nodos);
}

// -- «Repartir lo que llevo» (R-20): propose, saving nothing; apply only on «Aplicar» -----------------

function reparto(s) {
  const nodos = [];
  const mensaje = el("p", { id: "reparto-mensaje", className: "aviso", role: "status", hidden: true });
  if (s.reparto_posible === true) {
    const boton = el("button", { type: "button", id: "repartir", className: "principal ancho", textContent: "Repartir lo que llevo" });
    const caja = el("div", { id: "reparto", className: "reparto desplegable", hidden: true });
    let propuesta = null;
    const pintarPropuesta = (p) => {
      propuesta = p;
      const aplicar = el("button", { type: "button", id: "reparto-aplicar", className: "principal", textContent: "Aplicar" });
      const no = el("button", { type: "button", id: "reparto-no", textContent: p.dias.length ? "No, déjalo" : "Entendido" });
      aplicar.disabled = !p.dias.length;
      no.addEventListener("click", () => { caja.hidden = true; boton.hidden = false; mensaje.hidden = true; });
      aplicar.addEventListener("click", async () => {
        aplicar.disabled = true;
        const r = await pedir("/api/balance/repartir", { aplicar: true, propuesta });
        if (r.estado === 409 && r.datos.propuesta) {
          pintarPropuesta(r.datos.propuesta);
          mensaje.textContent = "La semana ha cambiado desde la propuesta: mírala otra vez antes de aplicarla.";
          mensaje.hidden = false;
          return;
        }
        if (!r.ok) { aplicar.disabled = false; mensaje.textContent = r.datos.error ?? "No se ha podido aplicar."; mensaje.hidden = false; return; }
        repintar();
      });
      // unit 018: nothing to propose (already spread): only the reason, nothing to apply
      caja.replaceChildren(el("p", { id: "reparto-texto", textContent: p.texto }),
        p.dias.length ? el("p", { className: "suave", textContent: "Aún no se ha cambiado nada: solo si lo aplicas." }) : null,
        el("div", { className: "acciones" }, p.dias.length ? aplicar : null, no));
    };
    boton.addEventListener("click", async () => {
      boton.disabled = true;
      const r = await pedir("/api/balance/repartir", { aplicar: false });
      boton.disabled = false;
      if (!r.ok) { mensaje.textContent = r.datos.error ?? "No he podido calcular el reparto."; mensaje.hidden = false; return; }
      pintarPropuesta(r.datos.propuesta);
      mensaje.hidden = true;
      caja.hidden = false;
      boton.hidden = true;
      caja.scrollIntoView?.({ block: "center" }); // the proposal is below the week: bring it into sight
    });
    nodos.push(boton, caja);
  } else if (typeof s.reparto_posible === "string") {
    nodos.push(el("p", { id: "reparto-motivo", className: "suave", textContent: s.reparto_posible }));
  }
  if (s.volver_al_plan) {
    const volver = el("button", { type: "button", id: "volver-al-plan", className: "boton ancho", textContent: "Volver al plan" });
    const si = el("button", { type: "button", id: "volver-si", className: "principal", textContent: "Sí, volver al plan" });
    const no = el("button", { type: "button", id: "volver-no", textContent: "No" });
    const confirmar = el("div", { id: "volver-confirmar", className: "confirmar-borrado", hidden: true },
      el("p", { textContent: "Los días que quedan de esta semana vuelven al % de tu plan: también quita un % puesto a mano en esos días. Los días de mantenimiento antes de una carrera se quedan como están." }),
      el("div", { className: "acciones" }, si, no));
    volver.addEventListener("click", () => { confirmar.hidden = false; volver.hidden = true; });
    no.addEventListener("click", () => { confirmar.hidden = true; volver.hidden = false; });
    si.addEventListener("click", async () => {
      si.disabled = true;
      const r = await pedir("/api/balance/volver-al-plan", { confirmado: true });
      if (!r.ok) { si.disabled = false; mensaje.textContent = r.datos.error ?? "No se ha podido volver al plan."; mensaje.hidden = false; return; }
      repintar();
    });
    nodos.push(volver, confirmar);
  }
  nodos.push(mensaje);
  return nodos;
}

// -- the chart and its ranges ----------------------------------------------------------------------

const fechaLarga = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" });

// Unit 018: «Desde», «Hasta» (type=date, between `min` and `max`) and «Ver», shared with the Peso tab. «Ver» with
// the start after the end only says so: nothing is asked and the chart stays. `alVer(desde, hasta)` asks.
export function formularioFechas({ prefijo, desde, hasta, min, max, alVer, alEscribir }) {
  const campo = (id, texto, valor) => {
    const entrada = el("input", { type: "date", id: `${prefijo}-${id}`, value: valor ?? "" });
    if (min) entrada.min = min;
    if (max) entrada.max = max;
    entrada.addEventListener("input", () => alEscribir?.({ desde: inicio.value, hasta: fin.value }));
    return [entrada, el("label", { className: "campo-fecha", htmlFor: `${prefijo}-${id}` }, el("span", { textContent: texto }), entrada)];
  };
  const [inicio, etiquetaInicio] = campo("desde", "Desde", desde);
  const [fin, etiquetaFin] = campo("hasta", "Hasta", hasta);
  const ver = el("button", { type: "button", id: `${prefijo}-ver`, className: "principal", textContent: "Ver" });
  const error = el("p", { id: `${prefijo}-fechas-error`, className: "aviso", role: "alert", hidden: true });
  ver.addEventListener("click", () => {
    error.hidden = true;
    if (!inicio.value || !fin.value) { error.textContent = "Elige las dos fechas."; error.hidden = false; return; }
    if (inicio.value > fin.value) { error.textContent = "La fecha de inicio tiene que ser anterior o igual a la de fin"; error.hidden = false; return; }
    alVer(inicio.value, fin.value, error);
  });
  return el("div", { id: `${prefijo}-fechas`, className: "fechas-grafica" },
    el("div", { className: "fechas-campos" }, etiquetaInicio, etiquetaFin, ver), error);
}

// Unit 018: under the chart, what a range between dates needs to say (the data start later, or there are none).
export function notaFechas(prefijo, pedido, datos) {
  if (datos.vacio) return el("p", { id: `${prefijo}-fechas-nota`, className: "suave", textContent: "No hay días con datos entre esas fechas" });
  if (pedido && datos.desde && pedido.desde < datos.desde) {
    return el("p", { id: `${prefijo}-fechas-nota`, className: "suave", textContent: `Tus datos empiezan el ${fechaLarga.format(comoFecha(datos.desde))}` });
  }
  return null;
}

async function verEntreFechas(tarjeta, desde, hasta, error) {
  const r = await pedir(`/api/balance?desde=${desde}&hasta=${hasta}`);
  if (!r.ok) { error.textContent = r.datos.error ?? "No he podido cargar esas fechas."; error.hidden = false; return; }
  if (rango !== "fechas") return;
  fechas = { desde, hasta };
  pintarGrafica(tarjeta, r.datos);
}

function pintarGrafica(tarjeta, datos) {
  datosGrafica = datos;
  if (datos.hasta && !hoyBalance) hoyBalance = datos.hasta;
  const botones = RANGOS.map(([clave, texto]) => {
    const b = el("button", { type: "button", textContent: texto });
    b.setAttribute("aria-pressed", String(clave === rango));
    b.addEventListener("click", async () => {
      if (clave === rango) return;
      rango = clave;
      const r = await pedir(`/api/balance?rango=${clave}`);
      if (r.ok && rango === clave) pintarGrafica(tarjeta, r.datos);
    });
    return b;
  });
  const deFechas = el("button", { type: "button", id: "balance-fechas-boton", textContent: "Fechas…" });
  deFechas.setAttribute("aria-pressed", String(rango === "fechas"));
  deFechas.addEventListener("click", async () => {
    if (rango === "fechas") return;
    rango = "fechas";
    if (fechas) {
      const r = await pedir(rutaGrafica());
      if (r.ok && rango === "fechas") { pintarGrafica(tarjeta, r.datos); return; }
    }
    pintarGrafica(tarjeta, datosGrafica);
  });
  const ancho = tarjeta.clientWidth ? tarjeta.clientWidth - 10 : undefined;
  const que = datos.agrupado === "semana" ? "media diaria de cada semana" : "un día por barra";
  const entreFechas = rango === "fechas" && fechas && !datos.rango;
  const nodos = [
    el("div", { className: "dato-fila cabeza-grafica" }, el("strong", { textContent: "Peso y déficit" }),
      entreFechas && datos.desde ? el("span", { id: "balance-periodo", className: "periodo", textContent: delAl(datos.desde, datos.hasta, true) }) : null,
      el("span", { className: "suave leyenda", textContent: que })),
    el("div", { id: "balance-selector", className: "segmentado rangos", role: "group", ariaLabel: "Rango de la gráfica" },
      el("div", { id: "balance-rangos", className: "rangos-fijos" }, ...botones), deFechas),
  ];
  if (rango === "fechas") {
    const valores = enElFormulario ?? fechas ?? { desde: datos.desde, hasta: datos.hasta };
    nodos.push(formularioFechas({
      prefijo: "balance", desde: valores.desde, hasta: valores.hasta, min: datos.primer_dia, max: hoyBalance,
      alEscribir: (v) => { enElFormulario = v; },
      alVer: (desde, hasta, error) => { enElFormulario = { desde, hasta }; verEntreFechas(tarjeta, desde, hasta, error); },
    }));
    if (entreFechas) nodos.push(notaFechas("balance", fechas, datos));
  }
  if (!(entreFechas && datos.vacio)) nodos.push(graficaPesoDeficit(datos, ancho), leyendaPesoDeficit(datos.objetivo));
  tarjeta.replaceChildren(...nodos.filter(Boolean));
}

// -- every week ------------------------------------------------------------------------------------

function textoSemana(w) {
  const peso = w.peso_medio_kg === null ? "—" : `${kilos.format(w.peso_medio_kg)} kg`;
  if (!w.dias) return `Semana ${delAl(w.desde, w.hasta, true)} · sin días cerrados · ${peso}`;
  return `Semana ${delAl(w.desde, w.hasta, true)} · ${kcalSigno(w.acumulado_kcal)} kcal · media ${kcalSigno(w.balance_medio_kcal)}/día (${pctSigno(w.balance_pct)}) · ${plural(w.dias, "día", "días")} · ${peso}`;
}

function listaSemanas(lista, contenedor) {
  const caja = el("article", { id: "balance-semanas", className: "tarjeta lista semanas" });
  for (const w of lista) {
    const b = el("button", { type: "button", className: "semana", textContent: textoSemana(w) });
    b.dataset.desde = w.desde;
    b.addEventListener("click", () => {
      semanaVista = w.desde;
      repintar();
      contenedor.closest("section")?.scrollIntoView?.({ block: "start" });
      window.scrollTo(0, 0);
    });
    caja.append(b);
  }
  return caja;
}
