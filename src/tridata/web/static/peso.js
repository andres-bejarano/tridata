// The Peso tab (unit 010). Plain ES module, no build step, no chart library.
// It ALWAYS shows today (decided by Andrés on 2026-10-21): the weight of today, its 7-day mean,
// the basal and the projection come from /api/dia without a date, and the chart of the 30 days
// that end today from /api/peso without `hasta`. It never follows the day looked at in Hoy.
// The server does every sum; this only draws. Weights are logged by saying or writing «peso 72,4».
// Unit 017: the chart is the one of grafica.js (also in Balance), and the basal is in whole kcal.

import { graficaPesoDeficit, leyendaPesoDeficit } from "./grafica.js";

const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" }); // whole kcal (R13)
const kilos = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const ritmo = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 }); // 0,13 kg/semana must not read 0,1
const cabecera = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "short" });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" });
const semana = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric" });
const DIAS_EN_LA_GRAFICA = 30;

let turno = 0; // drops a late answer when the tab is opened twice quickly

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

// The inner width of a chart card in `contenedor` (border 1 + padding 4 on each side), so 1 unit = 1 px.
export const anchoGrafica = (contenedor) => (contenedor && contenedor.clientWidth ? contenedor.clientWidth - 10 : undefined);

async function leer(ruta) {
  try {
    const r = await fetch(ruta);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

// Draws the tab into `contenedor`. Today, always.
export async function pintarPeso(contenedor) {
  const yo = ++turno;
  const [dia, serie] = await Promise.all([leer("/api/dia"), leer("/api/peso")]);
  if (yo !== turno) return;
  if (!serie) {
    contenedor.replaceChildren(el("p", { className: "aviso", textContent: "No he podido cargar tus pesos: no hay conexión con el PC." }));
    return;
  }
  const hoy = serie.hasta;
  const p = dia ? dia.peso : null;
  const delDia = serie.pesos.find((x) => x.dia === hoy) || null;
  const nodos = [tarjetaHoy(hoy, delDia, serie.pesos), datos(p)];
  if (serie.pesos.length) nodos.push(grafica(serie, anchoGrafica(contenedor)));
  nodos.push(tarjetaObjetivo(p, serie.objetivo));
  if (serie.pesos.length) nodos.push(el("h2", { textContent: "Últimos días" }), ultimosDias(hoy, serie.pesos));
  contenedor.replaceChildren(...nodos);
}

function tarjetaHoy(hoy, delDia, pesos) {
  const ejemplo = `«peso ${kilos.format(delDia ? delDia.kg : (pesos.at(-1)?.kg ?? 72.4))}»`;
  let ayuda = `Para apuntarlo, di o escribe ${ejemplo}.`;
  if (!pesos.length) ayuda = `Aún no hay pesos. Para apuntar el de hoy, di o escribe ${ejemplo}.`;
  else if (!delDia) ayuda = `Aún no has apuntado el de hoy: di o escribe ${ejemplo}.`;
  return el("article", { className: "tarjeta peso-hoy" },
    el("div", { id: "peso-cabecera", className: "suave fuerte", textContent: `Hoy · ${sinComa(cabecera.format(comoFecha(hoy)))}` }),
    el("div", { className: "cifra num" }, el("span", { id: "peso-cifra", textContent: delDia ? kilos.format(delDia.kg) : "—" }),
      el("small", { textContent: " kg" })),
    el("p", { className: "suave", textContent: ayuda }));
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

// The 30 days that end today (unit 017): the chart of grafica.js, kg on top and, below, the balance of each
// day with the food closed (deficit down, surplus up), the Monday dates and the legend.
function grafica(serie, ancho) {
  const desde = (() => { const d = comoFecha(serie.hasta); d.setDate(d.getDate() - (DIAS_EN_LA_GRAFICA - 1)); return d.toISOString().slice(0, 10); })();
  const tarjeta = el("article", { id: "peso-grafica", className: "tarjeta grafica" },
    el("div", { className: "dato-fila cabeza-grafica" }, el("strong", { textContent: "Últimos 30 días" })));
  tarjeta.setAttribute("aria-label", "Peso y balance de los últimos 30 días");
  tarjeta.append(graficaPesoDeficit({ pesos: serie.pesos, medias: serie.medias, barras: serie.barras || [], objetivo: serie.objetivo,
    desde, hasta: serie.hasta, agrupado: "dia" }, ancho), leyendaPesoDeficit(serie.objetivo));
  return tarjeta;
}

function tarjetaObjetivo(p, objetivo) {
  const y = p && p.proyeccion;
  if (!objetivo && !y) {
    return el("article", { id: "peso-objetivo", className: "tarjeta bloque-objetivo" },
      el("strong", { textContent: "Sin objetivo de peso" }), el("p", { className: "suave", textContent: "Ponlo en la pestaña Ajustes." }));
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
  return el("article", { id: "peso-objetivo", className: "tarjeta bloque-objetivo" },
    el("strong", { textContent: `Objetivo ${kilos.format(kg)} kg el ${diaMes.format(comoFecha(fecha))}` }),
    el("p", { className: "suave", textContent: texto }));
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
