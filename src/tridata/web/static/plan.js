// Unit 040 (REC-22 of `llevar-plan`): Fitness › Plan (`#fitness/plan`). Plain ES module, no build step.
// Three views inside #fitness-plan, all painted from what the server computes:
// - the plan (GET /api/plan, or ?id= after «Cambiar»): its races with their date, target and state or result, and its
//   weeks with the planned time and key sessions next to what was done (running in time in movement and km, from
//   Garmin, or what was accepted from the plan), today's week marked; «Importar plan» and «Bajar .md»;
// - one week (GET /api/plan/semana/N): each day with its session by blocks and, below, what was done; a past day
//   without anything says «no hecho», never a 0. Tapping a done session opens its laps (GET /api/plan/actividad/ID:
//   the detail of Fitness plus cadence and climb);
// - the import (POST /api/plan/importar with the text of the chosen .md): the parts read and each difference with
//   Garmin, proposing the plan's (it comes from Strava); nothing is saved until «Confirmar importación»
//   (POST /api/plan/confirmar). If the AI fails, nothing is saved and «Reintentar» sends the same text again.
// Unit 042 (REC-26): each session of today or a day to come has «Mandar a mi Garmin» (POST
// /api/plan/sesion/ID/garmin): the card «Voy a mandar a tu Garmin» with its blocks and their margin of pace, confirmed
// or discarded with /api/asistente/* like every card (nothing goes to Garmin before «Confirmar», G-55); confirmed,
// the session says «en tu Garmin». If Garmin fails, the card says so and offers «Bajar fichero»
// (GET /api/plan/sesion/ID.fit, the same session as a FIT file).

const $ = (id) => document.getElementById(id);
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
const unDecimal = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const dosDecimales = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const DIAS = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];

let api = null; // app.js's `api` (it keeps the «sin conexión» notice)
let peticion = 0; // drops a late answer
let planVisto = null; // id of the plan on screen (null: the one of the current objective)
let ultimo = null; // the last answer of GET /api/plan
let importacion = null; // { texto, fichero, propuesta, elecciones } while importing
let semanaVista = null; // n of the week on screen (unit 042: repainted after sending a session)

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined && hijo !== false) nodo.append(hijo);
  return nodo;
}

// «48'», «2h05», «3h16»: as Andrés writes them
const hm = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m}'` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`; };
const mmss = (s) => { const r = Math.round(s); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`; };
const km = (n) => `${unDecimal.format(n)} km`;
const fecha = (iso) => new Date(`${iso}T12:00:00Z`);
const diaMes = (iso) => { const d = fecha(iso); return `${d.getUTCDate()}/${d.getUTCMonth() + 1}`; };
function rango(desde, hasta) {
  const a = fecha(desde), b = fecha(hasta);
  return a.getUTCMonth() === b.getUTCMonth() ? `${a.getUTCDate()}–${diaMes(hasta)}` : `${diaMes(desde)}–${diaMes(hasta)}`;
}
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;

export function iniciarPlan(opciones) {
  ({ api } = opciones);
  $("plan-fichero").addEventListener("change", async (evento) => {
    const fichero = evento.target.files?.[0];
    evento.target.value = ""; // the same file can be chosen again
    if (!fichero) return;
    importar(await fichero.text(), fichero.name);
  });
}

function aviso(texto, reintentar = null) {
  const caja = $("plan-aviso");
  caja.hidden = !texto;
  caja.className = texto ? "aviso" : "";
  caja.replaceChildren(texto ?? "");
  if (reintentar) {
    caja.append(" ", el("button", { type: "button", className: "enlace-boton", textContent: "Reintentar", onclick: reintentar }));
  }
}

function ver(cual) {
  for (const v of ["vista", "semana", "importar"]) $(`plan-${v}`).hidden = v !== cual;
  window.scrollTo(0, 0);
}

export async function cargarPlan() {
  const mia = ++peticion;
  const { ok, datos } = await api(planVisto ? `/api/plan?id=${planVisto}` : "/api/plan");
  if (mia !== peticion) return;
  if (!ok) {
    if (planVisto) { planVisto = null; cargarPlan(); return; } // that plan is gone: the current one
    aviso(datos.error ?? "No he podido cargar tu plan.");
    return;
  }
  if (importacion === null) aviso("");
  pintarPlan(datos);
  if (importacion === null) ver("vista");
}

// -- the plan ------------------------------------------------------------------------------------------------------

function botonImportar() {
  return el("button", { type: "button", id: "plan-importar-boton", textContent: "📥 Importar plan",
    onclick: () => $("plan-fichero").click() });
}

function pintarPlan(datos) {
  ultimo = datos;
  const caja = $("plan-vista");
  const plan = datos.plan;
  if (!plan) {
    caja.replaceChildren(
      el("p", { className: "tarjeta plan-vacio", textContent: "Importa tu plan: elige tu .md y la app lo ordena en sus partes, lo cruza con Garmin y te enseña las diferencias antes de guardar nada." }),
      el("div", { className: "acciones-plan" }, botonImportar()));
    return;
  }
  const cabecera = el("div", { className: "plan-cab" }, el("h2", { id: "plan-nombre", textContent: plan.nombre }));
  if (datos.planes.length > 1) {
    const elegir = el("select", { id: "plan-elegir", ariaLabel: "Cambiar de plan" },
      ...datos.planes.map((p) => el("option", { value: String(p.id), textContent: p.en_curso ? `${p.nombre} (en curso)` : p.nombre,
        selected: p.id === plan.id })));
    elegir.addEventListener("change", () => { planVisto = Number(elegir.value); cargarPlan(); });
    cabecera.append(elegir);
  }
  caja.replaceChildren(
    cabecera,
    el("ul", { id: "plan-carreras", className: "tarjeta lista-plan" }, ...plan.carreras.map(filaCarrera)),
    el("h3", { className: "titulo-bloque titulo-semanas", textContent: "Semanas · previsto frente a hecho" }),
    plan.semanas.length
      ? el("ul", { id: "plan-semanas", className: "tarjeta lista-plan" }, ...plan.semanas.map(filaSemana))
      : el("p", { className: "tarjeta suave", textContent: "Este plan no tiene semanas." }),
    el("p", { className: "suave nota-plan", textContent: "Lo hecho sale de Garmin: tiempo en movimiento y km de carrera a pie. Bici, natación y gimnasio, dentro de cada semana." }),
    el("div", { className: "acciones-plan" }, botonImportar(),
      el("a", { id: "plan-bajar", className: "boton", href: `/api/plan/${plan.id}.md`, download: "", textContent: "📄 Bajar .md" })));
}

function filaCarrera(c) {
  const hecho = c.estado.startsWith("✅");
  const estado = c.principal && c.estado === "pendiente" ? "⭐" : c.estado;
  return el("li", { className: "carrera-plan" },
    el("div", {},
      el("strong", { textContent: c.fecha ? `${c.nombre} · ${diaMes(c.fecha)}` : c.nombre }),
      c.objetivo ? el("small", { className: "suave", textContent: `${c.principal ? "objetivo principal" : "objetivo"} ${c.objetivo}` }) : null),
    el("span", { className: `estado-carrera${hecho ? " bien" : ""}`, textContent: estado,
      title: c.principal ? "Carrera principal" : "" }));
}

function hechoCorto(h) {
  if (!h) return el("span", { className: "hecho-semana suave", textContent: "—" });
  const c = h.carrera;
  return el("span", { className: "hecho-semana" },
    el("strong", { textContent: c.segundos ? hm(c.segundos) : "0'" }),
    el("small", { textContent: km(c.km) }));
}

function filaSemana(s) {
  const titulo = `${rango(s.desde, s.hasta)}${s.hoy ? " · hoy" : ""}`;
  const previsto = [s.previsto_s ? `prev. ~${hm(s.previsto_s)}` : null, s.clave || null].filter(Boolean).join(" · ");
  const marcas = [];
  if (s.hecho?.carrera.tiempo_total) marcas.push("con tiempo total en alguna");
  if (s.hecho?.fuente === "plan") marcas.push("según el plan");
  const nodo = el("button", { type: "button", className: `semana${s.hoy ? " hoy" : ""}` },
    el("span", { className: "n-semana", textContent: `S${s.n}` }),
    el("span", { className: "cuerpo-semana" },
      el("strong", { textContent: titulo }),
      el("small", { className: "suave", textContent: previsto }),
      marcas.length ? el("small", { className: "marca-semana", textContent: marcas.join(" · ") }) : null,
      barra(s)),
    hechoCorto(s.hecho));
  nodo.addEventListener("click", () => abrirSemana(s.n));
  return el("li", {}, nodo);
}

function barra(s) {
  if (!s.hecho || !s.previsto_s) return null;
  const parte = Math.min(1, s.hecho.carrera.segundos / s.previsto_s);
  const caja = el("span", { className: "barra-semana", ariaHidden: "true" });
  caja.append(el("span", { style: `width:${Math.round(parte * 100)}%` }));
  return caja;
}

// -- one week ------------------------------------------------------------------------------------------------------

function cabecera(titulo, alVolver) {
  return el("div", { className: "plan-cabecera" },
    el("button", { type: "button", className: "flecha", ariaLabel: "Volver al plan", textContent: "◀", onclick: alVolver }),
    el("h2", { textContent: titulo }));
}

const volverAlPlan = () => { importacion = null; aviso(""); ver("vista"); };

async function abrirSemana(n) {
  const id = ultimo?.plan?.id;
  const mia = ++peticion;
  const { ok, datos } = await api(`/api/plan/semana/${n}${id ? `?id=${id}` : ""}`);
  if (mia !== peticion) return;
  if (!ok) { aviso(datos.error ?? "No he podido cargar esa semana."); return; }
  aviso("");
  semanaVista = n;
  pintarSemana(datos);
  ver("semana");
}

function resumenSemana(s) {
  const partes = [];
  const h = s.hecho;
  if (s.previsto_s) partes.push(el("strong", { textContent: `Prevista ~${hm(s.previsto_s)}` }));
  if (h) {
    const c = h.carrera;
    const hasta = s.hoy ? "hecha hasta hoy" : "hecha";
    partes.push(`${hasta} ${c.segundos ? hm(c.segundos) : "nada"}${c.km ? ` · ${km(c.km)}` : ""}`,
      `bici ${h.bici.n}`, `natación ${h.natacion.n}`, `gimnasio ${h.gimnasio.n}`);
    if (c.tiempo_total) partes.push("alguna sin tiempo en movimiento: cuenta su tiempo total");
    if (h.fuente === "plan") partes.push("carrera según el plan");
  } else {
    partes.push("aún no ha empezado");
  }
  const p = el("p", { className: "tarjeta resumen-semana" });
  partes.forEach((x, i) => { if (i) p.append(" · "); p.append(x); });
  return p;
}

function lineaHecha(a) {
  const datos = [a.distancia_m ? (a.distancia_m >= 1000 ? km(a.distancia_m / 1000) : `${enteras.format(a.distancia_m)} m`) : null,
    a.segundos ? hm(a.segundos) : null, a.ritmo_s_km ? mmss(a.ritmo_s_km) : null,
    a.pulso_medio ? `FC ${enteras.format(Math.round(a.pulso_medio))}` : null].filter(Boolean);
  const boton = el("button", { type: "button", className: "hecha", ariaExpanded: "false" },
    el("span", { textContent: `✓ ${[a.nombre, ...datos].join(" · ")}` }), el("span", { className: "chev", textContent: "›" }));
  const detalle = el("div", { className: "detalle-sesion", hidden: true });
  boton.addEventListener("click", () => alternarDetalle(a.actividad_id, boton, detalle));
  return [boton, detalle];
}

function filaDia(d) {
  const nombre = DIAS[fecha(d.fecha).getUTCDay()];
  const titulos = d.sesiones.map((s) => s.titulo).join(" + ");
  const nodo = el("li", { className: `dia-plan ${d.estado}` },
    el("strong", { textContent: `${nombre} ${diaMes(d.fecha)} · ${titulos || "sin sesión prevista"}` }));
  const bloques = d.sesiones.flatMap((s) => s.bloques);
  if (bloques.length) nodo.append(el("ul", { className: "bloques" }, ...bloques.map((b) => el("li", { textContent: b }))));
  (d.reloj ?? []).forEach((r, i) => nodo.append(filaReloj(d, d.sesiones[i], r)));
  for (const a of d.actividades) nodo.append(...lineaHecha(a));
  if (!d.actividades.length) {
    const texto = { no_hecho: "no hecho", hoy: "hoy: aún nada en Garmin", pendiente: "—" }[d.estado];
    nodo.append(el("span", { className: `estado-dia ${d.estado}`, textContent: texto }));
  }
  return nodo;
}

// -- unit 042: the session to the watch -------------------------------------------------------------------------

function filaReloj(d, sesion, r) {
  const caja = el("div", { className: "reloj-sesion" });
  const varias = d.sesiones.length > 1 ? ` (${sesion.titulo})` : "";
  const fila = el("div", { className: "fila-reloj" });
  if (r.estado === "al_dia") fila.append(el("span", { className: "en-garmin", textContent: `⌚ en tu Garmin${varias}` }));
  if (r.estado === "cambiada") fila.append(el("span", { className: "garmin-cambiada", textContent: `Cambiada desde que la mandaste a tu Garmin${varias}` }));
  if (r.estado !== "al_dia" && (d.estado === "hoy" || d.estado === "pendiente")) {
    const boton = el("button", { type: "button", className: "enlace-boton", textContent: "Mandar a mi Garmin" });
    boton.addEventListener("click", () => pedirTarjeta(r.id, caja, boton));
    fila.append(boton);
  }
  if (fila.childElementCount) caja.append(fila);
  return caja;
}

async function pedirTarjeta(id, caja, boton) {
  boton.disabled = true;
  const { ok, datos } = await api(`/api/plan/sesion/${id}/garmin`, {});
  boton.disabled = false;
  caja.querySelector(".tarjeta-reloj, .aviso")?.remove();
  if (!ok) { caja.append(el("p", { className: "aviso", textContent: datos.error ?? "No he podido preparar la tarjeta." })); return; }
  caja.append(tarjetaReloj(id, datos.tarjeta, caja));
}

function tarjetaReloj(id, t, caja, nota = null, fallo = null) {
  const confirmar = el("button", { type: "button", className: "principal", textContent: "Confirmar" });
  const descartar = el("button", { type: "button", textContent: "Descartar" });
  const poner = (...args) => tarjeta.replaceWith(tarjetaReloj(id, ...args));
  confirmar.addEventListener("click", async () => {
    confirmar.disabled = descartar.disabled = true;
    const { ok, estado, datos } = await api("/api/asistente/confirmar", { tarjeta: t });
    if (ok) { if (semanaVista !== null) abrirSemana(semanaVista); return; }
    if (estado === 409 && datos.tarjeta) { poner(datos.tarjeta, caja, datos.error); return; }
    poner(t, caja, null, datos.error ?? "No ha llegado a tu Garmin.");
  });
  descartar.addEventListener("click", async () => {
    confirmar.disabled = descartar.disabled = true;
    await api("/api/asistente/descartar", { tarjeta: t });
    tarjeta.remove();
  });
  const tarjeta = el("article", { className: `tarjeta tarjeta-cambio tarjeta-reloj${fallo ? " con-error" : ""}` },
    nota ? el("p", { className: "aviso", role: "status", textContent: nota }) : null,
    el("p", { className: "voy", textContent: t.texto }),
    t.donde ? el("p", { className: "suave", textContent: t.donde }) : null,
    el("ul", { className: "lineas" }, ...t.lineas.map((l) => el("li", { textContent: l }))),
    ...t.avisos.map((a) => el("p", { className: "suave aviso-cambio", textContent: a.charAt(0).toUpperCase() + a.slice(1) })),
    fallo ? el("p", { className: "aviso", role: "alert", textContent: fallo }) : null,
    fallo ? el("a", { className: "bajar-fit", href: `/api/plan/sesion/${id}.fit`, download: "", textContent: "Bajar fichero" }) : null,
    el("div", { className: "acciones" }, confirmar, descartar));
  return tarjeta;
}

function pintarSemana(s) {
  $("plan-semana").replaceChildren(
    cabecera(`S${s.n} · ${rango(s.desde, s.hasta)}`, volverAlPlan),
    resumenSemana(s),
    el("ul", { className: "tarjeta lista-plan dias" }, ...s.dias.map(filaDia)),
    el("p", { className: "suave nota-plan", textContent: "Un día pasado sin nada en Garmin dice «no hecho», nunca un 0." }));
}

async function alternarDetalle(id, boton, caja) {
  if (!caja.hidden) { caja.hidden = true; boton.setAttribute("aria-expanded", "false"); return; }
  boton.setAttribute("aria-expanded", "true");
  caja.hidden = false;
  caja.replaceChildren(el("p", { className: "suave", textContent: "Cargando las vueltas…" }));
  const { ok, datos } = await api(`/api/plan/actividad/${encodeURIComponent(id)}`);
  if (!ok) { caja.replaceChildren(el("p", { className: "aviso", textContent: datos.error ?? "No he podido cargarla." })); return; }
  pintarDetalle(caja, datos);
}

function ritmo(v) {
  if (v.velocidad_kmh !== null) return `${unDecimal.format(v.velocidad_kmh)} km/h`;
  if (v.ritmo_s_100m !== null) return `${mmss(v.ritmo_s_100m)} /100 m`;
  if (v.ritmo_s_km !== null) return `${mmss(v.ritmo_s_km)} /km`;
  return null;
}

function filaVuelta(v) {
  const datos = [v.distancia_m ? (v.distancia_m >= 1000 ? `${dosDecimales.format(v.distancia_m / 1000)} km` : `${enteras.format(v.distancia_m)} m`) : null,
    v.duracion_s ? mmss(v.duracion_s) : null, ritmo(v),
    v.pulso_medio ? `❤ ${enteras.format(Math.round(v.pulso_medio))}` : "sin pulso",
    v.cadencia ? `${enteras.format(Math.round(v.cadencia))} ppm` : null,
    v.desnivel_m ? `↗ ${enteras.format(Math.round(v.desnivel_m))} m` : null].filter(Boolean);
  return el("li", { className: "vuelta" },
    el("span", { className: "n-vuelta", textContent: String(v.n) }),
    el("span", { className: "datos-vuelta", textContent: datos.join(" · ") }),
    el("span", { className: `zona-vuelta${v.zona ? ` z${v.zona}` : ""}`, textContent: v.zona ? `Z${v.zona}` : "—" }));
}

function pintarDetalle(caja, d) {
  const a = d.actividad;
  const resumen = [a.pulso_medio ? `pulso medio ${enteras.format(Math.round(a.pulso_medio))}` : null,
    a.cadencia ? `cadencia ${enteras.format(Math.round(a.cadencia))} ppm` : null,
    a.desnivel_m ? `desnivel ${enteras.format(Math.round(a.desnivel_m))} m` : null].filter(Boolean).join(" · ");
  caja.replaceChildren(el("h3", { textContent: `Vueltas de ${a.nombre}` }),
    resumen ? el("p", { className: "suave", textContent: resumen }) : null,
    d.vueltas.length ? el("ol", { className: "vueltas" }, ...d.vueltas.map(filaVuelta))
      : el("p", { className: "suave", textContent: "Esta actividad no tiene vueltas." }));
}

// -- the import ----------------------------------------------------------------------------------------------------

async function importar(texto, fichero) {
  aviso("Leyendo tu plan… puede tardar un minuto.");
  const mia = ++peticion;
  const { ok, estado, datos } = await api("/api/plan/importar", { texto, fichero });
  if (mia !== peticion) return;
  if (!ok) {
    const reintentar = estado === 429 || estado === 400 ? null : () => importar(texto, fichero);
    aviso(datos.error ?? "No he podido leer tu plan. Inténtalo de nuevo.", reintentar);
    return;
  }
  aviso("");
  importacion = { propuesta: datos, elecciones: {} };
  for (const d of datos.diferencias) importacion.elecciones[String(d.semana)] = d.propuesta;
  pintarImportacion();
  ver("importar");
}

function textoResumen(p) {
  const r = p.resumen;
  const partes = [plural(r.carreras, "carrera", "carreras"), plural(r.semanas_hechas, "semana hecha", "semanas hechas"),
    plural(r.previstas, "prevista", "previstas"), plural(r.sesiones_clave, "sesión clave", "sesiones clave")];
  if (r.nutricion) partes.push("nutrición en carrera");
  partes.push(plural(r.incidencias, "incidencia", "incidencias"));
  if (r.comparativa) partes.push("comparativa");
  const notas = r.notas ? ` ${plural(r.notas, "nota sin sitio", "notas sin sitio")}, guardadas tal cual.` : "";
  return `leído «${p.nombre}»: ${partes.join(", ")}.${notas}`;
}

const tiempoKm = (x) => (x.segundos === null && x.km === null ? "nada"
  : [x.segundos !== null ? hm(x.segundos) : null, x.km !== null ? km(x.km) : null].filter(Boolean).join(" · "));

function filaDiferencia(d) {
  const elegida = () => importacion.elecciones[String(d.semana)];
  const opcion = (valor, texto) => {
    const b = el("button", { type: "button", textContent: texto });
    b.setAttribute("aria-pressed", String(elegida() === valor));
    b.addEventListener("click", () => { importacion.elecciones[String(d.semana)] = valor; pintarImportacion(); });
    return b;
  };
  return el("li", { className: "diferencia" },
    el("strong", { textContent: `S${d.semana} · ${rango(d.desde, d.hasta)} · total de la semana` }),
    el("p", { textContent: `Plan (Strava): ${tiempoKm(d.plan)} · Garmin: ${tiempoKm(d.garmin)}` }),
    el("div", { className: "segmentado" }, opcion("plan", "Lo del plan"), opcion("garmin", "Lo de Garmin")));
}

function pintarImportacion() {
  const p = importacion.propuesta;
  const n = p.diferencias.length;
  const confirmar = el("button", { type: "button", className: "principal grande", textContent: "Confirmar importación" });
  confirmar.addEventListener("click", () => confirmarImportacion(confirmar));
  const resumen = el("p", { className: "tarjeta resumen-importar" }, el("strong", { textContent: p.fichero || "Tu plan" }), ` ${textoResumen(p)}`);
  $("plan-importar").replaceChildren(
    cabecera("Importar plan", () => { volverAlPlan(); cargarPlan(); }),
    resumen,
    el("h3", { className: "titulo-bloque", textContent: n ? plural(n, "diferencia con Garmin", "diferencias con Garmin") : "Sin diferencias con Garmin" }),
    n ? el("ul", { className: "tarjeta lista-plan" }, ...p.diferencias.map(filaDiferencia)) : null,
    el("p", { className: "suave nota-plan", textContent: n ? "Se propone lo del plan porque viene de Strava. Hasta que confirmes no se guarda nada."
      : "Las semanas hechas coinciden con Garmin. Hasta que confirmes no se guarda nada." }),
    confirmar);
}

async function confirmarImportacion(boton) {
  boton.disabled = true;
  const { ok, datos } = await api("/api/plan/confirmar", { token: importacion.propuesta.token, elecciones: importacion.elecciones });
  boton.disabled = false;
  if (!ok) { aviso(datos.error ?? "No he podido guardar tu plan."); return; }
  importacion = null;
  planVisto = datos.plan?.id ?? null;
  aviso(`Guardado «${datos.plan?.nombre ?? "tu plan"}».`);
  $("plan-aviso").className = "aviso-bien";
  pintarPlan(datos);
  ver("vista");
}
