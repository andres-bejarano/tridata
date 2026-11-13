// The weight of today (unit 010; since unit 027 at the top of the Balance tab, which calls this module).
// Plain ES module, no build step. It ALWAYS shows today (decided by Andrés on 2026-10-21): the weight of today,
// its 7-day mean, the basal and the projection come from /api/dia without a date, and the weights of the 30 days
// that end today from /api/peso without `hasta`. It never follows the day looked at in Hoy.
// The server does every sum; this only draws.
// Unit 080 (R-223, S-74): a new weight is said to the coach («peso 72,4»), never written here; one already written
// down (today's, or one of «Últimos pesos») is changed with «Cambiar»: POST /api/peso/AAAA-MM-DD {kg}. «abc» or an
// impossible weight is not saved and the server says why; a good one fires `peso-cambiado` on `document`, so the
// Balance tab is painted again with the 7-day mean and the projection recomputed.
// Unit 017: the basal is in whole kcal. Unit 027: the Peso tab is gone; its chart is the one of Balance
// (grafica.js), so here only the card of today (with the weight goal) and «Últimos pesos» are left.

const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" }); // whole kcal (R13)
const kilos = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const ritmo = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 }); // 0,13 kg/semana must not read 0,1
const cabecera = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "short" });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" });
const semana = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric" });

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const comoFecha = (iso) => new Date(`${iso}T12:00:00`);
const mayuscula = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const sinComa = (t) => t.replace(",", "");
const diasEntre = (desde, hasta) => Math.round((comoFecha(hasta) - comoFecha(desde)) / 86_400_000);

async function leer(ruta) {
  try {
    const r = await fetch(ruta);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

// What Balance shows of the weight: `{ tarjeta, ultimos }` (`ultimos` null without any weight in 30 days), or
// `{ tarjeta }` with the warning when the PC does not answer. Today, always.
export async function cargarPeso() {
  const [dia, serie] = await Promise.all([leer("/api/dia"), leer("/api/peso")]);
  if (!serie) {
    return { tarjeta: el("p", { id: "peso-hoy", className: "aviso", textContent: "No he podido cargar tus pesos: no hay conexión con el PC." }), ultimos: null };
  }
  const hoy = serie.hasta;
  const p = dia ? dia.peso : null;
  const delDia = serie.pesos.find((x) => x.dia === hoy) || null;
  return {
    tarjeta: tarjetaHoy(hoy, delDia, serie.pesos, p, serie.objetivo),
    ultimos: serie.pesos.length ? ultimosDias(hoy, serie.pesos) : null,
  };
}

function tarjetaHoy(hoy, delDia, pesos, p, objetivo) {
  const ejemplo = `«peso ${kilos.format(delDia ? delDia.kg : (pesos.at(-1)?.kg ?? 72.4))}»`;
  let ayuda = "Si te has equivocado, cámbialo aquí.";
  if (!pesos.length) ayuda = `Aún no hay pesos. Para apuntar el de hoy, díselo al coach: ${ejemplo}.`;
  else if (!delDia) ayuda = `Aún no has apuntado el de hoy: díselo al coach, ${ejemplo}.`;
  const tarjeta = el("article", { id: "peso-hoy", className: "tarjeta peso-hoy" },
    el("div", { id: "peso-cabecera", className: "suave fuerte", textContent: `Peso de hoy · ${sinComa(cabecera.format(comoFecha(hoy)))}` }),
    el("div", { className: "cifra num" }, el("span", { id: "peso-cifra", textContent: delDia ? kilos.format(delDia.kg) : "—" }),
      el("small", { textContent: " kg" })),
    el("p", { className: "suave ayuda-peso", textContent: ayuda }));
  if (delDia) tarjeta.append(botonCambiar(delDia, "peso-cambiar", "Cambiar el peso de hoy"));
  tarjeta.append(datos(p), lineaObjetivo(p, objetivo));
  return tarjeta;
}

// -- unit 080 (R-223): change a weight already written down ------------------------------------------------------

// «Cambiar» opens, right after itself, the box to type the new value; only one box at a time.
function botonCambiar(peso, id, etiqueta) {
  const boton = el("button", { type: "button", className: "enlace-boton cambiar-peso", textContent: "Cambiar" });
  if (id) boton.id = id;
  boton.setAttribute("aria-label", etiqueta);
  boton.addEventListener("click", () => {
    document.querySelector(".editor-peso")?.remove();
    boton.after(editorPeso(peso));
  });
  return boton;
}

function editorPeso(peso) {
  const entrada = el("input", { type: "text", inputMode: "decimal", autocomplete: "off", value: kilos.format(peso.kg) });
  const aviso = el("p", { className: "aviso", role: "alert", hidden: true });
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const caja = el("div", { className: "editor-peso" },
    el("label", { className: "campo" }, el("span", { textContent: `Peso del ${diaMes.format(comoFecha(peso.dia))} (kg)` }), entrada),
    aviso, el("div", { className: "acciones" }, guardar, cancelar));
  cancelar.addEventListener("click", () => caja.remove());
  guardar.addEventListener("click", async () => {
    guardar.disabled = true;
    aviso.hidden = true;
    let r = null;
    let respuesta = {};
    try {
      r = await fetch(`/api/peso/${peso.dia}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kg: entrada.value.trim() }) });
      respuesta = await r.json().catch(() => ({}));
    } catch { /* the PC is off */ }
    guardar.disabled = false;
    if (!r || !r.ok) {
      aviso.textContent = r ? (respuesta.error ?? "No se ha podido cambiar el peso.") : "No hay conexión con el PC: no se ha cambiado nada.";
      aviso.hidden = false;
      entrada.focus();
      return;
    }
    caja.remove();
    document.dispatchEvent(new CustomEvent("peso-cambiado", { detail: respuesta.peso }));
  });
  setTimeout(() => { entrada.focus(); entrada.select(); }, 0);
  return caja;
}

function datos(p) {
  const media = p && p.media_7_dias;
  const b = p && p.basal;
  const caja = (id, titulo, valor, nota) => el("div", { id, className: "tarjeta" },
    el("span", { textContent: titulo }), el("strong", { className: "num", textContent: valor }), el("span", { textContent: nota }));
  const dias = media ? `${media.dias_con_peso} ${media.dias_con_peso === 1 ? "día" : "días"} con peso` : "sin pesos en 7 días";
  const basal = b && b.kcal !== null ? numero.format(b.kcal) : "—";
  const deBasal = !b || b.kcal === null ? "sin dato" : b.origen === "peso" ? "kcal, con tu peso" : "kcal, el de Ajustes";
  return el("div", { className: "datos" },
    caja("peso-media", "Media de 7 días", media ? `${kilos.format(media.kg)} kg` : "—", dias),
    caja("peso-basal", "Basal", basal, deBasal));
}

// The weight goal and what is left, inside the card (it was a card of its own in the Peso tab).
function lineaObjetivo(p, objetivo) {
  const y = p && p.proyeccion;
  if (!objetivo && !y) {
    return el("div", { id: "peso-objetivo", className: "objetivo-peso" },
      el("p", { className: "linea-objetivo-peso", textContent: "Sin objetivo de peso" }),
      el("p", { className: "suave", textContent: "Ponlo en la pestaña Ajustes." }));
  }
  const kg = y ? y.objetivo_kg : objetivo.kg;
  const fecha = y ? y.fecha : objetivo.fecha;
  let texto = p && p.proyeccion_texto ? `${mayuscula(p.proyeccion_texto)}.` : "";
  if (y && y.faltan_kg !== null) {
    const verbo = y.faltan_kg >= 0 ? "Te faltan" : "Te sobran";
    const cuanto = y.kg_por_semana === null ? "la fecha ya ha pasado"
      : `hace falta ${ritmo.format(Math.abs(y.kg_por_semana))} kg por semana`;
    texto = `${verbo} ${kilos.format(Math.abs(y.faltan_kg))} kg: ${cuanto}.`;
  }
  return el("div", { id: "peso-objetivo", className: "objetivo-peso" },
    el("p", { className: "linea-objetivo-peso" }, "Objetivo ",
      el("span", { textContent: `${kilos.format(kg)} kg el ${diaMes.format(comoFecha(fecha))}` })),
    texto ? el("p", { className: "suave", textContent: texto }) : null);
}

// The 7 most recent weights of those 30 days, newest first.
function ultimosDias(hoy, pesos) {
  const nombre = (iso) => {
    const atras = diasEntre(iso, hoy);
    if (atras === 0) return "Hoy";
    if (atras === 1) return "Ayer";
    return mayuscula(semana.format(comoFecha(iso)));
  };
  return el("article", { id: "peso-ultimos", className: "tarjeta lista" }, ...pesos.slice(-7).reverse().map((p) => el("div", { className: "fila-peso" },
    el("div", { className: "fila" },
      el("div", { className: "que" }, el("strong", { textContent: nombre(p.dia) }), p.nota ? el("span", { textContent: p.nota }) : null),
      el("div", { className: "kcal num", textContent: `${kilos.format(p.kg)} kg` })),
    botonCambiar(p, null, `Cambiar el peso de ${nombre(p.dia).toLowerCase()}`))));
}
