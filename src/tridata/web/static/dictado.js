// Recording with the phone's microphone (unit 006). Plain ES module, no build step.
// The microphone is asked for only after a tap (iOS requires a user gesture), MediaRecorder uses
// the first type the browser supports (Safari on iOS: audio/mp4, AAC), a recording stops by
// itself at 2 minutes, and the microphone is released as soon as it stops.

export const MAX_SEGUNDOS = 120;
const TIPOS = ["audio/mp4", "audio/webm"];

export const puedeGrabar = () => Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

function tipoAdmitido() {
  for (const tipo of TIPOS) if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(tipo)) return tipo;
  return ""; // let the browser choose
}

// Starts recording. `alSegundo(s)` every second; `alTerminar({ audio, tipo, segundos })` once,
// with the audio as an ArrayBuffer. Returns { parar } or throws if there is no microphone.
export async function grabar({ alSegundo = () => {}, alTerminar }) {
  const flujo = await navigator.mediaDevices.getUserMedia({ audio: true });
  const tipo = tipoAdmitido();
  let grabadora;
  try {
    grabadora = tipo ? new MediaRecorder(flujo, { mimeType: tipo }) : new MediaRecorder(flujo);
  } catch (error) {
    flujo.getTracks().forEach((pista) => pista.stop());
    throw error;
  }
  const trozos = [];
  const inicio = Date.now();
  let segundos = 0;
  const reloj = setInterval(() => {
    const ahora = Math.floor((Date.now() - inicio) / 1000);
    if (ahora !== segundos) { segundos = ahora; alSegundo(segundos); }
    if (segundos >= MAX_SEGUNDOS) parar();
  }, 250);

  grabadora.ondataavailable = (evento) => { if (evento.data && evento.data.size) trozos.push(evento.data); };
  grabadora.onstop = async () => {
    clearInterval(reloj);
    flujo.getTracks().forEach((pista) => pista.stop()); // the microphone is released
    const tipoFinal = (grabadora.mimeType || tipo || "audio/mp4").split(";")[0];
    const audio = await new Blob(trozos, { type: tipoFinal }).arrayBuffer();
    alTerminar({ audio, tipo: tipoFinal, segundos: Math.round((Date.now() - inicio) / 1000) });
  };
  grabadora.start(1000);
  alSegundo(0);

  function parar() {
    if (grabadora.state !== "inactive") grabadora.stop();
  }
  return { parar };
}
