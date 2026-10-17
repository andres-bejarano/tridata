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
- `gitleaks` 8.30.1 en `~/.local/bin` (que está en el `PATH`). Sin él, `scripts/ci/security`
  sale en rojo y dice qué instalar.

## Estructura

- `src/tridata/`: un módulo por pieza — `cli.py` (comando `tridata`), `garmin_client.py` y
  `sync.py` (Garmin Connect → SQLite), `storage.py` (`~/.tridata/data.sqlite3`),
  `metrics.py` (TSS/CTL/ATL/TSB), `exporters.py` (Markdown/JSON), `mcp_server.py`
  (`tridata-mcp`, solo lectura).
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
