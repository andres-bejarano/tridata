# AGENTS.md — tridata

Repo de código de «TriData: datos de entrenamiento de triatlón desde Garmin». Se orquesta
desde su meta-repo (la carpeta padre; ver `repos.yaml` allí). Aquí solo vive el código de la
aplicación. **El repo es público.**

## Comandos (literales, probados; los usa el método en cada cierre)

Todos se lanzan desde la raíz del checkout (`main/` o tu worktree) con el entorno de
desarrollo activo: `source ~/.venvs/tridata-dev/bin/activate`. Los scripts de `scripts/ci/`
usan el `python` activo y ponen el `src/` de ese checkout por delante, así que cada worktree
se comprueba a sí mismo.

| Para… | Comando |
|---|---|
| Levantar el entorno desde cero | `~/anaconda3/envs/tridata/bin/python -m venv ~/.venvs/tridata-dev && ~/.venvs/tridata-dev/bin/python -m pip install --upgrade pip && ~/.venvs/tridata-dev/bin/python -m pip install -e ".[dev]"` (y gitleaks, ver abajo) |
| Correr la suite completa | `scripts/ci/full-suite` |
| Correr los linters | `scripts/ci/lint` |
| Lanzar una instancia para que la use el usuario (solo lectura: la carga de entreno CTL/ATL/TSB) | `tridata export-pmc --out /tmp/tridata-pmc.md` → se abre con `less /tmp/tridata-pmc.md` |
| Instalar el dictado por voz (extra `voz`, en el entorno que corre `tridata web`) | `python -m pip install -e ".[voz]"` |
| Prueba de humo del dictado con el modelo real en la GPU (fuera de la suite) | `PYTHONPATH=src python scripts/probar_dictado.py .runtime/<unidad>/audio.m4a --veces 10` |
| Instalar y correr el E2E de la pantalla del móvil (extra `e2e`, fuera de `dev` y del CI; sin él `tests/e2e/` se salta) | `python -m pip install -e ".[dev,e2e]" && python -m playwright install chromium`, y luego `TRIDATA_CAPTURAS=../../.runtime/<unidad>/capturas PYTHONPATH=src python -m pytest -q tests/e2e` (las capturas solo si se pone `TRIDATA_CAPTURAS`) |
| Comprobación de seguridad | `scripts/ci/security` |
| Todo seguido (lint, suite, seguridad) | `scripts/check.sh` |
| Parar / limpiar | `rm -f /tmp/tridata-pmc.md && deactivate` (el entorno entero: `rm -rf ~/.venvs/tridata-dev`) |

Instalar gitleaks (versión fijada y suma SHA-256 comprobada; es la misma que usa GitHub):

```bash
V=8.30.1; SHA=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
cd "$(mktemp -d)"
curl -fsSLO "https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_linux_x64.tar.gz"
echo "$SHA  gitleaks_${V}_linux_x64.tar.gz" | sha256sum -c -
tar -xzf "gitleaks_${V}_linux_x64.tar.gz" gitleaks
install -m 0755 gitleaks ~/.local/bin/gitleaks
gitleaks version
```

GitHub Actions corre los mismos tres scripts: `.github/workflows/tests.yml` (suite, Python
3.10 y 3.11) y `.github/workflows/quality-security.yml` (lint y seguridad en paralelo, en cada
PR, al entrar en `master` y cada lunes). Dependabot propone las actualizaciones.

## Qué necesita esta máquina

- Python 3.11 del entorno conda `~/anaconda3/envs/tridata` **solo como base del venv**. Ese
  entorno corre la rutina diaria de las 08:00 (`daily_update.sh`, timer `tridata-sync`) y el
  MCP: no se instala nada en él. `python3` del sistema es 3.8 y no sirve.
- El venv `~/.venvs/tridata-dev` con el extra `dev` (pytest, ruff, mypy, pip-audit, mcp).
  Instalado en editable, apunta al checkout desde el que se instaló: si ese worktree se
  borra, se reinstala con `~/.venvs/tridata-dev/bin/python -m pip install -e ".[dev]"`.
- **Dictado por voz (opcional, extra `voz`)**: faster-whisper con `large-v3-turbo` en la GPU
  (RTX 2060, 6 GB; driver con CUDA 12 o superior). Descarga ≈ 3 GB: ≈ 1,45 GB de librerías
  (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, ctranslate2, PyAV) y ≈ 1,6 GB de modelo, que baja
  solo a `~/.cache/huggingface` la primera vez. No hace falta CUDA del sistema, ni ffmpeg, ni
  `LD_LIBRARY_PATH`: `ia/whisper.py` precarga cuBLAS y cuDNN desde el `site-packages` del entorno
  (probado con faster-whisper 1.2.1, ctranslate2 4.8.3, av 18.1.0). El modelo se cambia con
  `TRIDATA_MODELO_VOZ` en `.env`. Sin el extra, `tridata web` arranca igual, avisa de que el
  dictado está desactivado y escribir sigue funcionando. No va en `dev`: la suite dobla el
  transcriptor y ningún test carga el modelo. `scripts/probar_dictado.py` imprime dispositivo,
  segundos de carga y de cada transcripción y el texto; el audio de prueba (frases inventadas)
  va a `.runtime/` del meta-repo, nunca a este repo.
- `gitleaks` 8.30.1 en `~/.local/bin` (que está en el `PATH`). Sin él, `scripts/ci/security`
  sale en rojo y dice qué instalar.
- **Google Calendar (opcional)**: las sesiones del plan y los entrenos hechos van al calendario
  «TriData» de la cuenta de Google del usuario (`calendario/`). Sin configurarlo, Ajustes dice
  «sin conectar» y nada habla con Google. Para montarlo en una máquina nueva:
  1. En Google Cloud Console, un proyecto propio; en la **Biblioteca**, activar «Google Calendar
     API» (un proyecto nuevo no la trae habilitada).
  2. Pantalla de consentimiento **Externa**, con el propio usuario como usuario de prueba. El
     permiso (`calendar.app.created`: solo los calendarios que crea la app) no se declara a mano:
     la app lo pide al conectar.
  3. **Publicar la app** («En producción») sin pedir verificación: en «Prueba» el permiso caduca
     a los 7 días. Pide página principal y política de privacidad; sin logo no hay verificación
     que esperar, y al conectar Google avisa de «app no verificada» (se acepta, es la propia).
  4. Credenciales → cliente OAuth de tipo **App de escritorio** → descargar su JSON a un sitio
     fuera de este repo y poner su ruta en `TRIDATA_GOOGLE_CLIENTE` del `.env` que encuentra
     `load_dotenv()`.
  5. Reiniciar `tridata web` (la variable se lee al arrancar) y, **desde el PC que corre la app**,
     Ajustes › Google Calendar › «Conectar Google Calendar»: la página de permiso se abre en el
     navegador de esa máquina. El permiso se guarda en `~/.tridata/google-token.json` (600).
  «Desconectar» en Ajustes retira el permiso en Google y lo borra del PC; el calendario «TriData»
  se queda en Google y al volver a conectar se reutiliza. Los tests doblan Google siempre.

## Estructura

- `src/tridata/`: un módulo por pieza — `cli.py` (comando `tridata`), `garmin_client.py` y
  `sync.py` (Garmin Connect → SQLite), `storage.py` (`~/.tridata/data.sqlite3`),
  `metrics.py` (TSS/CTL/ATL/TSB), `exporters.py` (Markdown/JSON), `mcp_server.py`
  (`tridata-mcp`, solo lectura), `calendario/` (Google Calendar: `__init__.py` el permiso y la
  API, `sincronizar.py` qué se crea, cambia o borra) y `web/` (`tridata web`; `web/calendario.py`
  Ajustes › Google Calendar y el hilo que lo actualiza).
- `tests/` con `tests/fixtures/` (datos inventados); `scripts/ci/` las comprobaciones;
  `daily_update.sh` y `.config/systemd/user/` la rutina diaria.

## Reglas de este repo

- Los secretos van en `.env` (fuera de git; plantilla en `.env.example`). El `.env` no se
  lee, no se copia ni se pega en ningún sitio. Si algún día hay imagen Docker, el `.env` va en
  `.dockerignore`: un secreto horneado en una imagen es un secreto publicado.
- **Nada de datos de salud reales** (FC, sueño, HRV, peso, actividades) ni credenciales en
  tests, fixtures, commits, PRs ni logs: el repo es público. Los fixtures son inventados.
- Los tests no tocan `~/.tridata/`: `scripts/ci/full-suite` corre con un `HOME` temporal.
- Las dependencias de desarrollo y test están separadas de las de producción (extra `dev`
  de `pyproject.toml`).
- Los tests se escriben antes que el código y no se debilitan para que pase la suite.
- Los scripts obligatorios de `scripts/ci/` preparan su entorno, usan herramientas fijadas y
  propagan cualquier rojo (`set -euo pipefail`). Prohibido `|| true` o dar verde por no
  encontrar tests o una herramienta.
- La suite, el lint y la seguridad se ejecutan en local antes de fusionar; GitHub los repite.
- Las Actions externas van fijadas por SHA de 40 caracteres; los workflows propagan
  cualquier fallo.
- Un aviso de pip-audit se ignora solo con `--ignore-vuln` y su motivo escrito al lado en
  `scripts/ci/security`, con la condición para quitarlo.
