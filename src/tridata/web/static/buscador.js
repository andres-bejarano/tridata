// Unit 019: the search box of the saved recipes and ingredients. Plain ES module, no build step.
// One piece for «Añadir a mano» and the review cards (app.js) and the lines of a recipe (recetas.js, only
// ingredients). It is a field with its own list under it (not a <datalist>, which iPhone's Safari does not filter
// by a piece of the word): it matches name or alias without capitals or accents, by a piece («lent», «bote»),
// first what starts with what is written; empty and touched, the whole list in «Recetas» / «Ingredientes». A
// recipe with a doubtful ingredient or to review shows but cannot be chosen. Touch, arrows + Enter, Escape.
// The list is what GET /api/recetas brings, asked once (and again from Recetas); without the PC it is empty
// and the field is written by hand. `cuenta()` (from unit 013) shows the kcal of what is chosen, the same in
// every screen; only to SHOW them: the server works them out again with what is saved when it is logged (R-57).

const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, useGrouping: "always" });
const enteras = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });
export const kcalEnteras = (n) => enteras.format(Math.round(n) === 0 ? 0 : n);
const redondear = (n) => Math.round(n * 10) / 10;

// Entera · ½ · ⅓ · ¼ (decided: enough for now; other fractions only dictating)
export const FRACCIONES = [[1, "Entera"], [0.5, "½"], [1 / 3, "⅓"], [0.25, "¼"]];

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined && hijo !== false) nodo.append(hijo);
  return nodo;
}

// As `normalizar` on the server: lower case, no accents, single spaces.
export const normalizar = (texto) => String(texto ?? "").normalize("NFKD").replace(/[^\x20-\x7e]/g, "")
  .toLowerCase().split(/\s+/).filter(Boolean).join(" ");

// -- what is saved --------------------------------------------------------------------------------

let guardado = { recetas: [], fijos: [] };
let cargado = false;
let pidiendo = null;

// recetas.js gives it what it gets, so the list is always the one of the tab.
export function ponerGuardado(datos) {
  guardado = { recetas: datos.recetas ?? [], fijos: datos.fijos ?? [] };
  cargado = true;
}

export function cargarGuardado({ otraVez = false } = {}) {
  if (cargado && !otraVez) return Promise.resolve(guardado);
  if (!pidiendo) {
    pidiendo = fetch("/api/recetas")
      .then((r) => (r.ok ? r.json() : null))
      .then((datos) => { if (datos) ponerGuardado(datos); return guardado; })
      .catch(() => guardado) // without the PC: nothing to propose, the field is written by hand
      .finally(() => { pidiendo = null; });
  }
  return pidiendo;
}

export function cosaGuardada(elegido) {
  if (!elegido || typeof elegido !== "object") return null;
  const lista = elegido.tipo === "receta" ? guardado.recetas : guardado.fijos;
  return lista.find((c) => c.id === elegido.id) ?? null;
}

// -- the kcal of what is chosen (G-31, G-32), only to show them ------------------------------------

const gramosPosibles = (receta) => Boolean(receta.en_uso && (receta.en_uso.kcal_100g !== null || receta.en_uso.kcal_100g_aprox !== null));
export const unidadPropia = (fijo) => (fijo.por === "unidad" ? "unidad" : "g");

// `unidad`: null is the thing's own (grams, or units for a fixed food per unit); "receta" a fraction of it.
export function cuenta(tipo, cosa, n, unidad = null) {
  if (tipo === "fijo") {
    const propia = unidadPropia(cosa);
    if (unidad && unidad !== propia) return { problema: `Está guardado por ${propia === "g" ? "100 g: pon gramos" : "unidad: pon unidades"}.` };
    return { kcal: redondear(propia === "unidad" ? n * cosa.kcal : n * cosa.kcal / 100), provisional: false };
  }
  const v = cosa.en_uso;
  if (cosa.por_revisar) return { problema: "Corrige la receta en la pestaña Recetas: está por revisar." };
  if (!v || v.estado === "sin_kcal") return { problema: "Corrige la receta: tiene un ingrediente dudoso y no se puede calcular." };
  if (unidad === "receta") return { kcal: redondear(n * v.kcal_total), provisional: v.estado !== "definitiva" };
  if (v.kcal_100g !== null) return { kcal: redondear(n * v.kcal_100g / 100), provisional: false };
  if (v.kcal_100g_aprox !== null) return { kcal: redondear(n * v.kcal_100g_aprox / 100), provisional: true };
  return { problema: "Sin pesar: pésala o pon sus kcal/100 g aproximadas." };
}

// What the server will write as origin (Recetario._calcular), to show it before saving.
export function origenDe(tipo, cosa) {
  if (tipo === "fijo") return `de tu ingrediente «${cosa.nombre}»`;
  const tanda = cosa.versiones && cosa.versiones.length > 1 && cosa.en_uso ? ` (tanda ${cosa.en_uso.numero})` : "";
  return `de tu receta «${cosa.nombre}»${tanda}`;
}

// R8 (R-55): what was logged before the rename is not rewritten; the screen reads it with the new name.
export const leerOrigen = (origen) => (origen ? origen.replace(/^de tu alimento fijo /, "de tu ingrediente ") : origen);

// «la receta entera», «½ receta»… (a fraction dictated that is not one of these: «0,75 receta»)
export function textoReceta(n) {
  const f = FRACCIONES.find(([v]) => Math.abs(v - n) < 1e-6);
  if (f) return f[0] === 1 ? "la receta entera" : `${f[1]} receta`;
  return `${numero.format(n)} ${n > 1 ? "recetas" : "receta"}`;
}

// -- the list -------------------------------------------------------------------------------------

function describir(tipo, cosa) {
  if (tipo === "fijo") return { detalle: `Ingrediente · ${numero.format(cosa.kcal)} kcal/${cosa.por === "unidad" ? "unidad" : "100 g"}`, elegible: true };
  const v = cosa.en_uso;
  if (cosa.por_revisar || !v || v.estado === "sin_kcal") return { detalle: "Receta · corrígela en Recetas", elegible: false };
  if (v.estado === "definitiva") return { detalle: `Receta · ${numero.format(v.kcal_100g)} kcal/100 g`, elegible: true };
  const aprox = v.kcal_100g_aprox !== null ? ` · ~${numero.format(v.kcal_100g_aprox)} kcal/100 g` : "";
  return { detalle: `Receta · sin pesar · ${kcalEnteras(v.kcal_total)} kcal entera${aprox}`, elegible: true };
}

function candidatos(texto, solo) {
  const q = normalizar(texto);
  const todos = [
    ...(solo === "fijo" ? [] : guardado.recetas.map((cosa) => ({ tipo: "receta", cosa }))),
    ...(solo === "receta" ? [] : guardado.fijos.map((cosa) => ({ tipo: "fijo", cosa }))),
  ];
  const salida = [];
  for (const c of todos) {
    const nombres = [c.cosa.nombre, ...(c.cosa.alias ?? [])].map(normalizar);
    if (q && !nombres.some((n) => n.includes(q))) continue;
    salida.push({ ...c, ...describir(c.tipo, c.cosa), empieza: !q || nombres.some((n) => n.startsWith(q)) });
  }
  if (!q) return salida; // the whole list, recipes then ingredients (each as the server orders them)
  const peso = (c) => [c.empieza ? 0 : 1, c.elegible ? 0 : 1, c.tipo === "receta" ? 0 : 1];
  return salida.sort((a, b) => {
    const [x, y] = [peso(a), peso(b)];
    for (let i = 0; i < x.length; i += 1) if (x[i] !== y[i]) return x[i] - y[i];
    return a.cosa.nombre.localeCompare(b.cosa.nombre, "es");
  });
}

let cuantos = 0;

// The search box on `entrada` (an <input> already in its label). Returns the list, to put right under the field.
// `alElegir({ tipo, cosa })` gets what was chosen; the field then says its name. A read-only field (something
// already chosen) does not open it.
export function buscador({ entrada, solo = null, alElegir }) {
  const id = `buscador-${++cuantos}`;
  const lista = el("ul", { id, className: "desplegable", hidden: true });
  lista.setAttribute("role", "listbox");
  entrada.setAttribute("role", "combobox");
  entrada.setAttribute("aria-autocomplete", "list");
  entrada.setAttribute("aria-expanded", "false");
  entrada.setAttribute("aria-controls", id);
  entrada.autocomplete = "off";
  let opciones = [];
  let activa = -1;

  const cerrar = () => {
    lista.hidden = true;
    lista.replaceChildren();
    opciones = [];
    activa = -1;
    entrada.setAttribute("aria-expanded", "false");
    entrada.removeAttribute("aria-activedescendant");
  };

  const marcarActiva = () => {
    opciones.forEach((o, i) => {
      o.nodo.classList.toggle("activa", i === activa);
      o.nodo.setAttribute("aria-selected", String(i === activa));
    });
    if (activa >= 0) {
      entrada.setAttribute("aria-activedescendant", opciones[activa].nodo.id);
      opciones[activa].nodo.scrollIntoView({ block: "nearest" });
    } else entrada.removeAttribute("aria-activedescendant");
  };

  const elegir = (i) => {
    const o = opciones[i];
    if (!o || !o.elegible) return;
    entrada.value = o.cosa.nombre;
    cerrar();
    alElegir({ tipo: o.tipo, cosa: o.cosa });
  };

  const pintar = () => {
    if (entrada.readOnly || document.activeElement !== entrada) { cerrar(); return; }
    const vacio = !normalizar(entrada.value);
    const encontrados = candidatos(entrada.value, solo);
    if (!encontrados.length) { cerrar(); return; }
    const estabaCerrada = lista.hidden;
    const nodos = [];
    let grupo = null;
    opciones = encontrados.map((c, i) => {
      if (vacio && c.tipo !== grupo) {
        grupo = c.tipo;
        const titulo = el("li", { className: "grupo", textContent: c.tipo === "receta" ? "Recetas" : "Ingredientes" });
        titulo.setAttribute("role", "presentation");
        nodos.push(titulo);
      }
      const nodo = el("li", { id: `${id}-${i}`, className: c.elegible ? "opcion" : "opcion no-elegible" },
        el("strong", { textContent: c.cosa.nombre }), el("span", { textContent: c.detalle }));
      nodo.setAttribute("role", "option");
      nodo.setAttribute("aria-selected", "false");
      if (!c.elegible) nodo.setAttribute("aria-disabled", "true");
      nodo.addEventListener("click", () => elegir(i));
      nodos.push(nodo);
      return { ...c, nodo };
    });
    activa = -1;
    lista.replaceChildren(...nodos);
    lista.hidden = false;
    entrada.setAttribute("aria-expanded", "true");
    marcarActiva();
    // with the keyboard open (R7) the field goes up, so its list fits under it and above the box at the bottom
    if (estabaCerrada) (entrada.closest("label") ?? entrada).scrollIntoView({ block: "start" });
  };

  const abrir = () => {
    if (entrada.readOnly) return;
    if (cargado) { pintar(); return; }
    cargarGuardado().then(pintar);
  };

  entrada.addEventListener("focus", abrir);
  entrada.addEventListener("click", () => { if (lista.hidden) abrir(); });
  entrada.addEventListener("input", abrir);
  entrada.addEventListener("blur", cerrar);
  entrada.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape") { if (!lista.hidden) { evento.preventDefault(); cerrar(); } return; }
    if (evento.key === "ArrowDown" || evento.key === "ArrowUp") {
      if (lista.hidden) { abrir(); return; }
      evento.preventDefault();
      const paso = evento.key === "ArrowDown" ? 1 : -1;
      activa = Math.min(Math.max(activa + paso, 0), opciones.length - 1);
      marcarActiva();
      return;
    }
    if (evento.key === "Enter" && !lista.hidden && activa >= 0) { evento.preventDefault(); elegir(activa); }
  });
  // touching the list does not take the focus away from the field (it would close it before the tap)
  for (const tipo of ["pointerdown", "mousedown"]) lista.addEventListener(tipo, (evento) => evento.preventDefault());
  return lista;
}

// -- after choosing: only how much ------------------------------------------------------------------

// The buttons «Entera · ½ · ⅓ · ¼». `alElegir(f)`; `marcar(f | null)` shows which one is pressed.
export function fracciones(alElegir) {
  const botones = FRACCIONES.map(([f, texto]) => {
    const boton = el("button", { type: "button", className: "fraccion", textContent: texto });
    boton.setAttribute("aria-pressed", "false");
    boton.addEventListener("click", () => { marcar(f); alElegir(f); });
    return { f, boton };
  });
  function marcar(valor) {
    for (const { f, boton } of botones) boton.setAttribute("aria-pressed", String(valor !== null && Math.abs(f - valor) < 1e-6));
  }
  const caja = el("div", { className: "fracciones" }, ...botones.map((b) => b.boton));
  caja.setAttribute("role", "group");
  caja.setAttribute("aria-label", "Cuánto de la receta");
  return { caja, marcar };
}

const comoNumero = (v) => (String(v ?? "").trim() === "" ? null : Number(String(v).replace(",", ".")));

// What replaces quantity, unit and kcal once something is chosen (Hoy and the review): a recipe by fractions or,
// if it can be, by grams; an ingredient in grams or units. The kcal at once, whole. `alCambiar({ cantidad,
// unidad, kcal, provisional })` on every change; «Cambiar» calls `alSoltar()`.
export function cajaElegida({ tipo, cosa, cantidad = null, unidad = null, alCambiar = () => {}, alSoltar }) {
  const estado = { cantidad, unidad };
  const kcal = el("p", { className: "kcal-elegido num" });
  kcal.setAttribute("aria-live", "polite");
  const partes = [el("p", { className: "origen-elegido", textContent: origenDe(tipo, cosa) })];
  let entrada = null;
  let fr = null;
  const conGramos = tipo === "fijo" || gramosPosibles(cosa);
  const unidadCantidad = tipo === "fijo" ? unidadPropia(cosa) : "g";
  if (tipo === "receta") {
    fr = fracciones((f) => { estado.cantidad = f; estado.unidad = "receta"; if (entrada) entrada.value = ""; recalcular(); });
    partes.push(fr.caja);
    if (!conGramos) partes.push(el("p", { className: "suave sin-pesar", textContent: "sin pesar: apúntala entera o por fracciones, o pésala" }));
  }
  if (conGramos) {
    entrada = el("input", { type: "text", inputMode: "decimal", autocomplete: "off" });
    const etiqueta = el("label", { className: "campo text" }, el("span", { textContent: unidadCantidad === "unidad" ? "Unidades" : "Gramos" }), entrada);
    entrada.addEventListener("input", () => {
      estado.cantidad = comoNumero(entrada.value);
      estado.unidad = unidadCantidad;
      if (fr) fr.marcar(null);
      recalcular();
    });
    partes.push(etiqueta);
  }
  const cambiar = el("button", { type: "button", className: "cambiar", textContent: "Cambiar" });
  cambiar.addEventListener("click", () => alSoltar());
  const caja = el("div", { className: "elegida" }, ...partes, el("div", { className: "fila-elegida" }, kcal, cambiar));

  function leer() {
    const n = estado.cantidad;
    if (n === null || Number.isNaN(n) || n <= 0) return { cantidad: n, unidad: estado.unidad, kcal: null, provisional: false };
    const c = cuenta(tipo, cosa, n, estado.unidad);
    return { cantidad: n, unidad: estado.unidad, kcal: c.problema ? null : c.kcal, provisional: Boolean(c.provisional), problema: c.problema };
  }
  function recalcular() {
    const c = leer();
    kcal.textContent = c.kcal === null ? "— kcal" : `${kcalEnteras(c.kcal)} kcal${c.provisional ? " · provisional" : ""}`;
    kcal.title = c.problema ?? "";
    alCambiar(c);
  }
  // what came (a repaint, or the quantity of the card when it fits)
  if (estado.unidad === "receta" && fr) fr.marcar(estado.cantidad);
  else if (estado.cantidad !== null && entrada && estado.unidad === unidadCantidad) entrada.value = String(estado.cantidad).replace(".", ",");
  else { estado.cantidad = null; estado.unidad = null; }
  const c = leer();
  kcal.textContent = c.kcal === null ? "— kcal" : `${kcalEnteras(c.kcal)} kcal${c.provisional ? " · provisional" : ""}`;
  return { caja, leer, foco: () => (entrada ?? fr.caja.firstElementChild).focus() };
}
