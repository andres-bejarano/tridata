// Unit 030 (REC-18 of `ver-dia-movil`): Hoy at a glance. Plain ES module, no build step.
// Three cards from ONE answer, GET /api/hoy: Comida (eaten and what is left at the marked level, the same figures as
// Nutrición › Día), Entreno (today's activities) and Cómo llegas (sleep, resting HR, HRV with its 7-day mean and TSB,
// plus the hour of the last good sync and «↻ Actualizar Garmin»). Unit 080 (S-27): the card of the Asistente is gone.
// A Garmin datum that has not arrived says «aún no ha llegado» and one Garmin gave half-done «⚠ incompleto»: never
// a 0 (G-47). Tapping a card goes to its place. The button reuses POST /api/garmin/actualizar: while it runs it
// says so (Q-13); a failure says why with the words of the routine's warning and leaves data and hour as they were.
// Unit 099 (REC-43): below Comida, the card «Macros» (`macros` of /api/hoy): today's protein, carbohydrates and fat
// with «llevas», their objective and a bar; yesterday's verdict; tapping it opens and closes its 8 weeks in place.
// Unit 101 (REC-45, R-277): below 80 % of the kcal eaten with macros, only the warning (the share and how many foods
// have none) and the objective of the day, without bars.

const $ = (id) => document.getElementById(id);
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const fechaLarga = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long" });
const kcal = (n) => `${enteras.format(Math.round(Math.abs(n)))} kcal`;
const conSigno = (n) => { const r = Math.round(n); return (r > 0 ? "+" : r < 0 ? "−" : "") + enteras.format(Math.abs(r)); };
const NO_HA_LLEGADO = "aún no ha llegado";
const decimal = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const VEREDICTO = { bajo: "bajo", en_rango: "en rango", alto: "alto", bien: "bien" };
const MACROS = [["proteina", "proteína", "Proteína"], ["hidratos", "hidratos", "Hidratos"]];
const fechaCorta = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });

let api = null; // app.js's `api` (it keeps the «sin conexión» notice)
let alActualizarDia = () => {}; // app.js repaints Nutrición › Día with the day the button brings back
let motivos = {}; // the routine's words for each failure of the button (from /api/hoy)
let enMarcha = false;
let peticion = 0; // drops a late answer

export function iniciarHoy(opciones) {
  ({ api, alActualizarDia } = opciones);
  $("hoy-actualizar").addEventListener("click", actualizarGarmin);
  const macros = $("hoy-macros"); // unit 099: it opens its weeks, it does not navigate
  macros.addEventListener("click", alternarSemanas);
  macros.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter" || evento.key === " ") { evento.preventDefault(); alternarSemanas(); }
  });
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
  if (h.macros) pintarMacros(h.macros);
  pintarEntreno(h.entreno);
  pintarComoLlegas(h.como_llegas);
  motivos = h.garmin_sync.motivos ?? {};
  $("hoy-garmin-hora").textContent = h.garmin_sync.texto_hora;
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

// -- unit 099: the card «Macros» ------------------------------------------------------------------------------------

function nodo(etiqueta, clase, texto) {
  const n = document.createElement(etiqueta);
  if (clase) n.className = clase;
  if (texto !== undefined) n.textContent = texto;
  return n;
}

// A bar from 0 to 120 % of the top of the range, with the range shaded and what is eaten filled.
function barra(llevas, desde, hasta) {
  const b = nodo("div", "barra-macro");
  const escala = hasta * 1.2;
  const pct = (v) => `${Math.max(0, Math.min(100, (v / escala) * 100))}%`;
  if (desde !== null) {
    const rango = nodo("span", "rango-macro");
    rango.style.left = pct(desde);
    rango.style.width = hasta === null ? "0" : `calc(${pct(hasta)} - ${pct(desde)})`;
    b.append(rango);
  }
  const lleno = nodo("span", "lleno-macro");
  lleno.style.width = pct(llevas);
  b.append(lleno);
  return b;
}

function filaMacro(nombre, llevas, objetivo, laBarra) {
  const fila = nodo("li", "macro-fila");
  const texto = nodo("div", "macro-texto num");
  texto.append(nodo("strong", "", llevas === null ? nombre : `${nombre}: ${llevas}`),
    nodo("span", "objetivo-macro", objetivo));
  fila.append(texto);
  if (laBarra) fila.append(laBarra);
  return fila;
}

// Unit 101 (R-277, C-341): below 80 % of what is eaten with macros, only its objective: no bars, no grams eaten, no
// % of fat, no verdict (the warning above says the share and how many foods to ask the team for).
function soloObjetivo(d) {
  const filas = MACROS.map(([clave, , titulo]) => {
    const x = d[clave];
    const [a, b] = x.objetivo_g ?? x.objetivo_g_kg;
    const rango = x.objetivo_g ? `${a}-${b} g` : `${decimal.format(a)}-${decimal.format(b)} g/kg`;
    return filaMacro(titulo, null, `objetivo ${rango}`, null);
  });
  filas.push(filaMacro("Grasa", null, `objetivo ≥ ${d.grasa.objetivo_pct_min} %`, null));
  return filas;
}

function pintarMacros(m) {
  const d = m.hoy;
  if (d.estado !== "completo") {
    $("hoy-macros-barras").replaceChildren(...soloObjetivo(d));
    pintarAvisos(d);
    $("hoy-macros-ayer").textContent = `Ayer: ${textoVeredicto(m.ayer)}`;
    pintarSemanas(m.semanas);
    return;
  }
  const filas = MACROS.map(([clave, , titulo]) => {
    const x = d[clave];
    if (x.objetivo_g) {
      return filaMacro(titulo, `llevas ${enteras.format(Math.round(x.g))} g`,
        `de ${x.objetivo_g[0]}-${x.objetivo_g[1]} g`, barra(x.g, x.objetivo_g[0], x.objetivo_g[1]));
    }
    const [a, b] = x.objetivo_g_kg; // without a weight: only g/kg (R-274, C-347)
    return filaMacro(titulo, `llevas ${enteras.format(Math.round(x.g))} g`,
      `de ${decimal.format(a)}-${decimal.format(b)} g/kg`, barra(0, null, 1));
  });
  const g = d.grasa;
  const pct = g.pct_kcal === null ? "—" : `${g.pct_kcal} % de las kcal`;
  filas.push(filaMacro("Grasa", pct, `≥ ${g.objetivo_pct_min} %`,
    barra(g.pct_kcal ?? 0, g.objetivo_pct_min, g.objetivo_pct_min * 2)));
  $("hoy-macros-barras").replaceChildren(...filas);
  pintarAvisos(d);
  $("hoy-macros-ayer").textContent = `Ayer: ${textoVeredicto(m.ayer)}`;
  pintarSemanas(m.semanas);
}

function pintarAvisos(d) {
  const avisos = [d.estado === "completo" ? null : d.aviso, d.aviso_peso].filter(Boolean);
  $("hoy-macros-aviso").textContent = avisos.join(" · ");
  $("hoy-macros-aviso").hidden = !avisos.length;
}

// «proteína alto · hidratos bajo · grasa bien»; a day without verdict says why (R-276, R-277).
function textoVeredicto(d) {
  if (d.estado !== "completo") return d.aviso;
  const partes = [...MACROS.map(([clave, nombre]) => [nombre, d[clave].veredicto]), ["grasa", d.grasa.veredicto]]
    .filter(([, v]) => v).map(([nombre, v]) => `${nombre} ${VEREDICTO[v]}`);
  if (d.aviso_peso) partes.push("sin peso: sin veredicto de proteína ni hidratos");
  return partes.join(" · ");
}

function pintarSemanas(semanas) {
  const filas = [...semanas].reverse().map((s) => { // the current week first
    const fila = nodo("div", "macro-semana");
    fila.append(nodo("strong", "", `Semana del ${fechaCorta.format(new Date(`${s.lunes}T12:00:00`))}`));
    if (s.sin_macros) {
      fila.append(nodo("span", "", "sin macros"));
      return fila;
    }
    const partes = MACROS.filter(([clave]) => s[clave])
      .map(([clave, nombre]) => `${nombre} ${decimal.format(s[clave].media_g_kg)} g/kg · ${s[clave].en_rango} de ${s[clave].dias} días`);
    if (s.grasa) partes.push(`grasa ${s.grasa.media_pct} % · ${s.grasa.en_rango} de ${s.grasa.dias} días`);
    for (const p of partes) fila.append(nodo("span", "", p));
    return fila;
  });
  $("hoy-macros-semanas").replaceChildren(...filas);
}

function alternarSemanas() {
  const tarjeta = $("hoy-macros");
  const abrir = tarjeta.getAttribute("aria-expanded") !== "true";
  tarjeta.setAttribute("aria-expanded", String(abrir));
  $("hoy-macros-semanas").hidden = !abrir;
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
