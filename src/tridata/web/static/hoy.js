// Unit 030 (REC-18 of `ver-dia-movil`): Hoy at a glance. Plain ES module, no build step.
// Four cards from ONE answer, GET /api/hoy: Comida (eaten and what is left at the marked level, the same figures as
// Nutrición › Día), Entreno (today's activities), Cómo llegas (sleep, resting HR, HRV with its 7-day mean and TSB,
// plus the hour of the last good sync and «↻ Actualizar Garmin») and Asistente (empty until unit 032).
// A Garmin datum that has not arrived says «aún no ha llegado» and one Garmin gave half-done «⚠ incompleto»: never
// a 0 (G-47). Tapping a card goes to its place. The button reuses POST /api/garmin/actualizar: while it runs it
// says so (Q-13); a failure says why with the words of the routine's warning and leaves data and hour as they were.

const $ = (id) => document.getElementById(id);
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const kcal = (n) => `${enteras.format(Math.round(Math.abs(n)))} kcal`;
const conSigno = (n) => { const r = Math.round(n); return (r > 0 ? "+" : r < 0 ? "−" : "") + enteras.format(Math.abs(r)); };
const NO_HA_LLEGADO = "aún no ha llegado";

let api = null; // app.js's `api` (it keeps the «sin conexión» notice)
let alActualizarDia = () => {}; // app.js repaints Nutrición › Día with the day the button brings back
let motivos = {}; // the routine's words for each failure of the button (from /api/hoy)
let enMarcha = false;
let peticion = 0; // drops a late answer

export function iniciarHoy(opciones) {
  ({ api, alActualizarDia } = opciones);
  $("hoy-actualizar").addEventListener("click", actualizarGarmin);
  for (const tarjeta of document.querySelectorAll("#hoy .tarjeta-hoy")) {
    tarjeta.addEventListener("click", (evento) => {
      if (evento.target.closest("a, button")) return; // its link and the Garmin button do their own thing
      location.hash = tarjeta.dataset.ir;
    });
  }
}

export async function cargarHoy() {
  const mia = ++peticion;
  const { ok, datos } = await api("/api/hoy");
  if (mia !== peticion) return;
  if (!ok) {
    $("hoy-comida-detalle").textContent = datos.error ?? "No he podido cargar el día.";
    return;
  }
  pintarHoy(datos);
}

function pintarHoy(h) {
  $("hoy-fecha").textContent = fechaLarga.format(new Date(`${h.fecha}T12:00:00`)).replace(",", "");
  pintarComida(h.comida);
  pintarEntreno(h.entreno);
  pintarComoLlegas(h.como_llegas);
  motivos = h.garmin_sync.motivos ?? {};
  $("hoy-garmin-hora").textContent = h.garmin_sync.texto_hora;
  $("hoy-asistente-texto").textContent = h.asistente ?? "Aún no has hablado con el asistente.";
}

function pintarComida(c) {
  const pasado = Math.round(c.queda_kcal) < 0;
  $("hoy-comida").querySelector(".cifra").textContent = enteras.format(Math.round(Math.abs(c.queda_kcal)));
  $("hoy-queda-texto").textContent = pasado ? "kcal te has pasado" : "kcal te quedan";
  $("hoy-comida").classList.toggle("pasado", pasado);
  const nivel = c.nivel === 0 ? "mantenimiento" : `nivel ${conSigno(c.nivel)} %`;
  const partes = [`${kcal(c.total_kcal)} comidas`, nivel];
  if (c.planeado_kcal > 0) partes.push(`${kcal(c.planeado_kcal)} planeadas`);
  $("hoy-comida-detalle").textContent = partes.join(" · ");
}

function pintarEntreno(actividades) {
  const filas = actividades.map((a) => {
    const fila = document.createElement("div");
    fila.className = "actividad-hoy";
    const titulo = document.createElement("strong");
    titulo.textContent = `${a.nombre} · ${a.minutos} min`;
    const detalle = document.createElement("span");
    detalle.textContent = [a.deporte, a.kcal === null ? null : kcal(a.kcal)].filter(Boolean).join(" · ");
    fila.append(titulo, detalle);
    return fila;
  });
  if (!filas.length) {
    const nada = document.createElement("p");
    nada.className = "suave";
    nada.textContent = "Hoy aún no has entrenado.";
    filas.push(nada);
  }
  $("hoy-actividades").replaceChildren(...filas);
}

function chip(id, texto, estado) {
  const nodo = $(id);
  nodo.textContent = estado === "incompleto" ? `${texto} ⚠ incompleto` : texto;
  nodo.classList.toggle("falta", estado === "no_ha_llegado");
  nodo.classList.toggle("incompleto", estado === "incompleto");
}

function horas(segundos) {
  const minutos = Math.round(segundos / 60);
  return `${Math.floor(minutos / 60)} h ${minutos % 60} min`;
}

function pintarComoLlegas(c) {
  const s = c.sueno;
  chip("hoy-sueno", s.estado === "no_ha_llegado" ? `😴 Sueño: ${NO_HA_LLEGADO}`
    : `😴 ${horas(s.segundos)}${s.puntuacion ? ` · ${s.puntuacion} puntos` : ""}`, s.estado);
  const p = c.pulso_reposo;
  chip("hoy-pulso", p.estado === "no_ha_llegado" ? `❤️ Reposo: ${NO_HA_LLEGADO}` : `❤️ reposo ${p.valor}`, p.estado);
  const v = c.hrv;
  const media = v.media_7d ? ` · media 7 días ${enteras.format(Math.round(v.media_7d))}` : "";
  chip("hoy-hrv", v.estado === "no_ha_llegado" ? `HRV: ${NO_HA_LLEGADO}${media}`
    : `HRV ${enteras.format(Math.round(v.valor))} ms${media}`, v.estado);
  const f = c.frescura;
  chip("hoy-frescura", f.estado === "no_ha_llegado" ? `Frescura: ${NO_HA_LLEGADO}` : `Frescura (TSB) ${conSigno(f.valor)}`,
    f.estado);
  $("hoy-frescura").classList.toggle("bien", f.estado === "ok" && Math.round(f.valor) > 0);
}

// The routine's words for the failure (R-101); «already running» and the rest say what the server says.
function textoDelFallo(datos) {
  const porque = motivos[datos.motivo];
  return porque ? `No se ha podido actualizar: ${porque}.` : (datos.error ?? "No se ha podido conectar con Garmin.");
}

async function actualizarGarmin() {
  if (enMarcha) return; // one at a time from this phone; the server says it for the routine (409)
  enMarcha = true;
  const boton = $("hoy-actualizar");
  boton.disabled = true;
  boton.textContent = "Actualizando Garmin…";
  $("hoy-garmin-error").hidden = true;
  const { ok, datos } = await api("/api/garmin/actualizar", {});
  enMarcha = false;
  boton.disabled = false;
  boton.textContent = "↻ Actualizar Garmin";
  if (!ok) { // the cards and the hour stay as they were
    $("hoy-garmin-error").textContent = textoDelFallo(datos);
    $("hoy-garmin-error").hidden = false;
    return;
  }
  alActualizarDia(datos.dia);
  await cargarHoy();
}
