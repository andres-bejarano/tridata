// The weight of today (unit 010; since unit 027 at the top of the Balance tab, which calls this module).
// Plain ES module, no build step. It ALWAYS shows today (decided by Andrés on 2026-10-21): the weight of today,
// its 7-day mean, the basal and the projection come from /api/dia without a date, and the weights of the 30 days
// that end today from /api/peso without `hasta`. It never follows the day looked at in Hoy.
// The server does every sum; this only draws. Weights are logged by saying or writing «peso 72,4».
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
  let ayuda = `Para apuntarlo, di o escribe ${ejemplo}.`;
  if (!pesos.length) ayuda = `Aún no hay pesos. Para apuntar el de hoy, di o escribe ${ejemplo}.`;
  else if (!delDia) ayuda = `Aún no has apuntado el de hoy: di o escribe ${ejemplo}.`;
  return el("article", { id: "peso-hoy", className: "tarjeta peso-hoy" },
    el("div", { id: "peso-cabecera", className: "suave fuerte", textContent: `Peso de hoy · ${sinComa(cabecera.format(comoFecha(hoy)))}` }),
    el("div", { className: "cifra num" }, el("span", { id: "peso-cifra", textContent: delDia ? kilos.format(delDia.kg) : "—" }),
      el("small", { textContent: " kg" })),
    el("p", { className: "suave ayuda-peso", textContent: ayuda }),
    datos(p),
    lineaObjetivo(p, objetivo));
}

function datos(p) {
  const media = p && p.media_7_dias;
  const b = p && p.basal;
  const caja = (id, titulo, valor, nota) => el("div", { id, className: "tarjeta" },
    el("span", { textContent: titulo }), el("strong", { className: "num", textContent: valor }), el("span", { textContent: nota }));
  const dias = media ? `${media.dias_con_peso} ${media.dias_con_peso === 1 ? "día" : "días"} con peso` : "sin pesos en 7 días";
  const basal = b && b.kcal !== null ? numero.format(b.kcal) : "—";
  const deBasal = !b || b.kcal === null ? "sin dato" : b.origen === "garmin" ? "kcal, de tu Garmin" // unit 060
    : b.origen === "peso" ? "kcal, con tu peso" : "kcal, el de Ajustes";
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
  return el("article", { id: "peso-ultimos", className: "tarjeta lista" }, ...pesos.slice(-7).reverse().map((p) => el("div", { className: "fila" },
    el("div", { className: "que" }, el("strong", { textContent: nombre(p.dia) }), p.nota ? el("span", { textContent: p.nota }) : null),
    el("div", { className: "kcal num", textContent: `${kilos.format(p.kg)} kg` }))));
}
