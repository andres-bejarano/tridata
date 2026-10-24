// The assistant's cards in the review (unit 021, REC-12). Plain ES module, no build step.
// What Claude understood as a change comes, in `cambios`, as one of three results the server worked out:
// a card «Voy a …» (before → after, the concrete day, where it stays, warnings and lines to read, and the values
// that can be corrected) with Confirmar, Corregir and Descartar; a choice between candidates (R-69); or a «no» that
// says why nothing is proposed. Nothing changes until a card is confirmed (G-37): the server redoes it with what is
// in the base then and, if it is not what was shown, answers 409 with the redone card, which is painted again
// with «Ha cambiado desde que la viste: mírala otra vez» (G-41, R-78). Each card is decided on its own;
// «Confirmar y guardar» of the review never touches them (R-72).

const CAMBIADO = "Ha cambiado desde que la viste: mírala otra vez";

function el(etiqueta, props = {}, ...hijos) {
  const nodo = Object.assign(document.createElement(etiqueta), props);
  nodo.append(...hijos.filter((h) => h !== null && h !== undefined && h !== false));
  return nodo;
}

const mayuscula = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : "");

// What the page keeps of a card for itself (`_nota`, `_corrigiendo`) never goes back to the server.
const limpia = (c) => Object.fromEntries(Object.entries(c).filter(([k]) => !k.startsWith("_")));

// «15.000» is fifteen thousand on a Spanish keyboard; «2,5» is two and a half.
function comoNumero(texto) {
  const t = String(texto).trim().replace(/\s/g, "");
  if (t === "") return null;
  if (/^\d{1,3}(\.\d{3})+$/.test(t)) return Number(t.replace(/\./g, ""));
  const n = Number(t.replace(",", "."));
  return Number.isNaN(n) ? t : n;
}

// «22-10» never breaks at its hyphen: each date goes in a span that does not wrap (the text stays the same)
const conFechas = (texto) => String(texto).split(/(\d{2}-\d{2})/).map((trozo, i) =>
  (i % 2 ? el("span", { className: "sin-corte", textContent: trozo }) : trozo));

const aTexto = (v) => (v === null || v === undefined ? "" : String(v).replace(".", ","));

/**
 * Paints `cambios` (the array of results of the review, changed in place) in `caja`.
 * `api(ruta, cuerpo)` is the page's; `alHecho(datos)` gets {hecho, dia} of a confirmed card;
 * `alDecidir()` is called whenever a card goes (confirmed, discarded or closed).
 */
export function pintarCambios(caja, cambios, opciones) {
  caja.hidden = !cambios.length;
  if (!cambios.length) { caja.replaceChildren(); return; }
  const repintar = () => pintarCambios(caja, cambios, opciones);
  const quitar = (c) => {
    const i = cambios.indexOf(c);
    if (i >= 0) cambios.splice(i, 1);
    repintar();
    opciones.alDecidir();
  };
  const poner = (c, nuevo) => {
    const i = cambios.indexOf(c);
    if (i >= 0) cambios[i] = nuevo;
    repintar();
  };
  caja.replaceChildren(
    el("h3", { className: "titulo-cambios", textContent: cambios.length === 1 ? "Cambio" : "Cambios" }),
    ...cambios.map((c) => {
      if (c.tipo === "no") return tarjetaNo(c, quitar);
      if (c.tipo === "elegir") return tarjetaElegir(c, { ...opciones, quitar, poner });
      return tarjetaCambio(c, { ...opciones, quitar, poner });
    }),
  );
}

function nota(c) {
  return c._nota ? el("p", { className: "aviso", role: "status", textContent: c._nota }) : null;
}

function tarjetaNo(c, quitar) {
  const vale = el("button", { type: "button", textContent: "Vale" });
  vale.addEventListener("click", () => quitar(c));
  return el("article", { className: "tarjeta tarjeta-no" }, nota(c),
    el("p", { className: "fuerte", textContent: mayuscula(c.texto) }),
    el("div", { className: "acciones" }, vale));
}

function tarjetaElegir(c, { api, quitar, poner }) {
  const botones = c.candidatos.map((cand) => {
    const b = el("button", { type: "button", className: "candidato", textContent: cand.texto });
    b.addEventListener("click", async () => {
      b.disabled = true;
      const { ok, datos } = await api("/api/asistente/rehacer", { tarjeta: limpia(c), eleccion: cand.eleccion });
      if (ok) poner(c, datos.tarjeta);
      else poner(c, { ...c, _nota: datos.error ?? "No se ha podido elegir." });
    });
    return b;
  });
  const descartar = el("button", { type: "button", textContent: "Descartar" });
  descartar.addEventListener("click", () => quitar(c));
  return el("article", { className: "tarjeta tarjeta-elegir" }, nota(c),
    el("p", { className: "fuerte" }, ...conFechas(c.texto)),
    el("div", { className: "candidatos" }, ...botones),
    el("div", { className: "acciones" }, descartar));
}

function tarjetaCambio(c, { api, alHecho, quitar, poner }) {
  const error = c.error ? el("p", { className: "aviso", textContent: `No se puede confirmar: ${c.error}` }) : null;
  const confirmar = el("button", { type: "button", className: `principal${c.error ? " bloqueado" : ""}`, textContent: "Confirmar" });
  const corregir = el("button", { type: "button", textContent: "Corregir", hidden: !c.campos.length });
  const descartar = el("button", { type: "button", textContent: "Descartar" });
  const botones = [confirmar, corregir, descartar];
  const ocupado = (si) => { for (const b of botones) b.disabled = si; };

  confirmar.addEventListener("click", async () => {
    if (c.error) { error.scrollIntoView({ block: "nearest" }); return; } // R-71: it does not pass, it is not confirmed
    ocupado(true);
    const { ok, estado, datos } = await api("/api/asistente/confirmar", { tarjeta: limpia(c) });
    if (ok) { alHecho(datos); quitar(c); return; }
    if (estado === 409 && datos.tarjeta) { poner(c, { ...datos.tarjeta, _nota: CAMBIADO }); return; }
    poner(c, { ...c, _nota: datos.error ?? "No se ha podido hacer." });
  });
  descartar.addEventListener("click", async () => {
    ocupado(true);
    const { ok, datos } = await api("/api/asistente/descartar", { tarjeta: limpia(c) });
    if (ok) quitar(c);
    else poner(c, { ...c, _nota: datos.error ?? "No se ha podido descartar." });
  });

  // Corregir: the values of the card in line; «Rehacer» asks the server to redo and check it (R-71)
  const entradas = c.campos.map((campo) => {
    const entrada = el("input", campo.tipo === "fecha"
      ? { type: "date", value: campo.valor ?? "" }
      : { type: "text", value: campo.tipo === "numero" ? aTexto(campo.valor) : (campo.valor ?? ""), inputMode: campo.tipo === "numero" ? "decimal" : "text", autocomplete: "off" });
    return { campo, entrada, nodo: el("label", { className: "campo" }, el("span", { textContent: campo.etiqueta }), entrada) };
  });
  const rehacer = el("button", { type: "button", className: "principal", textContent: "Rehacer" });
  rehacer.addEventListener("click", async () => {
    const valores = Object.fromEntries(entradas.map(({ campo, entrada }) =>
      [campo.clave, campo.tipo === "numero" ? comoNumero(entrada.value) : entrada.value]));
    rehacer.disabled = true;
    const { ok, datos } = await api("/api/asistente/rehacer", { tarjeta: limpia(c), valores });
    if (ok) poner(c, datos.tarjeta);
    else poner(c, { ...c, _corrigiendo: true, _nota: datos.error ?? "No se ha podido rehacer." });
  });
  const caja = el("div", { className: "corregir", hidden: !c._corrigiendo }, ...entradas.map((e) => e.nodo), rehacer);
  corregir.addEventListener("click", () => {
    caja.hidden = !caja.hidden;
    if (!caja.hidden && entradas.length) entradas[0].entrada.focus();
  });

  return el("article", { className: `tarjeta tarjeta-cambio${c.error ? " con-error" : ""}` }, nota(c),
    el("p", { className: "voy" }, ...conFechas(c.texto)),
    c.donde ? el("p", { className: "suave", textContent: c.donde }) : null,
    c.lineas.length ? el("ul", { className: "lineas" }, ...c.lineas.map((l) => el("li", { textContent: l }))) : null,
    ...c.avisos.map((a) => el("p", { className: "suave aviso-cambio", textContent: mayuscula(a) })),
    error, caja,
    el("div", { className: "acciones" }, ...botones));
}
