// Unit 082 (REC-34, R-226, R-227, R-228): the block «Suplementación» on top of Nutrición › Día, apart from the meals.
// Its coffees and B12 of the day by hour, «N mg de cafeína · última: HH:MM» and, on each coffee, its share of the milk
// of the day («+125 ml»; «solo» when it had none). None of it has kcal (G-60): the milk is a food of the day.
// Tapping a dose opens its card (hour, form, mg, «solo»; delete asks first); «Añadir» opens an empty one. Without
// doses the block is folded: only its title and «Añadir». Plain ES module, no build step; app.js gives it `api` and
// `alCambiar(dia)` (the day painted again) so there is no import back.
//
// Unit 109 (REC-52, R-318..R-321): gels with caffeine and salt tablets too. The caffeine says where it comes from
// («230 mg de cafeína (café 130 · geles 100)», only with gels), the ones with a minute of the race go apart in «En
// carrera» by minute («min 60»), and «Sodio: N mg» only when some dose has sodium. In the card of a gel or a salt:
// its saved product (its values × the number, without typing them), the number, the mg per unit, the hour or the
// minute. «Productos» in «Añadir» saves, changes and deletes the saved gels and salts (delete asks first).

const FORMAS = [["expreso", "Expreso"], ["doble", "Doble"], ["ingles", "Inglés"], ["italiana", "Italiana"],
  ["soluble", "Soluble"]];
const QUES = [["cafe", "Café"], ["b12", "B12"], ["gel", "Gel con cafeína"], ["sal", "Pastilla de sal"]];
const TIPOS = [["gel", "Gel"], ["sal", "Pastilla de sal"]];
const mgTexto = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
const numTexto = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
let abierta = null; // { clave: "toma:N" | "nueva:AAAA-MM-DD", nodo } kept between repaints (what is typed stays)
let productos = []; // the saved gels and salts (unit 109), from the day or from /api/producto

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
const deCarrera = (que) => que === "gel" || que === "sal";
const vacio = (v) => v === null || v === undefined;

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

function numero(etiqueta, valor, paso = "1") {
  return campo(etiqueta, el("input", { type: "number", min: "0", step: paso, inputMode: "decimal",
    value: vacio(valor) ? "" : String(valor) }));
}

// What a dose shows on its right: the mg of caffeine (a coffee, a gel) or the sodium (a salt)
function mgDeToma(t) {
  if (t.que === "sal") return vacio(t.sodio_mg) ? "" : `${mgTexto.format(t.sodio_mg)} mg sodio`;
  return vacio(t.mg) ? "" : `${t.mg_estimado ? "≈" : ""}${mgTexto.format(t.mg)} mg`;
}

function filaToma(t, alTocar, enCarrera = false) {
  const leche = t.que !== "cafe" ? "" : t.solo ? "solo" : t.leche_ml ? `+${mgTexto.format(t.leche_ml)} ml` : "";
  const cuando = enCarrera ? `min ${t.minuto}` : t.hora ?? "—";
  const boton = el("button", { type: "button", className: "toma" },
    el("span", { className: "toma-hora num", textContent: cuando }),
    el("span", { className: "toma-que", textContent: mayuscula(t.texto) }),
    el("span", { className: "toma-mg num", textContent: mgDeToma(t) }),
    el("span", { className: "toma-leche num", textContent: leche }));
  boton.dataset.id = t.id;
  boton.addEventListener("click", alTocar);
  return el("li", {}, boton);
}

function opcionesProducto(select, tipo, elegido) {
  const de = productos.filter((p) => p.tipo === tipo);
  select.replaceChildren(el("option", { value: "", textContent: de.length ? "— Ninguno —" : "— No hay guardados —" }),
    ...de.map((p) => el("option", { value: String(p.id), textContent: p.nombre })));
  select.value = elegido && de.some((p) => String(p.id) === elegido) ? elegido : "";
}

// Unit 109: «Productos» — the saved gels and salts, each one changed or deleted by tapping it; «Nuevo producto».
function gestorProductos({ api, alGuardar }) {
  const caja = el("div", { className: "sup-productos" });
  let editando = null; // null: the list; {} a new one; a product: a change
  const pintar = () => {
    if (editando !== null) { caja.replaceChildren(fichaProducto(editando)); return; }
    const lista = el("ul", { className: "lista-productos" });
    for (const p of productos) {
      const valores = [vacio(p.mg_cafeina) ? null : `${numTexto.format(p.mg_cafeina)} mg cafeína`,
        vacio(p.sodio_mg) ? null : `${numTexto.format(p.sodio_mg)} mg sodio`].filter(Boolean).join(" · ");
      const boton = el("button", { type: "button", className: "producto" },
        el("span", { className: "producto-nombre", textContent: p.nombre }),
        el("span", { className: "producto-valores num", textContent: valores }));
      boton.addEventListener("click", () => { editando = p; pintar(); });
      lista.append(el("li", {}, boton));
    }
    const nuevo = el("button", { type: "button", className: "anadir-toma", textContent: "Nuevo producto" });
    nuevo.addEventListener("click", () => { editando = {}; pintar(); });
    caja.replaceChildren(...(productos.length ? [lista] : [el("p", { className: "suave", textContent: "Aún no hay productos guardados." })]), nuevo);
  };
  const volver = (datos) => {
    if (datos && datos.productos) { productos = datos.productos; alGuardar(); }
    editando = null;
    pintar();
  };
  function fichaProducto(p) {
    const nuevo = p.id === undefined;
    const nombre = campo("Nombre", el("input", { type: "text", value: p.nombre ?? "", placeholder: "Gel 226 cola" }));
    const tipo = selector("Tipo", TIPOS, p.tipo ?? "gel");
    const cafeina = numero("Cafeína (mg)", p.mg_cafeina);
    const sodio = numero("Sodio (mg)", p.sodio_mg);
    const verTipo = () => { cafeina.hidden = tipo.entrada.value !== "gel"; };
    tipo.entrada.addEventListener("change", verTipo);
    verTipo();
    const aviso = el("p", { className: "aviso", hidden: true });
    const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
    const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar producto" });
    const cancelar = el("button", { type: "button", textContent: "Volver" });
    const acciones = el("div", { className: "acciones" }, guardar, cancelar);
    cancelar.addEventListener("click", () => volver(null));
    guardar.addEventListener("click", async () => {
      const valor = (c) => (c.entrada.value.trim() === "" ? null : Number(c.entrada.value));
      const cuerpo = { nombre: nombre.entrada.value, tipo: tipo.entrada.value, sodio_mg: valor(sodio),
        mg_cafeina: tipo.entrada.value === "gel" ? valor(cafeina) : null };
      guardar.disabled = true;
      avisar("");
      const { ok, datos } = await api(nuevo ? "/api/producto" : `/api/producto/${p.id}`, cuerpo);
      guardar.disabled = false;
      if (!ok) { avisar(`No se ha podido guardar. ${datos.error ?? ""}`.trim()); return; }
      volver(datos);
    });
    const ficha = el("div", { className: "ficha-producto" }, nombre, el("div", { className: "fila" }, tipo, cafeina, sodio),
      aviso, acciones);
    if (nuevo) return ficha;
    const borrar = el("button", { type: "button", className: "peligro", textContent: "Borrar producto" });
    const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
      el("p", { textContent: `¿Borrar el producto ${p.nombre}? Las tomas ya apuntadas no cambian.` }));
    const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, borrar producto" });
    const no = el("button", { type: "button", textContent: "No" });
    pregunta.append(el("div", { className: "acciones" }, si, no));
    const preguntar = (s) => { pregunta.hidden = !s; acciones.hidden = s; borrar.hidden = s; };
    borrar.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
    no.addEventListener("click", () => preguntar(false));
    si.addEventListener("click", async () => {
      si.disabled = true;
      const r = await borrarCon(`/api/producto/${p.id}`);
      si.disabled = false;
      if (!r.ok) { preguntar(false); avisar(`No se ha podido borrar. ${r.datos.error ?? ""}`.trim()); return; }
      volver(r.datos);
    });
    ficha.append(borrar, pregunta);
    return ficha;
  }
  pintar();
  return caja;
}

async function borrarCon(ruta) {
  try {
    const respuesta = await fetch(ruta, {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmado: true }),
    });
    return { ok: respuesta.ok, datos: await respuesta.json().catch(() => ({})) };
  } catch {
    return { ok: false, datos: { error: "No hay conexión con el PC. Inténtalo de nuevo." } };
  }
}

// The card of a dose: a change (`t`) or a new one (`t` null, on `fecha`). `enviar` saves; `borrar` only for a change.
function fichaToma(t, fecha, { api, alCambiar, cerrar }) {
  const nueva = t === null;
  const que = selector("Qué", QUES, nueva ? "cafe" : t.que);
  const forma = selector("Forma", FORMAS, nueva ? "doble" : t.forma);
  const hora = campo("Hora", el("input", { type: "time", value: nueva ? horaDeAhora() : t.hora ?? "" }));
  const mg = campo("mg de cafeína", el("input", { type: "number", min: "0", step: "1", inputMode: "numeric",
    value: nueva || t.mg === null ? "" : String(t.mg) }));
  const solo = el("input", { type: "checkbox", checked: !nueva && Boolean(t.solo) });
  const soloCampo = el("label", { className: "campo-solo" }, solo, el("span", { textContent: "Solo (sin leche)" }));
  const dosis = campo("Dosis", el("input", { type: "text", value: nueva ? "" : t.dosis ?? "", placeholder: "opcional" }));
  // unit 109: a gel or a salt (the values per unit: what is kept is × the number)
  const porUnidad = (clave) => (nueva || !deCarrera(t.que) || vacio(t[clave]) ? null : t[clave] / (t.cantidad || 1));
  const producto = selector("Producto", [], "");
  const elegido = nueva ? "" : String(productos.find((p) => p.tipo === t.que && p.nombre === t.producto)?.id ?? "");
  const nombre = campo("Nombre", el("input", { type: "text", value: nueva ? "" : t.producto ?? "", placeholder: "opcional" }));
  const cantidad = numero("Número", nueva ? 1 : t.cantidad ?? 1, "any");
  const mgUnidad = numero("mg de cafeína por unidad", porUnidad("mg"), "any");
  const sodio = numero("mg de sodio por unidad", porUnidad("sodio_mg"), "any");
  const minuto = numero("Minuto de carrera", nueva ? null : t.minuto);
  const iniciales = { mg: mgUnidad.entrada.value, sodio: sodio.entrada.value };
  const elProducto = () => productos.find((p) => String(p.id) === producto.entrada.value);
  producto.entrada.addEventListener("change", () => {
    const p = elProducto();
    if (!p) return;
    nombre.entrada.value = p.nombre;
    mgUnidad.entrada.value = vacio(p.mg_cafeina) ? "" : String(p.mg_cafeina);
    sodio.entrada.value = vacio(p.sodio_mg) ? "" : String(p.sodio_mg);
  });
  const deCafe = [forma, mg, soloCampo];
  const deGelSal = [producto, nombre, cantidad, mgUnidad, sodio, minuto];
  const horaPuesta = hora.entrada.value; // a new one: the hour it is now, only for a coffee or the B12
  const verQue = () => {
    const q = que.entrada.value;
    if (nueva && deCarrera(q) && hora.entrada.value === horaPuesta) hora.entrada.value = "";
    if (nueva && !deCarrera(q) && hora.entrada.value === "") hora.entrada.value = horaPuesta;
    for (const nodo of deCafe) nodo.hidden = q !== "cafe";
    for (const nodo of deGelSal) nodo.hidden = !deCarrera(q);
    mgUnidad.hidden = q !== "gel";
    dosis.hidden = q !== "b12";
    if (deCarrera(q)) opcionesProducto(producto.entrada, q, producto.entrada.value || elegido);
  };
  que.entrada.addEventListener("change", verQue);
  verQue();
  const aviso = el("p", { className: "aviso", hidden: true });
  const avisar = (texto) => { aviso.textContent = texto; aviso.hidden = !texto; };
  const guardar = el("button", { type: "button", className: "principal", textContent: "Guardar" });
  const cancelar = el("button", { type: "button", textContent: "Cancelar" });
  const acciones = el("div", { className: "acciones" }, guardar, cancelar);
  cancelar.addEventListener("click", cerrar);
  const numeroDe = (c) => (c.entrada.value.trim() === "" ? null : Number(c.entrada.value));
  guardar.addEventListener("click", async () => {
    const q = que.entrada.value;
    const carrera = deCarrera(q);
    if (!carrera && !hora.entrada.value) { avisar("Pon la hora."); return; }
    if (carrera && !hora.entrada.value && minuto.entrada.value.trim() === "") { avisar("Pon la hora o el minuto."); return; }
    const cuerpo = { hora: hora.entrada.value || null };
    if (nueva) Object.assign(cuerpo, { dia: fecha, que: q });
    if (q === "cafe") {
      Object.assign(cuerpo, { forma: forma.entrada.value, solo: solo.checked });
      const escrito = mg.entrada.value.trim();
      if (nueva ? escrito !== "" : escrito !== (t.mg === null ? "" : String(t.mg))) cuerpo.mg = escrito === "" ? null : Number(escrito);
    } else if (q === "b12") {
      cuerpo.dosis = dosis.entrada.value.trim() || null;
    } else {
      const p = elProducto();
      Object.assign(cuerpo, { cantidad: numeroDe(cantidad), minuto: numeroDe(minuto),
        producto: nombre.entrada.value.trim() || null });
      if (p && (nueva || String(p.id) !== elegido)) cuerpo.producto_id = p.id;
      // the values per unit only when they are not the product's (or, in a change, when they were changed)
      const mgDe = p ? (vacio(p.mg_cafeina) ? "" : String(p.mg_cafeina)) : (nueva ? "" : iniciales.mg);
      const sodioDe = p ? (vacio(p.sodio_mg) ? "" : String(p.sodio_mg)) : (nueva ? "" : iniciales.sodio);
      if (q === "gel" && mgUnidad.entrada.value.trim() !== mgDe) cuerpo.mg = numeroDe(mgUnidad);
      if (sodio.entrada.value.trim() !== sodioDe) cuerpo.sodio_mg = numeroDe(sodio);
      if (cuerpo.producto_id !== undefined && cuerpo.producto === p.nombre) delete cuerpo.producto;
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
    ...(nueva ? [que] : []), producto, nombre, el("div", { className: "fila" }, hora, forma, mg),
    el("div", { className: "fila" }, cantidad, mgUnidad, sodio, minuto), soloCampo, dosis, aviso, acciones);
  if (nueva) {
    // unit 109: the saved products, managed from «Añadir»
    const verProductos = el("button", { type: "button", className: "ver-productos", textContent: "Productos" });
    verProductos.setAttribute("aria-expanded", "false");
    let gestor = null;
    verProductos.addEventListener("click", () => {
      if (gestor) { gestor.remove(); gestor = null; verProductos.setAttribute("aria-expanded", "false"); return; }
      gestor = gestorProductos({ api, alGuardar: () => { if (deCarrera(que.entrada.value)) opcionesProducto(producto.entrada, que.entrada.value, producto.entrada.value); } });
      verProductos.after(gestor);
      verProductos.setAttribute("aria-expanded", "true");
    });
    tarjeta.append(verProductos);
    return tarjeta;
  }
  const borrar = el("button", { type: "button", className: "peligro", textContent: "Borrar" });
  const cuando = !vacio(t.minuto) ? `del min ${t.minuto}` : `de las ${t.hora}`;
  const pregunta = el("div", { className: "confirmar-borrado", hidden: true },
    el("p", { textContent: `¿Borrar ${t.texto} ${cuando}?` }));
  const si = el("button", { type: "button", className: "peligro principal", textContent: "Sí, borrar" });
  const no = el("button", { type: "button", textContent: "No" });
  pregunta.append(el("div", { className: "acciones" }, si, no));
  const preguntar = (si_) => { pregunta.hidden = !si_; acciones.hidden = si_; borrar.hidden = si_; };
  borrar.addEventListener("click", () => { avisar(""); preguntar(true); si.focus(); });
  no.addEventListener("click", () => preguntar(false));
  si.addEventListener("click", async () => {
    si.disabled = true;
    const r = await borrarCon(`/api/toma/${t.id}`);
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

// «230 mg de cafeína (café 130 · geles 100) · última: 11:15»; without gels, as in the 082
function resumenCafeina(s) {
  let texto = `${mgTexto.format(s.cafeina_mg)} mg de cafeína`;
  if (!vacio(s.cafeina_geles_mg)) {
    texto += ` (café ${mgTexto.format(s.cafeina_cafe_mg ?? 0)} · geles ${mgTexto.format(s.cafeina_geles_mg)})`;
  }
  return s.ultima ? `${texto} · última: ${s.ultima}` : texto;
}

// Paints `#suplementacion` for `dia` (the JSON of /api/dia). `api(ruta, cuerpo)` and `alCambiar(dia)` are app.js's.
export function pintarSuplementacion(dia, { api, alCambiar }) {
  const bloque = document.getElementById("suplementacion");
  if (!bloque) return;
  const s = dia.suplementacion ?? { tomas: [], cafeina_mg: 0, ultima: null, leche: null };
  const carrera = s.en_carrera ?? [];
  if (Array.isArray(dia.productos)) productos = dia.productos;
  const repintar = () => pintarSuplementacion(dia, { api, alCambiar });
  const cerrar = () => { abierta = null; repintar(); };
  const opciones = { api, alCambiar, cerrar };
  const todas = [...s.tomas, ...carrera];
  if (abierta && abierta.clave.startsWith("toma:") && !todas.some((t) => `toma:${t.id}` === abierta.clave)) abierta = null;
  if (abierta && abierta.clave.startsWith("nueva:") && abierta.clave !== `nueva:${dia.fecha}`) abierta = null;
  const foco = abierta && abierta.nodo.contains(document.activeElement) ? document.activeElement : null;
  const cab = el("div", { className: "sup-cab" }, el("span", { className: "sup-titulo", textContent: "Suplementación" }));
  if (todas.length) cab.append(el("span", { className: "sup-resumen num", textContent: resumenCafeina(s) }));
  const nodos = [cab];
  const lista = (tomas, clase, enCarrera) => {
    const ul = el("ul", { className: clase });
    for (const t of tomas) {
      ul.append(filaToma(t, () => {
        abierta = abierta && abierta.clave === `toma:${t.id}` ? null : { clave: `toma:${t.id}`, nodo: fichaToma(t, dia.fecha, opciones) };
        repintar();
      }, enCarrera));
      if (abierta && abierta.clave === `toma:${t.id}`) ul.append(el("li", {}, conservada(abierta.clave, () => fichaToma(t, dia.fecha, opciones))));
    }
    return ul;
  };
  if (s.tomas.length) nodos.push(lista(s.tomas, "sup-tomas", false));
  if (carrera.length) {
    nodos.push(el("p", { className: "sup-sub", textContent: "En carrera" }), lista(carrera, "sup-tomas sup-carrera", true));
  }
  if (!vacio(s.sodio_mg)) nodos.push(el("p", { className: "sup-sodio num", textContent: `Sodio: ${mgTexto.format(s.sodio_mg)} mg` }));
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
  // folded (title and «Añadir» side by side) only while nothing is open: the card of a new dose needs the whole width
  bloque.classList.toggle("plegado", !todas.length && !s.leche && !(abierta && abierta.clave === claveNueva));
  bloque.replaceChildren(...nodos);
  bloque.hidden = false;
  if (foco && foco.isConnected && document.activeElement !== foco) foco.focus({ preventScroll: true });
}
