// Unit 031 (REC-19 of `ver-fitness`): the Fitness tab. Plain ES module, no build step.
// Three blocks from ONE answer, GET /api/fitness: the load (form, fatigue and freshness of today with the chart of the
// 14 days, grafica.js, and where today's HR comes from), the last activity, and the recovery (sleep, resting HR and
// HRV, each next to its 7-day mean). Tapping the activity opens its detail (GET /api/actividad/ID): every lap with
// its distance, time, pace or speed, mean HR and the zone of that HR, and the weather. A Garmin datum that has not
// arrived says «aún no ha llegado» and one given half-done «⚠ incompleto»: never a 0 (G-47). The server computes.

import { graficaCarga, leyendaCarga } from "./grafica.js";

const $ = (id) => document.getElementById(id);
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const unDecimal = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dosDecimales = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const diaSemana = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "short" });
const NO_HA_LLEGADO = "aún no ha llegado";
const ICONOS = { Carrera: "🏃", Bici: "🚴", Natación: "🏊", Fuerza: "🏋️" };

let api = null; // app.js's `api` (it keeps the «sin conexión» notice)
let peticion = 0; // drops a late answer
let abierta = null; // id of the activity whose detail is open
let hoyISO = null;

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const conSigno = (n) => { const r = Math.round(n); return (r > 0 ? "+" : r < 0 ? "−" : "") + enteras.format(Math.abs(r)); };
const horas = (s) => { const m = Math.round(s / 60); return `${Math.floor(m / 60)} h ${m % 60} min`; };
const minutos = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
const mmss = (s) => { const r = Math.round(s); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`; };
const distancia = (m) => (m >= 1000 ? `${unDecimal.format(m / 1000)} km` : `${enteras.format(m)} m`);
const distanciaVuelta = (m) => (m >= 1000 ? `${dosDecimales.format(m / 1000)} km` : `${enteras.format(m)} m`);
const sumarDias = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

function cuando(iso) {
  if (iso === hoyISO) return "hoy";
  if (hoyISO && iso === sumarDias(hoyISO, -1)) return "ayer";
  return diaSemana.format(new Date(`${iso}T12:00:00`)).replace(".", "").replace(",", "");
}

export function iniciarFitness(opciones) {
  ({ api } = opciones);
  $("fitness-ultima").addEventListener("click", alternarDetalle);
}

export async function cargarFitness() {
  const mia = ++peticion;
  const { ok, datos } = await api("/api/fitness");
  if (mia !== peticion) return;
  if (!ok) {
    $("fitness-pulso").textContent = datos.error ?? "No he podido cargar Fitness.";
    return;
  }
  hoyISO = datos.fecha;
  pintarCarga(datos.carga);
  pintarUltima(datos.ultima_actividad);
  pintarRecuperacion(datos.recuperacion);
}

function pintarCarga(c) {
  const h = c.hoy;
  $("fitness-ctl").textContent = h ? enteras.format(Math.round(h.ctl)) : "—";
  $("fitness-atl").textContent = h ? enteras.format(Math.round(h.atl)) : "—";
  $("fitness-tsb").textContent = h ? conSigno(h.tsb) : "—";
  $("fitness-tsb").classList.toggle("bien", Boolean(h) && Math.round(h.tsb) > 0);
  const caja = $("fitness-grafica");
  caja.replaceChildren(graficaCarga(c.serie, caja.clientWidth - 8), leyendaCarga());
  $("fitness-pulso").textContent = `Tu FC y tus zonas: ${c.pulso.texto}.`;
  $("fitness-pulso").classList.toggle("aviso", !c.pulso.confirmada);
}

function chips(a) {
  const lista = [];
  if (a.pulso_medio) lista.push(`Pulso medio ${enteras.format(Math.round(a.pulso_medio))}`);
  if (a.carga !== null) lista.push(`Carga ${enteras.format(Math.round(a.carga))}`);
  lista.push(a.vueltas ? `${a.vueltas} ${a.vueltas === 1 ? "vuelta" : "vueltas"}` : "sin vueltas");
  lista.push(a.clima ? "con clima" : "sin clima");
  return el("ul", { className: "chips-hoy" }, ...lista.map((t) => el("li", { className: "chip-hoy", textContent: t })));
}

function pintarUltima(a) {
  const boton = $("fitness-ultima");
  if (!a) {
    boton.disabled = true;
    boton.replaceChildren(el("p", { className: "suave", textContent: "Aún no hay ninguna actividad." }));
    $("fitness-detalle").hidden = true;
    return;
  }
  boton.disabled = false;
  boton.dataset.id = a.actividad_id;
  const linea = [a.distancia_m ? distancia(a.distancia_m) : null, a.duracion_s ? minutos(a.duracion_s) : null]
    .filter(Boolean).join(" · ");
  boton.replaceChildren(
    el("div", { className: "cab-actividad" },
      el("span", { className: "icono-actividad", textContent: ICONOS[a.deporte] ?? "⏱️", ariaHidden: "true" }),
      el("div", {},
        el("strong", { textContent: `${a.nombre} · ${cuando(a.fecha)}` }),
        el("span", { className: "suave", textContent: [a.deporte, linea].filter(Boolean).join(" · ") })),
      el("span", { className: "chev", textContent: "›" })),
    chips(a));
  if (abierta && abierta !== a.actividad_id) cerrarDetalle();
}

function cerrarDetalle() {
  abierta = null;
  $("fitness-detalle").hidden = true;
  $("fitness-ultima").setAttribute("aria-expanded", "false");
}

async function alternarDetalle() {
  const id = $("fitness-ultima").dataset.id;
  if (!id) return;
  if (abierta === id) { cerrarDetalle(); return; }
  abierta = id;
  $("fitness-ultima").setAttribute("aria-expanded", "true");
  const caja = $("fitness-detalle");
  caja.replaceChildren(el("p", { className: "suave", textContent: "Cargando las vueltas…" }));
  caja.hidden = false;
  const { ok, datos } = await api(`/api/actividad/${encodeURIComponent(id)}`);
  if (abierta !== id) return;
  if (!ok) { caja.replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido cargarla." })); return; }
  pintarDetalle(datos);
}

function ritmo(v) {
  if (v.velocidad_kmh !== null) return `${unDecimal.format(v.velocidad_kmh)} km/h`;
  if (v.ritmo_s_100m !== null) return `${mmss(v.ritmo_s_100m)} /100 m`;
  if (v.ritmo_s_km !== null) return `${mmss(v.ritmo_s_km)} /km`;
  return null;
}

function filaVuelta(v) {
  const datos = [v.distancia_m ? distanciaVuelta(v.distancia_m) : null, v.duracion_s ? mmss(v.duracion_s) : null, ritmo(v),
    v.pulso_medio ? `❤ ${enteras.format(Math.round(v.pulso_medio))}` : "sin pulso"].filter(Boolean);
  return el("li", { className: "vuelta" },
    el("span", { className: "n-vuelta", textContent: String(v.n) }),
    el("span", { className: "datos-vuelta", textContent: datos.join(" · ") }),
    el("span", { className: `zona-vuelta${v.zona ? ` z${v.zona}` : ""}`, textContent: v.zona ? `Z${v.zona}` : "—" }));
}

function textoClima(c) {
  const partes = [];
  if (c.temp_c !== null && c.temp_c !== undefined) partes.push(`${enteras.format(Math.round(c.temp_c))} °C`);
  if (c.condition) partes.push(c.condition);
  if (c.humidity_pct !== null && c.humidity_pct !== undefined) partes.push(`humedad ${enteras.format(Math.round(c.humidity_pct))} %`);
  if (c.wind_speed_kmh !== null && c.wind_speed_kmh !== undefined) partes.push(`viento ${enteras.format(Math.round(c.wind_speed_kmh))} km/h`);
  return partes.join(" · ");
}

function pintarDetalle(d) {
  const nodos = [el("h3", { textContent: `Vueltas de ${d.actividad.nombre}` })];
  if (d.vueltas.length) {
    if (!d.zonas) nodos.push(el("p", { className: "aviso", textContent: "Sin zonas confirmadas para ese día: confírmalas en Ajustes › Pulso y zonas." }));
    nodos.push(el("ol", { className: "vueltas" }, ...d.vueltas.map(filaVuelta)));
  } else {
    nodos.push(el("p", { className: "suave", textContent: "Esta actividad no tiene vueltas." }));
  }
  nodos.push(el("h3", { textContent: "Clima" }),
    el("p", { className: d.clima ? "clima" : "suave", textContent: d.clima ? textoClima(d.clima) : "Sin clima para esta actividad." }));
  $("fitness-detalle").replaceChildren(...nodos);
}

function dato(titulo, principal, detalle, estado) {
  const caja = el("div", { className: `tarjeta dato-recuperacion${estado === "no_ha_llegado" ? " falta" : ""}${estado === "incompleto" ? " incompleto" : ""}` },
    el("span", { textContent: titulo }),
    el("strong", { textContent: estado === "no_ha_llegado" ? NO_HA_LLEGADO : principal }));
  if (estado === "incompleto") caja.append(el("small", { className: "marca-incompleto", textContent: "⚠ incompleto" }));
  if (detalle) caja.append(el("small", { textContent: detalle }));
  return caja;
}

function pintarRecuperacion(r) {
  const s = r.sueno, p = r.pulso_reposo, v = r.hrv;
  const mediaSueno = s.media_7d ? `media 7 días ${horas(s.media_7d)}` : null;
  const detalleSueno = [s.puntuacion ? `${s.puntuacion} puntos` : null, mediaSueno].filter(Boolean).join(" · ");
  $("fitness-recuperacion").replaceChildren(
    dato("😴 Sueño", s.segundos ? horas(s.segundos) : null, detalleSueno, s.estado),
    dato("❤️ Pulso en reposo", p.valor ? `${enteras.format(p.valor)} ppm` : null,
      p.media_7d ? `media 7 días ${unDecimal.format(p.media_7d)}` : null, p.estado),
    dato("HRV", v.valor ? `${enteras.format(Math.round(v.valor))} ms` : null,
      v.media_7d ? `media 7 días ${enteras.format(Math.round(v.media_7d))} ms` : null, v.estado));
}
