// Service worker (unit 006, R7): the app opens with the PC off. Plain script, no build step.
// It only keeps the «shell» (the page, its JS and CSS) and always asks the network first, so with
// the PC on the newest version arrives; the cache is only the fallback. Data (/api/*) never come
// from a cache and nothing that is not a GET is touched. Served from the root (/sw.js) so its
// scope is the whole app.

const VERSION = "tridata-cascaron-v1"; // change it when the list below changes
const CASCARON = ["/", "/static/app.js", "/static/cola.js", "/static/dictado.js", "/static/estilos.css"];
const ESPERA_RED_MS = 4000; // a PC that is off over Tailscale may hang instead of failing

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(CASCARON)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(caches.keys()
    .then((nombres) => Promise.all(nombres.filter((n) => n !== VERSION).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

function conEspera(promesa, ms) {
  return new Promise((resolver, fallar) => {
    const reloj = setTimeout(() => fallar(new Error("sin respuesta")), ms);
    promesa.then((r) => { clearTimeout(reloj); resolver(r); }, (e) => { clearTimeout(reloj); fallar(e); });
  });
}

async function redPrimero(peticion, ruta) {
  const cache = await caches.open(VERSION);
  try {
    const respuesta = await conEspera(fetch(peticion), ESPERA_RED_MS);
    if (respuesta.ok) await cache.put(ruta, respuesta.clone());
    return respuesta;
  } catch (error) {
    const guardada = await cache.match(ruta);
    if (guardada) return guardada;
    throw error;
  }
}

self.addEventListener("fetch", (evento) => {
  const peticion = evento.request;
  if (peticion.method !== "GET") return;
  const url = new URL(peticion.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // data always from the PC, never from a cache
  if (!CASCARON.includes(url.pathname)) return;
  evento.respondWith(redPrimero(peticion, url.pathname));
});
