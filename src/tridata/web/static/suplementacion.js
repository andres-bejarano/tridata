// Unit 082 (REC-34, R-226, R-227, R-228): the block «Suplementación» on top of Nutrición › Día, apart from the meals.
// Its coffees and B12 of the day by hour, «N mg de cafeína · última: HH:MM» and, on each coffee, its share of the milk
// of the day («+125 ml»; «solo» when it had none). None of it has kcal (G-60): the milk is a food of the day.
// Tapping a dose opens its card (hour, form, mg, «solo»; delete asks first); «Añadir» opens an empty one. Without
// doses the block is folded: only its title and «Añadir». Plain ES module, no build step; app.js gives it `api` and
// `alCambiar(dia)` (the day painted again) so there is no import back.

const FORMAS = [["expreso", "Expreso"], ["doble", "Doble"], ["ingles", "Inglés"], ["italiana", "Italiana"],
  ["soluble", "Soluble"]];
const mgTexto = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
let abierta = null; // { clave: "toma:N" | "nueva:AAAA-MM-DD", nodo } kept between repaints (what is typed stays)

function el(etiqueta, props = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  Object.assign(nodo, props);
  for (const hijo of hijos) if (hijo !== null && hijo !== undefined) nodo.append(hijo);
  return nodo;
}

const mayuscula = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const horaDeAhora = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

function campo(etiqueta, entrada) {
  const nodo = el("label", { className: "campo" }, el("span", { textContent: etiqueta }), entrada);
  nodo.entrada = entrada;
  return nodo;
}

function selector(etiqueta, opciones, valor) {
  const select = el("select");
  for (const [clave, nombre] of opciones) select.append(el("option", { value: clave, textContent: nombre }));
  if (valor) select.value = valor;
  // a <label> wrapping a <select> takes the text of its options as name: aria-label keeps it «Forma»
  select.setAttribute("aria-label", etiqueta);
  return campo(etiqueta, select);
}

function filaToma(t, alTocar) {
  const mg = t.mg === null || t.mg === undefined ? "" : `${t.mg_estimado ? "≈" : ""}${mgTexto.format(t.mg)} mg`;
  const leche = t.que !== "cafe" ? "" : t.solo ? "solo" : t.leche_ml ? `+${mgTexto.format(t.leche_ml)} ml` : "";
  const boton = el("button", { type: "button", className: "toma" },
    el("span", { className: "toma-hora num", textContent: t.hora ?? "—" }),
    el("span", { className: "toma-que", textContent: mayuscula(t.texto) }),
    el("span", { className: "toma-mg num", textContent: mg }),
    el("span", { className: "toma-leche num", textContent: leche }));
  boton.dataset.id = t.id;
  boton.addEventListener("click", alTocar);
  return el("li", {}, boton);
}

// The card of a dose: a change (`t`) or a new one (`t` null, on `fecha`). `enviar` saves; `borrar` only for a change.
function fichaToma(t, fecha, { api, alCambiar, cerrar }) {
  const nueva = t === null;
  const que = selector("Qué", [["cafe", "Café"], ["b12", "B12"]], nueva ? "cafe" : t.que);
  const forma = selector("Forma", FORMAS, nueva ? "doble" : t.forma);
  const hora = campo("Hora", el("input", { type: "time", value: nueva ? horaDeAhora() : t.hora ?? "" }));
  const mg = campo("mg de cafeína", el("input", { type: "number", min: "0", step: "1", inputMode: "numeric",
    value: nueva || t.mg === null ? "" : String(t.mg) }));
  const solo = el("input", { type: "checkbox", checked: !nueva && Boolean(t.solo) });
  const soloCampo = el("label", { className: "campo-solo" }, solo, el("span", { textContent: "Solo (sin leche)" }));
  const dosis = campo("Dosis", el("input", { type: "text", value: nueva ? "" : t.dosis ?? "", placeholder: "opcional" }));
  const deCafe = [forma, mg, soloCampo];
  const verQue = () => {
    const esCafe = que.entrada.value === "cafe";
    for (const nodo of deCafe) nodo.hidden = !esCafe;
    dosis.hidden = esCafe;
  };
  que.entrada.addEventListener("change", verQue);
  verQue();
  const aviso = el("p", { className: "aviso", hidden: true });
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const acciones = el("div", { className: "acciones" }, guardar, cancelar);
  cancelar.addEventListener("click", cerrar);
  guardar.addEventListener("click", async () => {
    if (!hora.entrada.value) { avisar("Pon la hora."); return; }
    const esCafe = que.entrada.value === "cafe";
    const cuerpo = { hora: hora.entrada.value };
    if (nueva) Object.assign(cuerpo, { dia: fecha, que: que.entrada.value });
    if (esCafe) {
      Object.assign(cuerpo, { forma: forma.entrada.value, solo: solo.checked });
      const escrito = mg.entrada.value.trim();
      if (nueva ? escrito !== "" : escrito !== (t.mg === null ? "" : String(t.mg))) cuerpo.mg = escrito === "" ? null : Number(escrito);
    } else {
      cuerpo.dosis = dosis.entrada.value.trim() || null;
    }
    guardar.disabled = true;
    avisar("");
    const { ok, datos } = await api(nueva ? "/api/toma" : `/api/toma/${t.id}`, cuerpo);
    guardar.disabled = false;
    if (!ok) { avisar(`No se ha podido guardar. ${datos.error ?? ""}`.trim()); return; }
    abierta = null;
    alCambiar(datos.dia);
  });
  const tarjeta = el("article", { className: "tarjeta ficha-toma" + (nueva ? " nueva" : "") },
    ...(nueva ? [que] : []), el("div", { className: "fila" }, hora, forma, mg), soloCampo, dosis, aviso, acciones);
  if (nueva) return tarjeta;
  const borrar = el("button", { type: "button", className: "peligro", textContent: "Borrar" });
  const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
    el("p", { textContent: `¿Borrar ${t.texto} de las ${t.hora}?` }));
  const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, borrar" });
  const no = el("button", { type: "button", textContent: "No" });
  pregunta.append(el("div", { className: "acciones" }, si, no));
  const preguntar = (si_) => { pregunta.hidden = !si_; acciones.hidden = si_; borrar.hidden = si_; };
  borrar.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
  no.addEventListener("click", () => preguntar(false));
  si.addEventListener("click", async () => {
    si.disabled = true;
    let r;
    try {
      const respuesta = await fetch(`/api/toma/${t.id}`, {
        method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmado: true }),
      });
      r = { ok: respuesta.ok, datos: await respuesta.json().catch(() => ({})) };
    } catch {
      r = { ok: false, datos: { error: "No hay conexión con el PC. Inténtalo de nuevo." } };
    }
    si.disabled = false;
    if (!r.ok) { preguntar(false); avisar(`No se ha podido borrar. ${r.datos.error ?? ""}`.trim()); return; }
    abierta = null;
    alCambiar(r.datos.dia);
  });
  tarjeta.append(borrar, pregunta);
  return tarjeta;
}

function conservada(clave, crear) {
  if (!abierta || abierta.clave !== clave) abierta = { clave, nodo: crear() };
  return abierta.nodo;
}

// Paints `#suplementacion` for `dia` (the JSON of /api/dia). `api(ruta, cuerpo)` and `alCambiar(dia)` are app.js's.
export function pintarSuplementacion(dia, { api, alCambiar }) {
  const bloque = document.getElementById("suplementacion");
  if (!bloque) return;
  const s = dia.suplementacion ?? { tomas: [], cafeina_mg: 0, ultima: null, leche: null };
  const repintar = () => pintarSuplementacion(dia, { api, alCambiar });
  const cerrar = () => { abierta = null; repintar(); };
  const opciones = { api, alCambiar, cerrar };
  if (abierta && abierta.clave.startsWith("toma:") && !s.tomas.some((t) => `toma:${t.id}` === abierta.clave)) abierta = null;
  if (abierta && abierta.clave.startsWith("nueva:") && abierta.clave !== `nueva:${dia.fecha}`) abierta = null;
  const foco = abierta && abierta.nodo.contains(document.activeElement) ? document.activeElement : null;
  const cab = el("div", { className: "sup-cab" }, el("span", { className: "sup-titulo", textContent: "Suplementación" }));
  if (s.tomas.length) {
    const partes = [`${mgTexto.format(s.cafeina_mg)} mg de cafeína`];
    if (s.ultima) partes.push(`última: ${s.ultima}`);
    cab.append(el("span", { className: "sup-resumen num", textContent: partes.join(" · ") }));
  }
  const nodos = [cab];
  if (s.tomas.length) {
    const lista = el("ul", { className: "sup-tomas" });
    for (const t of s.tomas) {
      lista.append(filaToma(t, () => {
        abierta = abierta && abierta.clave === `toma:${t.id}` ? null : { clave: `toma:${t.id}`, nodo: fichaToma(t, dia.fecha, opciones) };
        repintar();
      }));
      if (abierta && abierta.clave === `toma:${t.id}`) lista.append(el("li", {}, conservada(abierta.clave, () => fichaToma(t, dia.fecha, opciones))));
    }
    nodos.push(lista);
  }
  if (s.leche) {
    const donde = s.leche.repartida ? "repartida entre los cafés" : "a lo largo del día";
    nodos.push(el("p", { className: "sup-leche suave",
      textContent: `Leche de los cafés: ${mgTexto.format(s.leche.ml)} ml · ${mgTexto.format(s.leche.kcal ?? 0)} kcal, ${donde}` }));
  }
  const claveNueva = `nueva:${dia.fecha}`;
  if (abierta && abierta.clave === claveNueva) {
    nodos.push(conservada(claveNueva, () => fichaToma(null, dia.fecha, opciones)));
  } else {
    const anadir = el("button", { type: "button", className: "anadir-toma", textContent: "Añadir" });
    anadir.addEventListener("click", () => { abierta = { clave: claveNueva, nodo: fichaToma(null, dia.fecha, opciones) }; repintar(); });
    nodos.push(anadir);
  }
  bloque.classList.toggle("plegado", !s.tomas.length && !s.leche);
  bloque.replaceChildren(...nodos);
  bloque.hidden = false;
  if (foco && foco.isConnected && document.activeElement !== foco) foco.focus({ preventScroll: true });
}
