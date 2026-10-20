// The recordings queue of the phone (unit 006, R-10). Plain ES module, no build step.
// A recording is saved in IndexedDB BEFORE it is sent, and its audio is deleted only when the PC
// answers with the list (200) or with the text (502: Claude failed). What comes back waits as a
// «lista pendiente de revisar» until it is confirmed or discarded. iOS has no Background Sync:
// the queue only goes out with the app open (on start, back to the front, back online, after
// recording and every 30 s while something is waiting), one at a time and in order.
// The audio is stored as an ArrayBuffer, never a Blob: WebKit has had bugs storing Blobs.

const BASE = "tridata";
const VERSION_BASE = 1;
const GRABACIONES = "grabaciones"; // { id, creada, fecha, tipo, audio, estado: sin_enviar | no_transcribible, error }
const LISTAS = "listas"; // { id, creada, fecha, texto, interpretacion | null }
const CADA_MS = 30_000;
const ESPERA_ENVIO_MS = 60_000; // a PC that does not answer is a PC that is not there

let base = null;
let enviando = false; // the lock: one sending loop at a time
let avisar = () => {};

export const fechaLocal = (d = new Date()) => {
  const dos = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`;
};

const nuevoId = () => (crypto.randomUUID ? crypto.randomUUID()
  : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));

function abrir() {
  if (base) return base;
  base = new Promise((resolver, fallar) => {
    const peticion = indexedDB.open(BASE, VERSION_BASE);
    peticion.onupgradeneeded = () => {
      const db = peticion.result;
      if (!db.objectStoreNames.contains(GRABACIONES)) db.createObjectStore(GRABACIONES, { keyPath: "id" });
      if (!db.objectStoreNames.contains(LISTAS)) db.createObjectStore(LISTAS, { keyPath: "id" });
    };
    peticion.onsuccess = () => resolver(peticion.result);
    peticion.onerror = () => { base = null; fallar(peticion.error); };
  });
  return base;
}

// One transaction over `almacenes`; `obra(tx)` queues the requests; resolves when it is committed.
async function transaccion(almacenes, modo, obra) {
  const db = await abrir();
  return new Promise((resolver, fallar) => {
    const tx = db.transaction(almacenes, modo);
    let resultado;
    Promise.resolve(obra(tx)).then((r) => { resultado = r; });
    tx.oncomplete = () => resolver(resultado);
    tx.onerror = () => fallar(tx.error);
    tx.onabort = () => fallar(tx.error);
  });
}

const comoPromesa = (peticion) => new Promise((resolver, fallar) => {
  peticion.onsuccess = () => resolver(peticion.result);
  peticion.onerror = () => fallar(peticion.error);
});

const porOrden = (a, b) => (a.creada < b.creada ? -1 : a.creada > b.creada ? 1 : 0);

async function todas(almacen) {
  const lista = await transaccion([almacen], "readonly", (tx) => comoPromesa(tx.objectStore(almacen).getAll()));
  return lista.sort(porOrden);
}

// What the day screen shows: how many wait to be sent, which could not be transcribed, the lists.
export async function estado() {
  const grabaciones = await todas(GRABACIONES);
  return {
    sinEnviar: grabaciones.filter((g) => g.estado === "sin_enviar").length,
    noTranscribibles: grabaciones.filter((g) => g.estado === "no_transcribible")
      .map(({ id, creada, fecha, error }) => ({ id, creada, fecha, error })),
    listas: await todas(LISTAS),
  };
}

// Saves a recording, then tries to send the queue. `audio` is an ArrayBuffer.
export async function guardarGrabacion(audio, tipo) {
  const ahora = new Date();
  const grabacion = { id: nuevoId(), creada: ahora.toISOString(), fecha: fechaLocal(ahora), tipo, audio, estado: "sin_enviar", error: null };
  await transaccion([GRABACIONES], "readwrite", (tx) => { tx.objectStore(GRABACIONES).put(grabacion); });
  await avisar({ conexion: null });
  enviarPendientes();
  return grabacion.id;
}

export async function borrarGrabacion(id) {
  await transaccion([GRABACIONES], "readwrite", (tx) => { tx.objectStore(GRABACIONES).delete(id); });
  await avisar({ conexion: null });
}

export async function borrarLista(id) {
  await transaccion([LISTAS], "readwrite", (tx) => { tx.objectStore(LISTAS).delete(id); });
  await avisar({ conexion: null });
}

// The answer of the PC becomes a list and the audio is deleted, in ONE transaction: a crash in
// between can never lose the recording without its list, nor keep both.
async function guardarRespuesta(g, texto, interpretacion) {
  const lista = { id: g.id, creada: g.creada, fecha: g.fecha, texto, interpretacion };
  await transaccion([GRABACIONES, LISTAS], "readwrite", (tx) => {
    tx.objectStore(LISTAS).put(lista);
    tx.objectStore(GRABACIONES).delete(g.id);
  });
}

async function marcarNoTranscribible(g, error) {
  await transaccion([GRABACIONES], "readwrite", (tx) => {
    tx.objectStore(GRABACIONES).put({ ...g, estado: "no_transcribible", error });
  });
}

async function enviarUna(g) {
  const control = new AbortController();
  const reloj = setTimeout(() => control.abort(), ESPERA_ENVIO_MS);
  let r;
  try {
    r = await fetch(`/api/dictado?id=${encodeURIComponent(g.id)}&fecha=${encodeURIComponent(g.fecha)}`, {
      method: "POST", headers: { "Content-Type": g.tipo }, body: new Blob([g.audio], { type: g.tipo }), signal: control.signal,
    });
  } catch {
    return "sin_conexion";
  } finally {
    clearTimeout(reloj);
  }
  const datos = await r.json().catch(() => ({}));
  if (r.status === 200) {
    await guardarRespuesta(g, datos.texto, datos.interpretacion);
    return "hecha";
  }
  if (r.status === 502 && typeof datos.texto === "string") { // Claude failed: the text is kept to interpret
    await guardarRespuesta(g, datos.texto, null);
    return "hecha";
  }
  if (datos.reintentar === false && [400, 413, 415, 422].includes(r.status)) {
    await marcarNoTranscribible(g, datos.error || "No se ha podido transcribir.");
    return "hecha";
  }
  if (!datos.error) return "sin_conexion"; // the proxy answered (tailscale serve: 502), not the app
  return "reintentar"; // 503 «try later» or anything unexpected: the audio stays
}

// Sends what waits, oldest first, one at a time. Stops at the first one the PC cannot take.
export async function enviarPendientes() {
  if (enviando) return;
  enviando = true;
  let conexion = null;
  try {
    for (;;) {
      const siguiente = (await todas(GRABACIONES)).find((g) => g.estado === "sin_enviar");
      if (!siguiente) break;
      const resultado = await enviarUna(siguiente);
      if (resultado === "sin_conexion") { conexion = false; break; }
      conexion = true;
      await avisar({ conexion });
      if (resultado === "reintentar") break;
    }
  } finally {
    enviando = false;
  }
  await avisar({ conexion });
}

// Starts the queue: `alCambiar({ conexion })` is called whenever something may have changed
// (`conexion` true/false when a sending said so, null when nothing was sent).
export async function iniciar(alCambiar) {
  avisar = async (cambio) => { try { await alCambiar(cambio); } catch (e) { console.error(e); } };
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  window.addEventListener("online", () => enviarPendientes());
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") enviarPendientes(); });
  setInterval(async () => {
    if (document.visibilityState !== "visible") return;
    if ((await estado()).sinEnviar > 0) enviarPendientes();
  }, CADA_MS);
  await avisar({ conexion: null });
  enviarPendientes();
}
