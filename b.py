#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
byte.py — gestor de notas Markdown y archivos binarios (Linux/macOS)
v1.1 (sin colores, abreviaturas entre [ ])

Cambios respecto a v1.0:
  - Cache de abreviaturas de GRUPO (antes solo se cacheaban las de entrada),
    evitando recalcular calc_abreviaturas() en cada render_ruta().
  - cmd_check ya no re-escanea el directorio por cada entrada (usa
    get_entrada_paths_map, igual que ya hacía el árbol).
  - Conexiones SSH con BatchMode=yes para no colgarse esperando un prompt
    interactivo en hosts sin ControlMaster/timeouts configurados en
    ~/.ssh/config.
  - Expansión de '~' en rutas remotas resuelta consultando $HOME real del
    remoto (remote_home_async) en vez de asumir /home/<user>.
  - Semáforo en cmd_check para acotar cuántas conexiones remotas se abren
    en paralelo.
"""
import os
import sys
import re
import json
import shutil
import shlex
import subprocess
import tempfile
import unicodedata
import hashlib
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Callable

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

__version__ = "1.1"

# ============================================================================
# CONFIGURACIÓN
# ============================================================================
class Config:
    DEFAULT_BASE = Path.home() / "Documentos/Filen/Obsidian/bytes"
    DEFAULT_EDITOR = os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")
    DEFAULT_VERSIONS_PATH = Path.home() / ".config" / "byte" / "versions"

    def __init__(self):
        self.base: Path = self.DEFAULT_BASE
        self.editor: str = self.DEFAULT_EDITOR
        self.gpg_key: str = ""
        self.gpg_keys_secondary: List[str] = []
        self.used_config_path: Optional[Path] = None
        self.columnas_default: bool = False
        self.search_encrypted: bool = False
        self.versions_path: Path = self.DEFAULT_VERSIONS_PATH
        self.diff_tool: str = "bat"
        self._load()

    def _load_toml_file(self, path: Path) -> Dict[str, Any]:
        if not path.is_file() or tomllib is None:
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _create_default_config(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'base   = "{self.DEFAULT_BASE}"\n'
            f'editor = "{self.DEFAULT_EDITOR}"\n'
            f'gpg_key = ""\n'
            f'gpg_keys_secondary = []\n'
            f'columnas = false\n'
            f'search_encrypted = false\n'
            f'versions_path = "{self.DEFAULT_VERSIONS_PATH}"\n'
            f'diff_tool = "auto"\n',
            encoding="utf-8"
        )

    def _load(self) -> None:
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        cfg = self._load_toml_file(system_path)
        if cfg:
            self.used_config_path = system_path
        else:
            vault_path = self.DEFAULT_BASE / ".byte" / "byte.toml"
            cfg = self._load_toml_file(vault_path)
            if cfg:
                self.used_config_path = vault_path
            else:
                self._create_default_config(vault_path)
                cfg = self._load_toml_file(vault_path)
                self.used_config_path = vault_path
                print(f"Configuración por defecto creada en {vault_path}", file=sys.stderr)
        if cfg:
            raw_base = cfg.get("base")
            if raw_base:
                self.base = Path(raw_base).expanduser().resolve()
            self.editor = cfg.get("editor") or self.DEFAULT_EDITOR
            self.gpg_key = cfg.get("gpg_key", "")
            raw_sec = cfg.get("gpg_keys_secondary", [])
            if isinstance(raw_sec, str):
                self.gpg_keys_secondary = [k.strip() for k in raw_sec.split(",") if k.strip()]
            else:
                self.gpg_keys_secondary = [str(k).strip() for k in raw_sec]
            self.columnas_default = bool(cfg.get("columnas", False))
            self.search_encrypted = bool(cfg.get("search_encrypted", False))
            raw_versions = cfg.get("versions_path")
            if raw_versions:
                self.versions_path = Path(raw_versions).expanduser().resolve()
            self.diff_tool = cfg.get("diff_tool", "auto")
            if self.diff_tool not in ("auto", "delta", "bat", "diff"):
                self.diff_tool = "auto"

    def save(self, base: Path, editor: str, gpg_key: str, gpg_keys_secondary: List[str],
             columnas: bool, search_encrypted: bool, versions_path: Path, diff_tool: str = "auto") -> None:
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        target = system_path if system_path.is_file() else self.base / ".byte" / "byte.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        sec_str = '[' + ', '.join(f'"{k}"' for k in gpg_keys_secondary) + ']'
        target.write_text(
            f'base   = "{base}"\n'
            f'editor = "{editor}"\n'
            f'gpg_key = "{gpg_key}"\n'
            f'gpg_keys_secondary = {sec_str}\n'
            f'columnas = {str(columnas).lower()}\n'
            f'search_encrypted = {str(search_encrypted).lower()}\n'
            f'versions_path = "{versions_path}"\n'
            f'diff_tool = "{diff_tool}"\n',
            encoding="utf-8"
        )
        self.base = base
        self.editor = editor
        self.gpg_key = gpg_key
        self.gpg_keys_secondary = gpg_keys_secondary
        self.columnas_default = columnas
        self.search_encrypted = search_encrypted
        self.versions_path = versions_path
        self.diff_tool = diff_tool
        self.used_config_path = target

# ============================================================================
# EXTENSIONES DE TEXTO
# ============================================================================
EXT_TEXTO = {
    ".md", ".txt", ".csv", ".tsv", ".log", ".org", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".css", ".js", ".py", ".sh", ".lua", ".rb", ".go", ".rs",
    ".zshrc", ".bashrc", ".profile", ".bash_profile", ".zshenv",
    ".gitconfig", ".gitignore", ".editorconfig",
}

# ============================================================================
# SSH — opciones comunes
# ============================================================================
# BatchMode=yes evita que ssh se quede colgado pidiendo una passphrase o
# contraseña interactiva si el host no tiene claves/agent configurado, o si
# no coincide con ningún bloque de ~/.ssh/config con sus propios timeouts
# (pinito/popeye ya definen ConnectTimeout/ServerAlive* ahí; esto es la red
# de seguridad para cualquier otro host remoto que reciba byte.py).
SSH_OPTS: List[str] = ["-o", "BatchMode=yes"]

# ============================================================================
# UTILIDADES GENERALES
# ============================================================================
def es_remoto(path: str) -> bool:
    if path.startswith('ssh://'):
        return True
    if ':' in path and not path.startswith('/') and not path.startswith('./') and not path.startswith('../'):
        return True
    return False

def remote_parse(remote: str) -> Tuple[str, str]:
    if remote.startswith('ssh://'):
        rest = remote[6:]
        if '/' in rest:
            user_host, path = rest.split('/', 1)
            return user_host, '/' + path
        return rest, ''
    parts = remote.split(':', 1)
    if len(parts) != 2:
        raise ValueError(f"Formato remoto inválido: {remote}")
    return parts[0], parts[1]

def remote_abbrev(remote: str) -> str:
    if ':' not in remote:
        return remote
    user_host, path = remote_parse(remote)
    parts = Path(path).parts
    short_path = f"…/{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else path
    return f"{user_host}:{short_path}"

def calcular_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def detectar_tipo_archivo(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read(1024)
        return "text"
    except (UnicodeDecodeError, OSError):
        return "binary"

def _resaltar(texto: str, abrev: Optional[str], long: int) -> str:
    """Reemplaza la abreviatura por [abrev] en el texto."""
    if not abrev:
        return texto
    idx = texto.find(abrev)
    if idx != -1:
        pre, lbl, post = texto[:idx], texto[idx:idx+long], texto[idx+long:]
        return f"{pre}[{lbl}]{post}"
    return f"{texto}[{abrev}]"

# ============================================================================
# UTILIDADES ASYNC
# ============================================================================
async def async_run(*cmd: str, input_data: Optional[bytes] = None) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=input_data)
    proc._stdout_data = stdout
    proc._stderr_data = stderr
    return proc

async def remote_exists_async(remote: str) -> bool:
    user_host, path = remote_parse(remote)
    proc = await async_run("ssh", *SSH_OPTS, user_host, "test", "-f", path)
    return proc.returncode == 0

async def remote_read_async(remote: str) -> bytes:
    user_host, path = remote_parse(remote)
    proc = await async_run("ssh", *SSH_OPTS, user_host, "cat", path)
    if proc.returncode != 0:
        raise RuntimeError(f"Error leyendo {remote}: {proc._stderr_data.decode()}")
    return proc._stdout_data

async def remote_write_async(remote: str, data: bytes) -> None:
    user_host, path = remote_parse(remote)
    proc = await asyncio.create_subprocess_exec(
        "ssh", *SSH_OPTS, user_host, f"cat > {shlex.quote(path)}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(input=data)
    if proc.returncode != 0:
        raise RuntimeError(f"Error escribiendo en {remote}: {stderr.decode()}")

async def remote_check_async(remote: str) -> Optional[Tuple[float, bytes]]:
    user_host, path = remote_parse(remote)
    remote_cmd = (
        f'f={shlex.quote(path)}; '
        f'if [ -f "$f" ]; then '
        f'  mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null); '
        f'  echo "$mtime" && cat "$f"; '
        f'else exit 2; fi'
    )
    proc = await async_run("ssh", *SSH_OPTS, user_host, remote_cmd)
    if proc.returncode == 2:
        return None
    if proc.returncode != 0:
        raise RuntimeError(f"Error consultando {remote}: {proc._stderr_data.decode(errors='replace')}")
    out = proc._stdout_data
    idx = out.find(b"\n")
    if idx == -1:
        raise RuntimeError(f"Respuesta inesperada de {remote}")
    mtime_str = out[:idx].decode(errors="replace").strip()
    contenido = out[idx + 1:]
    try:
        mtime = float(mtime_str)
    except ValueError:
        raise RuntimeError(f"mtime inválido de {remote}: {mtime_str!r}")
    return mtime, contenido

async def remote_home_async(user_host: str) -> str:
    """Resuelve el $HOME real del remoto en vez de asumir /home/<user>
    (falla en macOS -> /Users/, o en remotos con home no estándar)."""
    try:
        proc = await async_run("ssh", *SSH_OPTS, user_host, "sh", "-c", "echo $HOME")
        home = proc._stdout_data.decode(errors="replace").strip()
        if proc.returncode == 0 and home:
            return home
    except Exception:
        pass
    user = user_host.split('@')[0] if '@' in user_host else user_host
    return '/root' if user == 'root' else f'/home/{user}'

# ============================================================================
# REGISTRO
# ============================================================================
class Registry:
    def __init__(self, base: Path):
        self.path = base / ".byte" / "byte.json"
        self.links_path = Path.home() / ".config" / "byte" / "links.json"
        self._data: Optional[Dict] = None
        self._links: Optional[Dict] = None
        self._mtime: float = 0.0
        self._mtime_links: float = 0.0
        self._load()

    def _load_links(self) -> None:
        if not self.links_path.is_file():
            self._links = {}
            self._mtime_links = 0.0
            return
        mtime_actual = self.links_path.stat().st_mtime
        if self._links is not None and mtime_actual == self._mtime_links:
            return
        try:
            with open(self.links_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._links = data if isinstance(data, dict) else {}
            self._mtime_links = mtime_actual
        except Exception:
            self._links = {}

    def _save_links(self) -> None:
        if self._links is None:
            return
        self.links_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.links_path, "w", encoding="utf-8") as f:
            json.dump(self._links, f, indent=2, ensure_ascii=False)
        self._mtime_links = self.links_path.stat().st_mtime

    def _load(self) -> None:
        if not self.path.is_file():
            self._data = {"info": {}, "gpg": {}, "abbr_cache": {}}
            self._mtime = 0.0
            self._load_links()
            return
        mtime_actual = self.path.stat().st_mtime
        if self._data is not None and mtime_actual == self._mtime:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._mtime = mtime_actual
        except Exception:
            self._data = {"info": {}, "gpg": {}, "abbr_cache": {}}
        if self._links is None:
            self._load_links()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        self._mtime = self.path.stat().st_mtime

    def _key(self, grupo: str, stem: str) -> str:
        return f"{grupo}/{stem}"

    # --- links ---
    def add_origin(self, grupo: str, stem: str, ruta: str) -> None:
        self._load_links()
        key = self._key(grupo, stem)
        if key not in self._links:
            self._links[key] = []
        if ruta not in self._links[key]:
            self._links[key].append(ruta)
            self._save_links()

    def remove_origin(self, grupo: str, stem: str, ruta: str) -> None:
        self._load_links()
        key = self._key(grupo, stem)
        if key in self._links:
            self._links[key] = [p for p in self._links[key] if p != ruta]
            if not self._links[key]:
                del self._links[key]
            self._save_links()

    def remove_all_origins(self, grupo: str, stem: str) -> None:
        self._load_links()
        self._links.pop(self._key(grupo, stem), None)
        self._save_links()

    def get_origins(self, grupo: str, stem: str) -> List[str]:
        self._load_links()
        return self._links.get(self._key(grupo, stem), [])

    def rename_links(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load_links()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._links:
            self._links[key_dst] = self._links.pop(key_src)
            self._save_links()

    # --- info ---
    def get_info(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        return entry.get("info") if isinstance(entry, dict) else None

    def get_type(self, grupo: str, stem: str) -> str:
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        if isinstance(entry, dict) and "type" in entry:
            return entry["type"]
        return "text"

    def set_info(self, grupo: str, stem: str, texto: str) -> None:
        self._load()
        key = self._key(grupo, stem)
        if not isinstance(self._data["info"].get(key), dict):
            self._data["info"][key] = {}
        self._data["info"][key]["info"] = texto.strip()
        self._save()

    def set_type(self, grupo: str, stem: str, tipo: str) -> None:
        self._load()
        key = self._key(grupo, stem)
        if not isinstance(self._data["info"].get(key), dict):
            self._data["info"][key] = {}
        self._data["info"][key]["type"] = tipo
        self._save()

    def has_info(self, grupo: str, stem: str) -> bool:
        return self.get_info(grupo, stem) is not None

    def remove_info(self, grupo: str, stem: str) -> None:
        self._load()
        self._data["info"].pop(self._key(grupo, stem), None)
        self._save()

    def rename_info(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["info"]:
            self._data["info"][key_dst] = self._data["info"].pop(key_src)
            self._save()

    # --- gpg ---
    def mark_gpg(self, grupo: str, stem: str, key_id: str) -> None:
        self._load()
        self._data["gpg"][self._key(grupo, stem)] = key_id
        self._save()

    def unmark_gpg(self, grupo: str, stem: str) -> None:
        self._load()
        self._data["gpg"].pop(self._key(grupo, stem), None)
        self._save()

    def is_protected(self, grupo: str, stem: str) -> bool:
        self._load()
        return self._key(grupo, stem) in self._data["gpg"]

    def key_id(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        return self._data["gpg"].get(self._key(grupo, stem))

    def rename_gpg(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["gpg"]:
            self._data["gpg"][key_dst] = self._data["gpg"].pop(key_src)
            self._save()

    # --- abbr_cache (por grupo) ---
    def get_abbr_cache(self) -> Dict:
        return self._data.get("abbr_cache", {})

    def set_abbr_cache(self, abbr_cache: Dict) -> None:
        self._data["abbr_cache"] = abbr_cache
        self._save()

# ============================================================================
# ALMACENAMIENTO
# ============================================================================
class ByteStorage:
    def __init__(self, base: Path, config: Config):
        self.base = base
        self.byte_dir = base / ".byte"
        self.registry = Registry(base)
        self.versions_path = config.versions_path
        self._dir_cache: Dict[str, Tuple[float, List[Path]]] = {}

    def asegurar_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self.byte_dir.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".config" / "byte").mkdir(parents=True, exist_ok=True)
        self.versions_path.mkdir(parents=True, exist_ok=True)

    def _listar_grupo(self, grupo: str) -> List[Path]:
        gp = self.base / grupo
        if not gp.is_dir():
            self._dir_cache[grupo] = (0.0, [])
            return []
        mtime_actual = gp.stat().st_mtime
        cached_mtime, cached_files = self._dir_cache.get(grupo, (0.0, []))
        if mtime_actual != cached_mtime:
            self._dir_cache[grupo] = (
                mtime_actual,
                sorted(f for f in gp.iterdir() if not f.name.startswith(".") and f.is_file())
            )
        return self._dir_cache[grupo][1]

    def _invalidar_cache_grupo(self, grupo: str) -> None:
        self._dir_cache.pop(grupo, None)

    def normalize(self, txt: str) -> str:
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", txt) if unicodedata.category(c) != "Mn")

    def titulo(self, txt: str) -> str:
        return txt.strip().capitalize()

    def get_grupos(self) -> List[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir() and not d.name.startswith("."))

    def get_entradas(self, grupo: str) -> List[str]:
        stems = []
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            stem = Path(f.stem).stem if ext == ".gpg" else f.stem
            stems.append(stem)
        seen: set = set()
        unicos = []
        for s in stems:
            norm = self.normalize(s)
            if norm not in seen:
                seen.add(norm)
                unicos.append(s)
        return unicos

    def get_entrada_path(self, grupo: str, stem: str) -> Optional[Path]:
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            if ext == ".gpg":
                if Path(f.stem).stem == stem:
                    return f
            elif f.stem == stem:
                return f
        return None

    def get_entrada_paths_map(self, grupo: str) -> Dict[str, Path]:
        """Como get_entrada_path pero para todas las entradas del grupo a la vez,
        evitando un escaneo lineal por cada stem cuando se itera sobre varias
        entradas del mismo grupo (por ejemplo al dibujar el árbol o en --check)."""
        mapa: Dict[str, Path] = {}
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            stem = Path(f.stem).stem if ext == ".gpg" else f.stem
            if stem not in mapa:
                mapa[stem] = f
        return mapa

    def grupo_path(self, grupo: str) -> Path:
        return self.base / self.titulo(grupo)

    def entrada_path(self, grupo: str, stem: str, ext: str = ".md") -> Path:
        return self.base / self.titulo(grupo) / f"{stem}{ext}"

    def trash(self, path: Path) -> None:
        if not path.exists():
            return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        shutil.move(path, dest)

    def mtime(self, path: Optional[Path]) -> Optional[datetime]:
        if not path or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime)

    def limpiar_vacios(self) -> None:
        for g in self.get_grupos():
            gp = self.grupo_path(g)
            if gp.is_dir() and not any(f for f in gp.iterdir() if not f.name.startswith(".")):
                gp.rmdir()

    def leer_entrada(self, grupo: str, stem: str) -> Optional[bytes]:
        path = self.get_entrada_path(grupo, stem)
        if not path or not path.is_file():
            return None
        if path.suffix.lower() == ".gpg":
            try:
                tmp = self._gpg_decrypt_to_tmp(path)
                contenido = tmp.read_bytes()
                tmp.unlink()
                return contenido
            except RuntimeError:
                return None
        return path.read_bytes()

    def escribir_entrada(self, grupo: str, stem: str, contenido: bytes,
                         key_id: Optional[str] = None, cifrar: bool = True) -> None:
        ev_path = self.get_entrada_path(grupo, stem)
        if cifrar:
            if key_id is None:
                key_id = self.registry.key_id(grupo, stem)
            debe_cifrar = key_id is not None
        else:
            debe_cifrar = False
        if not ev_path:
            ev_path = self.entrada_path(grupo, stem, ext=".gpg" if debe_cifrar else ".md")
        ev_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(contenido)
            tmp_path = Path(tf.name)
        try:
            if debe_cifrar:
                self._gpg_encrypt(tmp_path, key_id, ev_path)
            else:
                shutil.copy2(tmp_path, ev_path)
        finally:
            tmp_path.unlink()
        self._invalidar_cache_grupo(grupo)

    def _gpg_encrypt(self, plain_path: Path, key_id: str, output_path: Path) -> None:
        keys = [k.strip() for k in key_id.split(",") if k.strip()] if "," in key_id else [key_id]
        out = output_path if output_path.suffix == ".gpg" else Path(str(output_path) + ".gpg")
        cmd = ["gpg", "--yes", "--batch", "--trust-model", "always"]
        for k in keys:
            cmd += ["-r", k]
        cmd += ["-o", str(out), "-e", str(plain_path)]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())
        if out != output_path:
            out.rename(output_path)

    def _gpg_decrypt_to_tmp(self, path: Path) -> Path:
        inner_ext = Path(path.stem).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(suffix=inner_ext, delete=False)
        tmp.close()
        tmp_path = Path(tmp.name)
        res = subprocess.run(
            ["gpg", "--yes", "--batch", "-o", str(tmp_path), "-d", str(path)],
            capture_output=True
        )
        if res.returncode != 0:
            tmp_path.unlink()
            raise RuntimeError(res.stderr.decode())
        return tmp_path

    # --- versionado ---
    def guardar_version(self, grupo: str, stem: str) -> Optional[Path]:
        ev_path = self.get_entrada_path(grupo, stem)
        if not ev_path or not ev_path.is_file():
            return None
        version_dir = self.versions_path / grupo / stem
        version_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_path = version_dir / f"{timestamp}{ev_path.suffix}"
        shutil.copy2(ev_path, version_path)
        return version_path

    def listar_versiones(self, grupo: str, stem: str) -> List[Path]:
        version_dir = self.versions_path / grupo / stem
        if not version_dir.is_dir():
            return []
        pattern = re.compile(r'^\d{8}_\d{6}\.[^.]+$')
        files = [f for f in version_dir.iterdir() if f.is_file() and pattern.match(f.name)]
        files.sort(key=lambda p: p.name, reverse=True)
        return files

    def restaurar_version(self, grupo: str, stem: str, version_path: Path) -> bool:
        if not version_path.is_file():
            return False
        ev_path = self.get_entrada_path(grupo, stem)
        if not ev_path:
            ev_path = self.entrada_path(grupo, stem, ext=version_path.suffix)
        contenido = version_path.read_bytes()
        key_id = self.registry.key_id(grupo, stem) if self.registry.is_protected(grupo, stem) else None
        self.escribir_entrada(grupo, stem, contenido, key_id=key_id, cifrar=bool(key_id))
        return True

# ============================================================================
# INTERFAZ
# ============================================================================
class ByteInterface:
    def __init__(self, storage: ByteStorage, columnas_default: bool = False):
        self.storage = storage
        self.registry = storage.registry
        self.columnas_default = columnas_default
        self._persistent_cache: Dict = self.registry.get_abbr_cache()
        # Cache en memoria de abreviaturas de GRUPO (no se persiste en disco:
        # es barata de recalcular y cambia poco, pero antes se recomputaba
        # en cada llamada a render_ruta(), una vez por línea impresa).
        self._grupo_abbr_cache: Dict[str, str] = {}
        self._grupo_abbr_cache_key: Optional[Tuple[str, ...]] = None

    def _get_abreviaturas(self, grupo: str, long: int = 2) -> Dict[str, str]:
        if long != 2:
            return self.calc_abreviaturas(self.storage.get_entradas(grupo), long)
        gp = self.storage.base / grupo
        if gp.is_dir():
            current_mtime = gp.stat().st_mtime
            cached = self._persistent_cache.get(grupo)
            if cached and abs(cached["mtime"] - current_mtime) < 0.001:
                return cached["abbr"]
        evs = self.storage.get_entradas(grupo)
        abbr = self.calc_abreviaturas(evs, long)
        if gp.is_dir():
            self._persistent_cache[grupo] = {"mtime": gp.stat().st_mtime, "abbr": abbr}
            self.registry.set_abbr_cache(self._persistent_cache)
        return abbr

    def _get_abreviaturas_grupos(self, long: int = 3) -> Dict[str, str]:
        """Cache en memoria (por proceso) de las abreviaturas de grupo.
        Se invalida solo si cambia la lista de grupos, evitando recalcular
        calc_abreviaturas() en cada render_ruta()."""
        grupos = self.storage.get_grupos()
        firma = tuple(grupos)
        if self._grupo_abbr_cache_key == firma:
            return self._grupo_abbr_cache
        abbr = self.calc_abreviaturas(grupos, long)
        self._grupo_abbr_cache = abbr
        self._grupo_abbr_cache_key = firma
        return abbr

    def update_all_abbreviations(self) -> None:
        self._persistent_cache = {}
        for grupo in self.storage.get_grupos():
            evs = self.storage.get_entradas(grupo)
            abbr = self.calc_abreviaturas(evs, 2)
            gp = self.storage.base / grupo
            if gp.is_dir():
                self._persistent_cache[grupo] = {"mtime": gp.stat().st_mtime, "abbr": abbr}
        self.registry.set_abbr_cache(self._persistent_cache)
        # Los grupos pudieron cambiar (crearse/eliminarse) durante el check;
        # invalidamos también el cache de abreviaturas de grupo.
        self._grupo_abbr_cache_key = None

    def invalidar_cache_abreviaturas(self, grupo: Optional[str] = None) -> None:
        if grupo is None:
            self._persistent_cache.clear()
        else:
            self._persistent_cache.pop(grupo, None)
        self.registry.set_abbr_cache(self._persistent_cache)
        # Un rename/mv/del de grupo también puede afectar la lista de grupos.
        self._grupo_abbr_cache_key = None

    def leer(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  (Interrumpido)")
            sys.exit(0)

    def calc_abreviaturas(self, lista: List[str], long: int) -> Dict[str, str]:
        max_long = max(long, 5)
        resultado: Dict[str, str] = {}
        usados: set = set()
        for item in sorted(lista, key=len):
            encontrado = None
            for l in range(long, max_long + 1):
                if len(item) < l:
                    continue
                for i in range(len(item) - l + 1):
                    sub = item[i:i+l]
                    if ' ' in sub:
                        continue
                    if sub not in usados:
                        encontrado = sub
                        break
                if encontrado:
                    break
            if encontrado is None:
                letras = [c for c in item if not c.isspace()]
                if len(letras) >= 2:
                    sub = letras[0] + letras[-1]
                    if sub not in usados:
                        encontrado = sub
                    else:
                        for i in range(len(letras)-1):
                            for j in range(i+1, len(letras)):
                                sub = letras[i] + letras[j]
                                if sub not in usados:
                                    encontrado = sub
                                    break
                            if encontrado:
                                break
                if encontrado is None:
                    import string
                    for c1 in string.ascii_lowercase:
                        for c2 in string.ascii_lowercase:
                            sub = c1 + c2
                            if sub not in usados:
                                encontrado = sub
                                break
                        if encontrado:
                            break
            resultado[item] = encontrado or item[:max_long]
            usados.add(resultado[item])
        return {item: resultado[item] for item in lista}

    def render_ruta(self, grupo: str, stem: str) -> str:
        g_abbr = self._get_abreviaturas_grupos(3)
        g_render = _resaltar(grupo, g_abbr.get(grupo), 3)
        evs = self.storage.get_entradas(grupo)
        if stem not in evs:
            evs = evs + [stem]
        e_abbr = self._get_abreviaturas(grupo)
        e_render = _resaltar(stem, e_abbr.get(stem), 2)
        return f"{g_render}/{e_render}"

    def _fmt_origin(self, path_str: str) -> str:
        if es_remoto(path_str):
            return remote_abbrev(path_str)
        p = Path(path_str)
        parts = p.parts
        return f"…/{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else path_str

    def print_dos_columnas(self, titulo_izq: str, lineas_izq: List[str],
                           titulo_der: str, lineas_der: List[str],
                           indent: str = "  ") -> None:
        """Imprime dos bloques (título + líneas) lado a lado — todo lo de
        'entrada' de un lado, todo lo de 'origen' del otro. Si no entran en
        el ancho de la terminal, cae a formato apilado en vez de cortar."""
        ancho_izq = max([len(titulo_izq)] + [len(l) for l in lineas_izq], default=0)
        ancho_der = max([len(titulo_der)] + [len(l) for l in lineas_der], default=0)
        sep = "   "
        term_width = shutil.get_terminal_size().columns
        if len(indent) + ancho_izq + len(sep) + ancho_der > term_width:
            print(f"{indent}{titulo_izq}")
            for l in lineas_izq:
                print(f"{indent}  {l}")
            print(f"{indent}{titulo_der}")
            for l in lineas_der:
                print(f"{indent}  {l}")
            return
        print(f"{indent}{titulo_izq.ljust(ancho_izq)}{sep}{titulo_der}")
        print(f"{indent}{'─' * ancho_izq}{sep}{'─' * ancho_der}")
        max_filas = max(len(lineas_izq), len(lineas_der))
        for i in range(max_filas):
            li = lineas_izq[i] if i < len(lineas_izq) else ""
            ld = lineas_der[i] if i < len(lineas_der) else ""
            print(f"{indent}{li.ljust(ancho_izq)}{sep}{ld}")

    def _get_badges_compactos(self, grupo: str, stem: str) -> str:
        r = self.storage.registry
        b1 = "g" if r.is_protected(grupo, stem) else " "
        b2 = "i" if r.has_info(grupo, stem) else " "
        origins = r.get_origins(grupo, stem)
        if origins:
            first = origins[0]
            if es_remoto(first):
                b3 = "r"
            elif Path(first).is_file():
                b3 = "c"
            else:
                b3 = "x"
        else:
            b3 = " "
        b4 = "b" if (not r.is_protected(grupo, stem) and r.get_type(grupo, stem) == "binary") else " "
        return b1 + b2 + b3 + b4

    def _render_entrada_linea(self, grupo: str, stem: str, ev_path: Optional[Path],
                              e_abbr: Dict[str, str], compact: bool = False,
                              show_info_text: bool = False) -> str:
        abbr = e_abbr.get(stem)
        event_render = _resaltar(stem, abbr, 2)
        ext_str = ""
        if ev_path and ev_path.suffix.lower() != ".gpg":
            ext_str = ev_path.suffix.lower()
        display_name = event_render + ext_str

        r = self.storage.registry
        if compact:
            return f"{self._get_badges_compactos(grupo, stem)} {display_name}"

        badges = ""
        if r.is_protected(grupo, stem):
            badges += " g"
        elif r.get_type(grupo, stem) == "binary":
            badges += " b"
        if r.has_info(grupo, stem):
            badges += " i"

        origins = r.get_origins(grupo, stem)
        origins_str = ""
        if origins:
            parts = []
            for path_str in origins:
                if es_remoto(path_str):
                    parts.append(f"r → {remote_abbrev(path_str)}")
                else:
                    disponible = Path(path_str).is_file()
                    origen_fmt = self._fmt_origin(path_str)
                    if not disponible:
                        parts.append(f"x {origen_fmt}")
                    else:
                        parts.append(f"c → {origen_fmt}")
            origins_str = " · " + " , ".join(parts)

        info_text = ""
        if show_info_text:
            info = r.get_info(grupo, stem)
            if info:
                info_text = f"  {info}"

        return f"{display_name}{badges}{origins_str}{info_text}"

    def print_arbol_columnas(self, show_dates: bool = False,
                             filter_func: Optional[Callable[[str, str], bool]] = None,
                             show_info_text: bool = False) -> None:
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        term_width = shutil.get_terminal_size().columns
        g_abbr_tmp = self._get_abreviaturas_grupos(3)

        grupos_data = []
        for g in grupos:
            evs = self.storage.get_entradas(g)
            if filter_func:
                evs = [e for e in evs if filter_func(g, e)]
            if evs:
                grupos_data.append((g, evs))

        if not grupos_data:
            print("  (vacío)")
            return

        # Renderiza cada grupo (encabezado + líneas) UNA sola vez y de paso calcula
        # su ancho de columna, en lugar de renderizar dos veces por entrada
        # (antes: una para medir en ancho_grupo() y otra para imprimir).
        render_cache: Dict[str, List[str]] = {}
        anchos: List[int] = []
        for grupo, evs in grupos_data:
            e_abbr = self._get_abreviaturas(grupo)
            paths_map = self.storage.get_entrada_paths_map(grupo)
            header = f"{_resaltar(grupo, g_abbr_tmp.get(grupo), 3)} ({len(evs)})"
            lineas = [header]
            max_ancho = len(header)
            for stem in evs:
                ev_path = paths_map.get(stem)
                linea = self._render_entrada_linea(grupo, stem, ev_path, e_abbr, compact=True,
                                                   show_info_text=show_info_text)
                lineas.append(linea)
                max_ancho = max(max_ancho, len(linea))
            render_cache[grupo] = lineas
            anchos.append(max_ancho + 2)

        n_cols = len(grupos_data)
        while n_cols > 1 and sum(anchos[:n_cols]) > term_width:
            n_cols -= 1
        grupos_en_filas = [grupos_data[i:i+n_cols] for i in range(0, len(grupos_data), n_cols)]
        anchos_en_filas = [anchos[i:i+n_cols] for i in range(0, len(anchos), n_cols)]
        sep = "  "
        for fila_grupos, fila_anchos in zip(grupos_en_filas, anchos_en_filas):
            columnas: List[List[str]] = [render_cache[grupo] for grupo, _ in fila_grupos]
            max_filas = max(len(col) for col in columnas)
            for fi in range(max_filas):
                partes = []
                for col, ancho in zip(columnas, fila_anchos):
                    celda = col[fi] if fi < len(col) else ""
                    partes.append(celda.ljust(ancho - 2))
                print(sep.join(partes).rstrip())
            print()

    def print_arbol(self, grupos_filter: Optional[List[str]] = None,
                    show_dates: bool = False, column_mode: bool = False,
                    filter_func: Optional[Callable[[str, str], bool]] = None,
                    show_info_text: bool = False) -> None:
        if column_mode:
            self.print_arbol_columnas(show_dates, filter_func=filter_func, show_info_text=show_info_text)
            return
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        g_abbr_tmp = self._get_abreviaturas_grupos(3)
        for gi, grupo in enumerate(grupos):
            evs = self.storage.get_entradas(grupo)
            if filter_func:
                evs = [e for e in evs if filter_func(grupo, e)]
            if not evs:
                continue
            if gi > 0:
                print()
            grupo_render = _resaltar(grupo, g_abbr_tmp.get(grupo), 3)
            print(f"{grupo_render} ({len(evs)})")
            e_abbr = self._get_abreviaturas(grupo)
            paths_map = self.storage.get_entrada_paths_map(grupo)
            for stem in evs:
                ev_path = paths_map.get(stem)
                line = self._render_entrada_linea(grupo, stem, ev_path, e_abbr, compact=False,
                                                  show_info_text=show_info_text)
                if show_dates:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        line += f"  {mt.strftime('%Y-%m-%d %H:%M')}"
                print(f"  {line}")
        print()

    def pedir_grupo(self, label: str = "Grupo", mostrar_arbol: bool = True) -> str:
        if mostrar_arbol:
            self.print_arbol(column_mode=self.columnas_default)
        grupos = self.storage.get_grupos()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in grupos:
                return res
            g_abbr = self._get_abreviaturas_grupos(3)
            for g, ab in g_abbr.items():
                if ab.lower() == res.lower():
                    return g
            res_norm = self.storage.normalize(res)
            for g in grupos:
                if self.storage.normalize(g) == res_norm:
                    return g
            return self.storage.titulo(res)

    def pedir_entrada(self, grupo: str, label: str = "Entrada") -> str:
        evs = self.storage.get_entradas(grupo)
        if evs:
            e_abbr = self._get_abreviaturas(grupo)
            print(f"\n  Entradas en {grupo}:")
            for e in evs:
                render = _resaltar(e, e_abbr.get(e), 2)
                print(f"    {render}")
            print()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in evs:
                return res
            e_abbr = self._get_abreviaturas(grupo)
            for e, ab in e_abbr.items():
                if ab == res:
                    return e
            res_norm = self.storage.normalize(res)
            for e in evs:
                if self.storage.normalize(e) == res_norm:
                    return e
            return res

# ============================================================================
# APLICACIÓN
# ============================================================================
class ByteApp:
    def __init__(self, config: Config):
        self.config = config
        self.storage = ByteStorage(config.base, config)
        self.ui = ByteInterface(self.storage, config.columnas_default)

    def find_grupo(self, token: str) -> Optional[str]:
        grupos = self.storage.get_grupos()
        if token in grupos:
            return token
        g_abbr = self.ui._get_abreviaturas_grupos(3)
        for g, ab in g_abbr.items():
            if ab.lower() == token.lower():
                return g
        token_norm = self.storage.normalize(token)
        for g in grupos:
            if self.storage.normalize(g) == token_norm:
                return g
        return None

    def parse_arg(self, arg: str) -> Tuple[Optional[str], Optional[str]]:
        if not arg:
            return None, None
        m = re.match(r"^([^/.]+)[/.](.+)$", arg)
        if m:
            g_raw, ev_raw = m.group(1), m.group(2)
            grupo = self.find_grupo(g_raw) or self.storage.titulo(g_raw)
            ev_base = Path(ev_raw).stem
            e_abbr = self.ui._get_abreviaturas(grupo)
            for ev, ab in e_abbr.items():
                if ab.lower() == ev_base.lower():
                    return grupo, ev
            evs = self.storage.get_entradas(grupo)
            ev_norm = self.storage.normalize(ev_base)
            for ev in evs:
                if self.storage.normalize(ev) == ev_norm:
                    return grupo, ev
            return grupo, ev_base
        m = re.match(r"^([^/]+)/$", arg)
        if m:
            return self.find_grupo(m.group(1)) or self.storage.titulo(m.group(1)), None
        return None, arg

    def resolver_arg(self, arg: str) -> Tuple[Optional[str], Optional[str]]:
        g, e = self.parse_arg(arg)
        if g and e:
            return g, e
        token = e or g
        if not token:
            return None, None
        for grupo in self.storage.get_grupos():
            evs = self.storage.get_entradas(grupo)
            if token in evs:
                return grupo, token
            e_abbr = self.ui._get_abreviaturas(grupo)
            for ev, ab in e_abbr.items():
                if ab == token:
                    return grupo, ev
            token_norm = self.storage.normalize(token)
            for ev in evs:
                if self.storage.normalize(ev) == token_norm:
                    return grupo, ev
        grupo = self.find_grupo(token)
        if grupo:
            return grupo, None
        return None, token

    def _validar_stem(self, stem: str) -> bool:
        if len(stem) < 4:
            print("El nombre debe tener al menos 4 caracteres.")
            return False
        alias = {"l", "u", "d", "m", "i", "g", "q", "c", "x", "s", "v", "r"}
        if stem in alias:
            print(f"'{stem}' es un alias de comando reservado.")
            return False
        return True

    def _fmt_version_fecha(self, vpath: Path) -> str:
        try:
            return datetime.strptime(vpath.stem, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return vpath.stem

    # --- Métodos de diff ---
    async def _diff_tool_async(self) -> Optional[str]:
        if self.config.diff_tool != "auto":
            if shutil.which(self.config.diff_tool):
                return self.config.diff_tool
        for tool in ("delta", "bat"):
            if shutil.which(tool):
                return tool
        return None

    async def mostrar_diff_async(self, a: Path, b: Path,
                                  label_a: str = "entrada (vault)",
                                  label_b: str = "origen") -> None:
        proc = await async_run("diff", "-u",
                               "-L", label_a,
                               "-L", label_b,
                               str(a), str(b))
        if not proc._stdout_data.strip():
            print("  (sin diferencias)")
            return
        tool = await self._diff_tool_async()
        diff_text = proc._stdout_data
        if tool == "delta":
            # Siempre lado a lado: todo lo de 'entrada' de un lado, todo lo
            # de 'origen' del otro. delta ajusta el ancho de cada panel solo,
            # incluso en terminales angostas.
            delta_args = ["delta", "--paging=never", "--side-by-side"]
            p = await asyncio.create_subprocess_exec(
                *delta_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await p.communicate(input=diff_text)
            print(out.decode(errors="replace"), end="")
        elif tool == "bat":
            with tempfile.NamedTemporaryFile(suffix=".diff", delete=False, mode="wb") as tf:
                tf.write(diff_text)
                tmp = Path(tf.name)
            p = await async_run("bat", "--language=diff", "--pager=never", str(tmp))
            print(p._stdout_data.decode(errors="replace"), end="")
            tmp.unlink()
        else:
            print(diff_text.decode(errors="replace"), end="")

    async def mostrar_diff_remoto_async(self, local: Path, contenido_remoto: bytes,
                                         label_a: str = "entrada (vault)",
                                         label_b: str = "origen") -> None:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.tmp', delete=False) as tf:
            tf.write(contenido_remoto)
            tmp = Path(tf.name)
        try:
            await self.mostrar_diff_async(local, tmp, label_a=label_a, label_b=label_b)
        finally:
            tmp.unlink()

    def _listar_enlaces(self) -> None:
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (sin enlaces registrados)")
            return
        self.ui.print_arbol(
            grupos_filter=grupos,
            column_mode=self.ui.columnas_default,
            filter_func=lambda g, s: bool(self.storage.registry.get_origins(g, s)),
            show_info_text=False
        )

    # --- comandos ---
    def cmd_open(self, args: List[str]) -> None:
        if not sys.stdout.isatty() and len(args) == 1:
            grupo, stem = self.resolver_arg(args[0])
            if not grupo:
                stem = args[0]
                grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
                if not grupo:
                    return
            if not stem:
                stem = self.ui.pedir_entrada(grupo)
                if not stem:
                    return
            contenido = self.storage.leer_entrada(grupo, stem)
            if contenido is None:
                print(f"Entrada no existe: {grupo}/{stem}", file=sys.stderr)
                sys.exit(1)
            sys.stdout.buffer.write(contenido)
            return
        if not args:
            grupo = self.ui.pedir_grupo()
            if not grupo:
                return
            stem = self.ui.pedir_entrada(grupo)
            if not stem:
                return
            texto = None
        else:
            token = args[0]
            texto = " ".join(args[1:]) if len(args) > 1 else None
            grupo, stem = self.resolver_arg(token)
            if not grupo:
                stem = Path(token).stem if Path(token).suffix in EXT_TEXTO | {".gpg"} else token
                if not self._validar_stem(stem):
                    return
                grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
                if not grupo:
                    return
            if not stem:
                stem = self.ui.pedir_entrada(grupo)
                if not stem:
                    return
            if not self._validar_stem(stem):
                return
        ev_path = self.storage.get_entrada_path(grupo, stem)
        if ev_path is None:
            ev_path = self.storage.entrada_path(grupo, stem, ext=".md")
        if texto and texto.strip():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            linea = f"{timestamp} {texto}\n"
            if not ev_path.is_file():
                ev_path.parent.mkdir(parents=True, exist_ok=True)
                ev_path.write_text(linea, encoding="utf-8")
                self.storage.registry.set_type(grupo, stem, "text")
                print(f"+ {self.ui.render_ruta(grupo, stem)} │ {linea.strip()}")
                return
            if ev_path.suffix.lower() == ".gpg":
                if self.storage.registry.get_type(grupo, stem) == "binary":
                    print("No se puede añadir texto a un archivo cifrado binario.")
                    return
                try:
                    tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                except RuntimeError as e:
                    print(f"GPG error: {e}")
                    return
                try:
                    contenido_actual = tmp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    print("Contenido descifrado no es texto UTF-8.")
                    tmp.unlink()
                    return
                tmp.write_text(contenido_actual + linea, encoding="utf-8")
                key_id = self.storage.registry.key_id(grupo, stem)
                if not key_id:
                    print("No hay clave GPG registrada.")
                    tmp.unlink()
                    return
                try:
                    self.storage._gpg_encrypt(tmp, key_id, ev_path)
                except Exception as e:
                    print(f"Error al cifrar: {e}")
                finally:
                    tmp.unlink()
                print(f"~ {self.ui.render_ruta(grupo, stem)} │ {linea.strip()}")
                return
            if detectar_tipo_archivo(ev_path) == "binary":
                print("No se puede añadir texto a un archivo binario.")
                return
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(linea)
            print(f"~ {self.ui.render_ruta(grupo, stem)} │ {linea.strip()}")
            return
        # abrir en editor
        if ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo = self.storage.registry.get_type(grupo, stem)
            tipo_real = detectar_tipo_archivo(ev_path)
            if tipo_real != "text":
                if tipo != tipo_real:
                    self.storage.registry.set_type(grupo, stem, tipo_real)
                ruta_fmt = self.ui.render_ruta(grupo, stem)
                print(f"\n{ruta_fmt} es un archivo binario.")
                if self.ui.leer(f"Exportar (s/N): ").lower() == "s":
                    destino = Path.cwd() / ev_path.name
                    shutil.copy2(ev_path, destino)
                    print(f"✓ Exportado a {destino}")
                return
        ruta_fmt = self.ui.render_ruta(grupo, stem)
        es_nuevo = not ev_path.is_file()
        if ev_path.suffix.lower() == ".gpg":
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                print(f"GPG error: {e}")
                return
            tipo_real = detectar_tipo_archivo(tmp)
            if not self.storage.registry.get_type(grupo, stem):
                self.storage.registry.set_type(grupo, stem, tipo_real)
            if tipo_real == "binary":
                print(f"\n{ruta_fmt} contiene datos binarios.")
                if self.ui.leer(f"¿Descifrar y exportar? (s/N): ").lower() == "s":
                    inner_ext = Path(ev_path.stem).suffix or ".bin"
                    destino = Path.cwd() / f"{stem}{inner_ext}"
                    shutil.copy2(tmp, destino)
                    print(f"✓ Exportado a {destino}")
                tmp.unlink()
                return
            mtime_antes = tmp.stat().st_mtime
            subprocess.run([self.config.editor, str(tmp)])
            if tmp.stat().st_mtime != mtime_antes:
                key_id = self.storage.registry.key_id(grupo, stem)
                try:
                    self.storage.escribir_entrada(grupo, stem, tmp.read_bytes(), key_id=key_id, cifrar=True)
                    print(f"~ {ruta_fmt} (cifrado)")
                except Exception as e:
                    print(f"Error al guardar: {e}")
            else:
                print("  (sin cambios)")
            tmp.unlink()
            return
        ev_path.parent.mkdir(parents=True, exist_ok=True)
        ev_path.touch()
        subprocess.run([self.config.editor, str(ev_path)])
        if es_nuevo and ev_path.stat().st_size == 0:
            ev_path.unlink()
            print("(Archivo vacío descartado)")
        else:
            accion = "+" if es_nuevo else "~"
            print(f"{accion} {ruta_fmt}")
            if es_nuevo:
                self.storage.registry.set_type(grupo, stem, "text")

    async def cmd_link(self, args: List[str]) -> None:
        if not args:
            self._listar_enlaces()
            return

        if len(args) == 1:
            archivo = args[0]
            grupo_hint = stem_override = None
        else:
            archivo = args[0]
            segundo = args[1]
            g_res, e_res = self.parse_arg(segundo)
            if g_res is not None and e_res is not None:
                grupo_hint, stem_override = g_res, e_res
            elif "/" in segundo:
                partes = segundo.split("/", 1)
                grupo_hint = self.find_grupo(partes[0]) or self.storage.titulo(partes[0])
                stem_override = partes[1] or None
            else:
                grupo_hint = None
                stem_override = segundo

        src_str = archivo
        if es_remoto(src_str):
            user_host, path = remote_parse(src_str)
            if path.startswith('~'):
                home = await remote_home_async(user_host)
                path = path.replace('~', home, 1)
                src_str = (f"ssh://{user_host}{path}" if src_str.startswith('ssh://')
                           else f"{user_host}:{path}")
            src_exists = await remote_exists_async(src_str)
            src_parent = None
        else:
            src = Path(src_str).expanduser().resolve()
            src_exists = src.is_file()
            src_parent = src.parent
            src_str = str(src)

        if stem_override:
            evs = self.storage.get_entradas(grupo_hint) if grupo_hint else []
            e_abbr = self.ui._get_abreviaturas(grupo_hint) if grupo_hint else {}
            encontrado = next((ev for ev, ab in e_abbr.items() if ab == stem_override.lower()), None) if grupo_hint else None
            if encontrado:
                stem, ext = encontrado, ""
            else:
                p_override = Path(stem_override)
                if p_override.suffix in EXT_TEXTO | {".gpg"}:
                    stem, ext = p_override.stem, p_override.suffix
                else:
                    stem = stem_override
                    if not es_remoto(src_str):
                        s = Path(src_str).suffix
                        ext = s.lower() if s and s.lower() in EXT_TEXTO else s
                    else:
                        ext = p_override.suffix or ".bin"
        else:
            if not es_remoto(src_str):
                src_path = Path(src_str)
                stem = src_path.stem
                s = src_path.suffix
                ext = s.lower() if s and s.lower() in EXT_TEXTO else s
            else:
                remote_path = remote_parse(src_str)[1]
                stem = Path(remote_path).stem
                ext = Path(remote_path).suffix or ""

        if not self._validar_stem(stem):
            return

        if grupo_hint:
            grupo = grupo_hint
        else:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            grupo = self.ui.pedir_grupo("Grupo para la entrada", mostrar_arbol=False)
            if not grupo:
                return

        ev_path = self.storage.get_entrada_path(grupo, stem)
        ev_exists = ev_path is not None and ev_path.is_file()
        if not ev_exists and not ext:
            if not es_remoto(src_str):
                tipo = detectar_tipo_archivo(Path(src_str))
                ext = ".md" if tipo == "text" else (Path(src_str).suffix or ".bin")
            else:
                ext = ".bin"
        if not ev_path:
            ev_path = self.storage.entrada_path(grupo, stem, ext=ext)

        if not src_exists and ev_exists:
            print(f"  El archivo externo no existe, se creará desde el vault.")
            prompt = f"  Crear {src_str} desde {grupo}/{stem}? (s/N): "
            if self.ui.leer(prompt).lower() != 's':
                return
            contenido = self.storage.leer_entrada(grupo, stem)
            if contenido is None:
                print("  Error al leer la entrada")
                return
            if es_remoto(src_str):
                await remote_write_async(src_str, contenido)
            else:
                if src_parent:
                    src_parent.mkdir(parents=True, exist_ok=True)
                Path(src_str).write_bytes(contenido)
            self.storage.registry.add_origin(grupo, stem, src_str)
            print(f"+ {self.ui.render_ruta(grupo, stem)}  → {self.ui._fmt_origin(src_str)} (copia desde vault)")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and not ev_exists:
            print(f"  La entrada {grupo}/{stem} no existe, se creará desde el archivo externo.")
            prompt = f"  Crear {grupo}/{stem} desde {src_str}? (s/N): "
            if self.ui.leer(prompt).lower() != 's':
                return
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            if es_remoto(src_str):
                try:
                    contenido = await remote_read_async(src_str)
                except Exception as e:
                    print(f"Error al descargar desde {src_str}: {e}")
                    return
                ev_path.write_bytes(contenido)
                if detectar_tipo_archivo(ev_path) == "text" and ev_path.suffix != ".md":
                    new_path = ev_path.with_suffix(".md")
                    ev_path.rename(new_path)
                    ev_path = new_path
                    print("  (detectado como texto, usando extensión .md)")
                metodo = "descarga remota"
            else:
                shutil.copy2(Path(src_str), ev_path)
                metodo = "copia"
            self.storage.registry.add_origin(grupo, stem, src_str)
            self.storage.registry.set_type(grupo, stem, detectar_tipo_archivo(ev_path))
            print(f"+ {self.ui.render_ruta(grupo, stem)}  → {self.ui._fmt_origin(src_str)}  ({metodo})")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and ev_exists:
            origins = self.storage.registry.get_origins(grupo, stem)
            if src_str in origins:
                print(f"  {self.ui.render_ruta(grupo, stem)} ya tiene este origen.")
                return
            print(f"  Conflicto: ambos archivos existen.")
            print(f"    Vault: {ev_path}")
            print(f"    Externo: {src_str}")
            while True:
                op = self.ui.leer("  [e]ntrada→origen, [o]rigen→entrada, [a]ñadir, [d]iff, [n]ada: ").lower()
                if op == 'd':
                    if es_remoto(src_str):
                        contenido_remoto = await remote_read_async(src_str)
                        await self.mostrar_diff_remoto_async(ev_path, contenido_remoto)
                    else:
                        await self.mostrar_diff_async(ev_path, Path(src_str))
                    continue
                if op == 'e':
                    if self.ui.leer(f"  ¿Sobrescribir {src_str} con la entrada? (s/N): ").lower() != 's':
                        continue
                    contenido = self.storage.leer_entrada(grupo, stem)
                    if contenido is not None:
                        if es_remoto(src_str):
                            await remote_write_async(src_str, contenido)
                        else:
                            Path(src_str).write_bytes(contenido)
                        self.storage.registry.add_origin(grupo, stem, src_str)
                        print(f"✓ {self.ui.render_ruta(grupo, stem)} → {src_str}")
                    break
                if op == 'o':
                    if self.ui.leer(f"  ¿Sobrescribir la entrada con {src_str}? (s/N): ").lower() != 's':
                        continue
                    if es_remoto(src_str):
                        contenido = await remote_read_async(src_str)
                    else:
                        contenido = Path(src_str).read_bytes()
                    key_id = self.storage.registry.key_id(grupo, stem) if self.storage.registry.is_protected(grupo, stem) else None
                    self.storage.escribir_entrada(grupo, stem, contenido, key_id=key_id, cifrar=bool(key_id))
                    self.storage.registry.add_origin(grupo, stem, src_str)
                    self.storage.registry.set_type(grupo, stem, detectar_tipo_archivo(ev_path))
                    print(f"✓ {self.ui.render_ruta(grupo, stem)} actualizado desde {src_str}")
                    break
                if op == 'a':
                    self.storage.registry.add_origin(grupo, stem, src_str)
                    print(f"+ {self.ui.render_ruta(grupo, stem)}  → {self.ui._fmt_origin(src_str)}")
                    break
                if op == 'n':
                    print("  Cancelado.")
                    break
                print("  Opción no válida.")
            return

        print("  Ni el externo ni la entrada existen.")

    def cmd_unlink(self, args: List[str]) -> None:
        if not args:
            enlazados = [
                (g, stem, self.storage.registry.get_origins(g, stem))
                for g in self.storage.get_grupos()
                for stem in self.storage.get_entradas(g)
                if self.storage.registry.get_origins(g, stem)
            ]
            if not enlazados:
                print("  No hay enlaces registrados.")
                return
            for g, stem, origins in enlazados:
                ruta_fmt = self.ui.render_ruta(g, stem)
                print(f"  {ruta_fmt}")
                for idx, origin in enumerate(origins):
                    print(f"      [{idx+1}] → {self.ui._fmt_origin(origin)}")
            entrada = self.ui.leer("Entrada a desenlazar: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        grupo, stem = self.resolver_arg(entrada)
        if not grupo or not stem:
            print(f"No encontrado: '{entrada}'")
            return
        origins = self.storage.registry.get_origins(grupo, stem)
        if not origins:
            print(f"  {self.ui.render_ruta(grupo, stem)} (sin enlaces)")
            return
        if len(origins) == 1:
            origen = origins[0]
            if self.ui.leer(f"  ¿Desenlazar {self.ui.render_ruta(grupo, stem)} de {origen}? (s/N): ").lower() == 's':
                self.storage.registry.remove_origin(grupo, stem, origen)
                print(f"  {self.ui.render_ruta(grupo, stem)}  desenlazado")
        else:
            print(f"  Múltiples orígenes:")
            for idx, origin in enumerate(origins):
                print(f"    [{idx+1}] → {self.ui._fmt_origin(origin)}")
            op = self.ui.leer("  Número, 't' todos, 'c' cancelar: ")
            if op == 'c':
                return
            if op == 't':
                if self.ui.leer(f"  ¿Eliminar todos? (s/N): ").lower() == 's':
                    self.storage.registry.remove_all_origins(grupo, stem)
                    print(f"  {self.ui.render_ruta(grupo, stem)}  todos eliminados")
                return
            if op.isdigit():
                idx = int(op) - 1
                if 0 <= idx < len(origins):
                    origen = origins[idx]
                    if self.ui.leer(f"  ¿Desenlazar {origen}? (s/N): ").lower() == 's':
                        self.storage.registry.remove_origin(grupo, stem, origen)
                        print(f"  {self.ui.render_ruta(grupo, stem)}  desenlazado")
                else:
                    print("  Número inválido.")

    def cmd_del(self, args: List[str]) -> None:
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o entrada: ")
        if not entrada:
            print("Cancelado.")
            return
        grupo, stem = self.resolver_arg(entrada)
        if grupo and not stem:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir():
                print(f"No existe el grupo '{grupo}'")
                return
            self.ui.print_arbol([grupo], column_mode=self.ui.columnas_default)
            if self.ui.leer(f"Enviar al trash '{grupo}/'? (s/N): ") == "s":
                for ev in self.storage.get_entradas(grupo):
                    self.storage.registry.remove_all_origins(grupo, ev)
                    self.storage.registry.remove_info(grupo, ev)
                    self.storage.registry.unmark_gpg(grupo, ev)
                self.storage.trash(gp)
                print(f"Enviado al trash: {grupo}/")
            return
        if not grupo or not stem:
            print(f"No encontrado: '{entrada}'")
            return
        ev_path = self.storage.get_entrada_path(grupo, stem)
        if not ev_path or not ev_path.is_file():
            print(f"No existe {grupo}/{stem}")
            return
        versiones = self.storage.listar_versiones(grupo, stem)
        ruta_fmt = self.ui.render_ruta(grupo, stem)
        if not versiones:
            if self.ui.leer(f"Enviar al trash {grupo}/{ev_path.name}? (s/N): ") == "s":
                self.storage.registry.remove_all_origins(grupo, stem)
                self.storage.registry.remove_info(grupo, stem)
                self.storage.registry.unmark_gpg(grupo, stem)
                self.storage.trash(ev_path)
                print(f"- {ruta_fmt}")
            self.storage.limpiar_vacios()
            return
        print(f"\nLa entrada {ruta_fmt} tiene {len(versiones)} versiones.")
        print("  [t] Borrar todo  [v] Borrar versión  [c] Cancelar")
        op = self.ui.leer("  Elige (t/v/c): ").lower()
        if op == 'c' or not op:
            print("Cancelado.")
            return
        if op == 't':
            if self.ui.leer(f"¿Eliminar {ruta_fmt} y todas sus versiones? (s/N): ").lower() == 's':
                version_dir = self.storage.versions_path / grupo / stem
                if version_dir.is_dir():
                    shutil.rmtree(version_dir)
                self.storage.registry.remove_all_origins(grupo, stem)
                self.storage.registry.remove_info(grupo, stem)
                self.storage.registry.unmark_gpg(grupo, stem)
                self.storage.trash(ev_path)
                print(f"- {ruta_fmt} (y versiones)")
                self.storage.limpiar_vacios()
            else:
                print("Cancelado.")
        elif op == 'v':
            self._borrar_version_interactivo(grupo, stem, versiones)

    def _borrar_version_interactivo(self, grupo: str, stem: str, versiones: List[Path]) -> None:
        if len(versiones) == 1:
            vpath = versiones[0]
            fecha = self._fmt_version_fecha(vpath)
            if self.ui.leer(f"  ¿Eliminar la única versión ({fecha})? (s/N): ").lower() == 's':
                self._unlink_version(vpath)
                print(f"✓ Eliminada versión {fecha}")
            else:
                print("Cancelado.")
            return
        print(f"\nVersiones:")
        for i, vpath in enumerate(versiones, 1):
            print(f"  [{i}] {self._fmt_version_fecha(vpath)}")
        seleccion = self.ui.leer("  Número a eliminar (o 'c'): ")
        if seleccion.lower() == 'c' or not seleccion:
            print("Cancelado.")
            return
        if seleccion.isdigit():
            idx = int(seleccion) - 1
            if 0 <= idx < len(versiones):
                vpath = versiones[idx]
                fecha = self._fmt_version_fecha(vpath)
                if self.ui.leer(f"  ¿Eliminar versión {fecha}? (s/N): ").lower() == 's':
                    self._unlink_version(vpath)
                    print(f"✓ Eliminada {fecha}")
                else:
                    print("Cancelado.")
            else:
                print("Índice inválido.")

    def _unlink_version(self, vpath: Path) -> None:
        vpath.unlink()
        parent = vpath.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            gp = parent.parent
            if gp.is_dir() and not any(gp.iterdir()):
                gp.rmdir()

    def _renombrar(self, grupo: str, origen: str, destino: str) -> None:
        if not self._validar_stem(destino):
            return
        p_src = self.storage.get_entrada_path(grupo, origen)
        if not p_src:
            print(f"No existe: {grupo}/{origen}")
            return
        p_dest = self.storage.entrada_path(grupo, destino, ext=p_src.suffix)
        if p_dest.is_file():
            if self.ui.leer(f"Ya existe {grupo}/{destino}. ¿Sobrescribir? (s/N): ").lower() != 's':
                return
            self.storage.trash(p_dest)
        shutil.move(p_src, p_dest)
        self.storage.registry.rename_links(grupo, origen, grupo, destino)
        self.storage.registry.rename_info(grupo, origen, grupo, destino)
        if self.storage.registry.is_protected(grupo, origen):
            self.storage.registry.mark_gpg(grupo, destino, self.storage.registry.key_id(grupo, origen))
            self.storage.registry.unmark_gpg(grupo, origen)
        self.storage._invalidar_cache_grupo(grupo)
        self.ui.invalidar_cache_abreviaturas(grupo)
        print(f"✓ Renombrado: {grupo}/{origen} → {grupo}/{destino}")

    def _mover(self, g_src: str, e_src: str, g_dest: str, e_dest: str) -> None:
        if not self._validar_stem(e_dest):
            return
        p_src = self.storage.get_entrada_path(g_src, e_src)
        if not p_src or not p_src.is_file():
            print(f"No existe: {g_src}/{e_src}")
            return
        p_dest = self.storage.entrada_path(g_dest, e_dest, ext=p_src.suffix)
        if p_dest.is_file():
            self._fusionar(g_dest, e_dest, g_src, e_src)
            return
        p_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(p_src, p_dest)
        for o in self.storage.registry.get_origins(g_src, e_src):
            self.storage.registry.add_origin(g_dest, e_dest, o)
        self.storage.registry.remove_all_origins(g_src, e_src)
        info_txt = self.storage.registry.get_info(g_src, e_src)
        if info_txt:
            self.storage.registry.set_info(g_dest, e_dest, info_txt)
            self.storage.registry.remove_info(g_src, e_src)
        tipo = self.storage.registry.get_type(g_src, e_src)
        if tipo:
            self.storage.registry.set_type(g_dest, e_dest, tipo)
        if self.storage.registry.is_protected(g_src, e_src):
            self.storage.registry.mark_gpg(g_dest, e_dest, self.storage.registry.key_id(g_src, e_src))
            self.storage.registry.unmark_gpg(g_src, e_src)
        for g in (g_src, g_dest):
            self.storage._invalidar_cache_grupo(g)
            self.ui.invalidar_cache_abreviaturas(g)
        self.storage.limpiar_vacios()
        print(f"✓ Movido: {self.ui.render_ruta(g_src, e_src)} ➔ {self.ui.render_ruta(g_dest, e_dest)}")

    def _fusionar(self, g_dest: str, e_dest: str, g_src: str, e_src: str) -> None:
        if g_src == g_dest and e_src == e_dest:
            print("No se puede fusionar consigo mismo.")
            return
        p_src = self.storage.get_entrada_path(g_src, e_src)
        p_dest = self.storage.get_entrada_path(g_dest, e_dest)
        if not p_src or not p_src.is_file():
            print(f"No existe: {g_src}/{e_src}")
            return
        p_dest_real = p_dest or self.storage.entrada_path(g_dest, e_dest)
        p_dest_real.parent.mkdir(parents=True, exist_ok=True)
        contenido_src = p_src.read_bytes()
        if p_dest_real.is_file():
            contenido_dest = p_dest_real.read_bytes()
            sep = b"\n\n---\n\n"
            p_dest_real.write_bytes(contenido_dest + sep + contenido_src if contenido_dest.strip() else contenido_src)
        else:
            p_dest_real.write_bytes(contenido_src)
        for o in self.storage.registry.get_origins(g_src, e_src):
            self.storage.registry.add_origin(g_dest, e_dest, o)
        self.storage.registry.remove_all_origins(g_src, e_src)
        if not self.storage.registry.get_info(g_dest, e_dest):
            self.storage.registry.rename_info(g_src, e_src, g_dest, e_dest)
        else:
            self.storage.registry.remove_info(g_src, e_src)
        if self.storage.registry.is_protected(g_src, e_src) and not self.storage.registry.is_protected(g_dest, e_dest):
            self.storage.registry.mark_gpg(g_dest, e_dest, self.storage.registry.key_id(g_src, e_src))
        self.storage.registry.unmark_gpg(g_src, e_src)
        p_src.unlink()
        for g in (g_src, g_dest):
            self.storage._invalidar_cache_grupo(g)
            self.ui.invalidar_cache_abreviaturas(g)
        self.storage.limpiar_vacios()
        print(f"✓ Fusionado: {self.ui.render_ruta(g_src, e_src)} ➔ {self.ui.render_ruta(g_dest, e_dest)}")

    def cmd_mv(self, args: List[str]) -> None:
        if not args:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            opcion = self.ui.leer("¿[m]over o [f]usionar? (m/f): ").lower()
            if opcion == "f":
                g_dest = self.ui.pedir_grupo("Grupo destino", mostrar_arbol=False)
                e_dest = self.ui.pedir_entrada(g_dest, "Entrada destino")
                g_src = self.ui.pedir_grupo("Grupo fuente", mostrar_arbol=False)
                e_src = self.ui.pedir_entrada(g_src, "Entrada fuente")
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                g_src = self.ui.pedir_grupo("Grupo origen", mostrar_arbol=False)
                e_src = self.ui.pedir_entrada(g_src, "Entrada origen")
                g_dest = self.ui.pedir_grupo("Grupo destino", mostrar_arbol=False)
                nuevo = self.ui.leer(f"Nuevo nombre (Enter = '{e_src}'): ") or e_src
                if g_src == g_dest and self.storage.normalize(e_src) != self.storage.normalize(nuevo):
                    self._renombrar(g_src, e_src, nuevo)
                else:
                    self._mover(g_src, e_src, g_dest, nuevo)
            return
        if len(args) == 1:
            print("Uso: byte --mv [origen] [destino]")
            return
        g_origen, e_origen = self.resolver_arg(args[0])
        if not g_origen or not e_origen:
            print(f"Origen no encontrado: '{args[0]}'")
            return
        destino_arg = args[1]
        if destino_arg.endswith("/"):
            g_dest = self.find_grupo(destino_arg.rstrip("/")) or self.storage.titulo(destino_arg.rstrip("/"))
            self._mover(g_origen, e_origen, g_dest, e_origen)
            return
        if "/" in destino_arg:
            partes = destino_arg.split("/", 1)
            g_dest = self.find_grupo(partes[0]) or self.storage.titulo(partes[0])
            e_dest = Path(partes[1]).stem
            if not self._validar_stem(e_dest):
                return
            self._mover(g_origen, e_origen, g_dest, e_dest)
            return
        e_dest = Path(destino_arg).stem
        if not self._validar_stem(e_dest):
            return
        if self.storage.normalize(e_dest) == self.storage.normalize(e_origen) and e_dest != e_origen:
            self._renombrar(g_origen, e_origen, e_dest)
            return
        p_dest = self.storage.get_entrada_path(g_origen, e_dest)
        if p_dest and p_dest.is_file():
            self._fusionar(g_origen, e_dest, g_origen, e_origen)
        else:
            self._mover(g_origen, e_origen, g_origen, e_dest)

    def _clave_existe(self, clave: str) -> bool:
        try:
            result = subprocess.run(
                ["gpg", "--list-keys", "--with-colons", clave],
                capture_output=True, text=True
            )
            return result.returncode == 0 and ("pub" in result.stdout or "uid" in result.stdout)
        except Exception:
            return False

    def cmd_gpg(self, args: List[str]) -> None:
        if not shutil.which("gpg"):
            print("gpg no disponible.")
            return
        if not args:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            entrada = self.ui.leer("Entrada: ")
            if not entrada:
                return
            extra_keys: List[str] = []
        else:
            entrada = args[0]
            extra_keys = list(args[1:])
        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'", mostrar_arbol=False)
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_entrada(grupo)
            if not stem:
                return
        ev_path = self.storage.get_entrada_path(grupo, stem)
        ruta_fmt = self.ui.render_ruta(grupo, stem)
        ya_cifrado = ev_path and ev_path.suffix.lower() == ".gpg"
        if ya_cifrado:
            key_actual = self.storage.registry.key_id(grupo, stem) or self.config.gpg_key
            actuales = [k for k in (key_actual or "").split(",") if k]
            if actuales:
                print(f"  g destinatarios actuales:")
                for k in actuales:
                    etiq = "primaria" if k == self.config.gpg_key else "secundaria"
                    print(f"    {k}  {etiq}")
            nuevas = list(extra_keys)
            while True:
                resp = self.ui.leer(f"  Añadir llave secundaria (Enter termina): ")
                if not resp:
                    break
                nuevas.append(resp)
            if not nuevas:
                print("  Sin cambios.")
                return
            todos = list(actuales)
            for k in nuevas:
                if k not in todos:
                    todos.append(k)
            validas = [k for k in todos if self._clave_existe(k)]
            invalidas = [k for k in todos if k not in validas]
            if invalidas:
                print("Claves no encontradas (ignoradas):")
                for k in invalidas:
                    print(f"  {k}")
                if not validas:
                    print("Sin claves válidas. Cancelado.")
                    return
                if self.ui.leer(f"  Continuar con las válidas? (s/N): ").lower() != 's':
                    return
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                print(f"GPG error: {e}")
                return
            key_id_str = ",".join(validas)
            try:
                self.storage._gpg_encrypt(tmp, key_id_str, ev_path)
            except Exception as e:
                print(f"Error al re-cifrar: {e}")
                tmp.unlink()
                return
            tmp.unlink()
            self.storage.registry.mark_gpg(grupo, stem, key_id_str)
            print(f"~ {ruta_fmt}  g → {' '.join(validas)}")
            return
        if not self.config.gpg_key:
            print("Sin llave primaria configurada. Usa 'byte x' para configurarla.")
            return
        if ev_path and ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo_real = detectar_tipo_archivo(ev_path)
            if self.storage.registry.get_type(grupo, stem) != tipo_real:
                self.storage.registry.set_type(grupo, stem, tipo_real)
        all_keys = [self.config.gpg_key] + [k for k in self.config.gpg_keys_secondary if k != self.config.gpg_key]
        validas = [k for k in all_keys if self._clave_existe(k)]
        invalidas = [k for k in all_keys if k not in validas]
        if invalidas:
            print("Claves no encontradas (ignoradas):")
            for k in invalidas:
                print(f"  {k}")
            if not validas:
                print("Sin claves válidas. Cancelado.")
                return
            if self.ui.leer(f"  Continuar? (s/N): ").lower() != 's':
                return
        if not ev_path or not ev_path.is_file():
            ev_path = self.storage.entrada_path(grupo, stem, ext=".md")
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            ev_path.touch()
            self.storage.registry.set_type(grupo, stem, "text")
        output_path = Path(str(ev_path) + ".gpg")
        key_id_str = ",".join(validas)
        try:
            self.storage._gpg_encrypt(ev_path, key_id_str, output_path)
        except Exception as e:
            print(f"GPG error: {e}")
            return
        ev_path.unlink()
        self.storage.registry.mark_gpg(grupo, stem, key_id_str)
        print(f"~ {ruta_fmt}  g {self.config.gpg_key}")

    def cmd_nogpg(self, args: List[str]) -> None:
        if not shutil.which("gpg"):
            print("gpg no disponible.")
            return
        if not args:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            entrada = self.ui.leer("Entrada a desproteger: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'", mostrar_arbol=False)
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_entrada(grupo)
            if not stem:
                return
        ev_path = self.storage.get_entrada_path(grupo, stem)
        if not ev_path or ev_path.suffix.lower() != ".gpg":
            print(f"  {self.ui.render_ruta(grupo, stem)} no está cifrado.")
            return
        if self.ui.leer(f"  ¿Descifrar {grupo}/{stem}? (s/N): ") != "s":
            return
        try:
            tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
        except RuntimeError as e:
            print(f"GPG error: {e}")
            return
        inner_ext = Path(ev_path.stem).suffix or ".md"
        clear_path = ev_path.parent / f"{stem}{inner_ext}"
        shutil.move(tmp, clear_path)
        ev_path.unlink()
        self.storage.registry.unmark_gpg(grupo, stem)
        self.storage.registry.set_type(grupo, stem, detectar_tipo_archivo(clear_path))
        self.storage._invalidar_cache_grupo(grupo)
        self.ui.invalidar_cache_abreviaturas(grupo)
        print(f"~ {self.ui.render_ruta(grupo, stem)} (descifrado)")

    async def cmd_check(self, args: List[str]) -> None:
        print("\n=== CONFIGURACIÓN ===")
        if self.config.used_config_path:
            print(f"Archivo: {str(self.config.used_config_path).replace(str(Path.home()), '~')}")
        print(f"Directorio: {str(self.storage.base).replace(str(Path.home()), '~')}")
        print(f"Editor: {self.config.editor}")
        print(f"Clave GPG primaria: {self.config.gpg_key or '(no configurada)'}")
        if self.config.gpg_keys_secondary:
            print(f"Claves GPG secundarias: {', '.join(self.config.gpg_keys_secondary)}")
        print()
        tipo_cambiado = False
        # get_entrada_paths_map una vez por grupo en lugar de get_entrada_path
        # (escaneo lineal del directorio) una vez por entrada dentro del bucle.
        for g in self.storage.get_grupos():
            paths_map = self.storage.get_entrada_paths_map(g)
            for stem in self.storage.get_entradas(g):
                ev_path = paths_map.get(stem)
                if not ev_path or ev_path.suffix.lower() == ".gpg":
                    continue
                tipo_reg = self.storage.registry.get_type(g, stem)
                tipo_real = detectar_tipo_archivo(ev_path)
                if tipo_reg != tipo_real:
                    if not tipo_cambiado:
                        print("Verificando tipos...")
                        tipo_cambiado = True
                    print(f"  {self.ui.render_ruta(g, stem)}: registrado '{tipo_reg}' pero es '{tipo_real}'")
                    if self.ui.leer(f"  ¿Actualizar? (s/N): ").lower() == "s":
                        self.storage.registry.set_type(g, stem, tipo_real)
                        print(f"    ✓ Actualizado")
        if tipo_cambiado:
            print()
        candidatos = []
        for g in self.storage.get_grupos():
            paths_map = self.storage.get_entrada_paths_map(g)
            for stem in self.storage.get_entradas(g):
                ev_path = paths_map.get(stem)
                if not ev_path:
                    continue
                for origin in self.storage.registry.get_origins(g, stem):
                    candidatos.append((g, stem, ev_path, origin))
        if not candidatos:
            print("No hay enlaces registrados.")
            self.ui.update_all_abbreviations()
            print("Caché de abreviaturas actualizada.")
            return

        async def verificar(g: str, stem: str, ev_path: Path, src_str: str):
            if not es_remoto(src_str):
                src = Path(src_str)
                if not src.is_file():
                    return g, stem, ev_path, src_str, None, None, None
                es_gpg = ev_path.suffix.lower() == ".gpg"
                if es_gpg:
                    contenido_ev = self.storage.leer_entrada(g, stem)
                    if contenido_ev is None:
                        return g, stem, ev_path, src_str, None, None, "gpg_error"
                    diff = contenido_ev != src.read_bytes()
                else:
                    def archivos_iguales(a: Path, b: Path) -> bool:
                        return a.stat().st_size == b.stat().st_size and calcular_md5(a) == calcular_md5(b)
                    diff = not archivos_iguales(ev_path, src)
                return g, stem, ev_path, src_str, diff, None, None
            else:
                try:
                    resultado = await remote_check_async(src_str)
                except Exception as e:
                    return g, stem, ev_path, src_str, None, None, str(e)
                if resultado is None:
                    return g, stem, ev_path, src_str, None, None, None
                mtime_remoto, contenido_remoto = resultado
                es_gpg = ev_path.suffix.lower() == ".gpg"
                if es_gpg:
                    contenido_ev = self.storage.leer_entrada(g, stem)
                    if contenido_ev is None:
                        return g, stem, ev_path, src_str, None, None, "gpg_error"
                    diff = contenido_ev != contenido_remoto
                else:
                    diff = ev_path.read_bytes() != contenido_remoto
                return g, stem, ev_path, src_str, diff, (mtime_remoto, contenido_remoto), None

        # Semáforo: acota cuántas conexiones (remotas en particular) se abren
        # en paralelo. No cambia el resultado, solo evita saturar la red o
        # abrir demasiados procesos ssh a la vez si hay muchos enlaces.
        sem = asyncio.Semaphore(4)

        async def verificar_limitado(g: str, stem: str, ev_path: Path, src_str: str):
            async with sem:
                return await verificar(g, stem, ev_path, src_str)

        resultados = await asyncio.gather(
            *[verificar_limitado(g, stem, ep, o) for g, stem, ep, o in candidatos]
        )
        cambios = []
        for g, stem, ev_path, src_str, diff, remote_data, error in resultados:
            if error == "gpg_error":
                print(f"{g}/{stem} — no se pudo descifrar (GPG), omitido.")
            elif error is not None:
                print(f"{g}/{stem} — error: {error}")
            elif diff is None:
                print(f"{g}/{stem} → origen no disponible: {self.ui._fmt_origin(src_str)} (omitido)")
            elif diff:
                cambios.append((g, stem, ev_path, src_str, remote_data))
        for g, stem, ev_path, src_str, remote_data in cambios:
            es_gpg = ev_path.suffix.lower() == ".gpg"
            ruta_fmt = self.ui.render_ruta(g, stem)
            origen_fmt = self.ui._fmt_origin(src_str)
            gpg_tag = " g" if es_gpg else ""
            mtime_ev = datetime.fromtimestamp(ev_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if not es_remoto(src_str):
                mtime_src = datetime.fromtimestamp(Path(src_str).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                contenido_remoto_cache = None
            else:
                mtime_remoto, contenido_remoto_cache = remote_data
                mtime_src = datetime.fromtimestamp(mtime_remoto).strftime("%Y-%m-%d %H:%M")
            print(f"\n{ruta_fmt}{gpg_tag}  (modificado)")
            self.ui.print_dos_columnas(
                "ENTRADA (vault)", [ruta_fmt, mtime_ev],
                "ORIGEN", [origen_fmt, mtime_src],
            )
            print()
            es_bin = (not es_gpg and self.storage.registry.get_type(g, stem) == "binary") or \
                     (not es_remoto(src_str) and detectar_tipo_archivo(Path(src_str)) == "binary")
            label_entrada = f"ENTRADA (vault)  {mtime_ev}"
            label_origen  = f"ORIGEN           {mtime_src}"
            while True:
                if es_bin:
                    res = self.ui.leer("  [o] origen→entrada, [e] entrada→origen, [m]d5, [N]o: ").lower()
                else:
                    res = self.ui.leer("  [o] origen→entrada, [e] entrada→origen, [d]iff, [N]o: ").lower()
                if res == "m" and es_bin:
                    if es_remoto(src_str):
                        md5_src = hashlib.md5(contenido_remoto_cache).hexdigest()
                    else:
                        md5_src = calcular_md5(Path(src_str))
                    if es_gpg:
                        tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        md5_ev = calcular_md5(tmp)
                        tmp.unlink()
                    else:
                        md5_ev = calcular_md5(ev_path)
                    self.ui.print_dos_columnas(
                        "ENTRADA (vault)", [md5_ev],
                        "ORIGEN", [md5_src],
                    )
                elif res == "d" and not es_bin:
                    if es_remoto(src_str):
                        await self.mostrar_diff_remoto_async(
                            ev_path, contenido_remoto_cache,
                            label_a=label_entrada,
                            label_b=label_origen,
                        )
                    else:
                        await self.mostrar_diff_async(
                            ev_path, Path(src_str),
                            label_a=label_entrada,
                            label_b=label_origen,
                        )
                elif res == "o":
                    if es_remoto(src_str):
                        self.storage.escribir_entrada(g, stem, contenido_remoto_cache, cifrar=bool(es_gpg))
                    else:
                        shutil.copy2(Path(src_str), ev_path)
                    self.storage._invalidar_cache_grupo(g)
                    self.ui.invalidar_cache_abreviaturas(g)
                    if not es_gpg:
                        self.storage.registry.set_type(g, stem, detectar_tipo_archivo(ev_path))
                    print(f"  ✓ Entrada actualizada")
                    break
                elif res == "e":
                    if es_remoto(src_str):
                        try:
                            contenido = self.storage.leer_entrada(g, stem)
                            if contenido is None:
                                print("Error leyendo entrada")
                                break
                            await remote_write_async(src_str, contenido)
                            print(f"  ✓ Origen remoto actualizado")
                        except Exception as e:
                            print(f"Error subiendo: {e}")
                    else:
                        shutil.copy2(ev_path, Path(src_str))
                        print(f"  ✓ Origen actualizado")
                    break
                else:
                    print("  Omitido")
                    break
        print("Revisión completada.")
        self.ui.update_all_abbreviations()
        print("Caché de abreviaturas actualizada.")

    def _mostrar_info_entrada(self, grupo: str, stem: str) -> None:
        """Vista detallada de una entrada:
        - sin nota: nombre completo (sin abreviar) + ruta básica desde el vault.
        - con nota: nota primero, luego badges (cifrado/binario/versiones),
          enlaces y versiones, todo en secciones separadas y legibles."""
        r = self.storage.registry
        ev_path = self.storage.get_entrada_path(grupo, stem)
        txt = r.get_info(grupo, stem)
        origins = r.get_origins(grupo, stem)
        protegido = r.is_protected(grupo, stem)
        tipo = r.get_type(grupo, stem)
        versiones = self.storage.listar_versiones(grupo, stem)

        if ev_path:
            ext = ev_path.suffix if ev_path.suffix.lower() != ".gpg" else (Path(ev_path.stem).suffix or "")
        else:
            ext = ""
        nombre_completo = f"{grupo}/{stem}{ext}"

        print()
        if not txt:
            print(f"{nombre_completo}")
            print("  (sin nota)")
        else:
            print(f"{nombre_completo}")
            print(f"  {txt}")

        etiquetas = []
        if protegido:
            etiquetas.append(f"g cifrado → {r.key_id(grupo, stem)}")
        if tipo == "binary":
            etiquetas.append("b binario")
        if versiones:
            etiquetas.append(f"{len(versiones)} versión(es)")
        if etiquetas:
            print(f"  {'  ·  '.join(etiquetas)}")

        if origins:
            print(f"\n  Enlaces:")
            for o in origins:
                if es_remoto(o):
                    marca = "r"
                elif Path(o).is_file():
                    marca = "c"
                else:
                    marca = "x"
                print(f"    [{marca}] {self.ui._fmt_origin(o)}")

        if versiones:
            print(f"\n  Versiones:")
            for i, vpath in enumerate(versiones, 1):
                print(f"    [{i}] {self._fmt_version_fecha(vpath)}")
        print()

    def cmd_info(self, args: List[str]) -> None:
        if not args:
            grupos = self.storage.get_grupos()
            if not grupos:
                print("  (vacío)")
                return
            self.ui.print_arbol(
                grupos_filter=grupos,
                column_mode=self.ui.columnas_default,
                filter_func=lambda g, s: self.storage.registry.has_info(g, s) or bool(self.storage.registry.get_origins(g, s)),
                show_info_text=True
            )
            return
        grupo, stem = self.resolver_arg(args[0])
        if grupo is None:
            print(f"No encontrado: '{args[0]}'")
            return
        if stem is not None:
            if len(args) >= 2:
                ruta_fmt = self.ui.render_ruta(grupo, stem)
                self.storage.registry.set_info(grupo, stem, " ".join(args[1:]))
                print(f"Nota guardada para {ruta_fmt}")
                return
            self._mostrar_info_entrada(grupo, stem)
            return
        evs = self.storage.get_entradas(grupo)
        if not evs:
            print(f"El grupo {grupo} no tiene entradas.")
            return
        print(f"\nGrupo: {grupo}")
        for s in evs:
            ruta_fmt = self.ui.render_ruta(grupo, s)
            txt = self.storage.registry.get_info(grupo, s) or "(sin nota)"
            badges = self.ui._get_badges_compactos(grupo, s)
            versiones = self.storage.listar_versiones(grupo, s)
            ver_str = f" [{len(versiones)}v]" if versiones else ""
            print(f"  {badges} {ruta_fmt}: {txt}{ver_str}")

    def cmd_config(self, args: List[str]) -> None:
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        target = system_path if system_path.is_file() else self.config.base / ".byte" / "byte.toml"
        disp = str(target).replace(str(Path.home()), "~")
        print(f"\nBYTE — Configuración")
        print(f"Archivo: {disp}\n")
        print(f"Vista por columnas: {'sí' if self.config.columnas_default else 'no'}")
        print(f"Buscar en cifrados: {'sí' if self.config.search_encrypted else 'no'}\n")
        resp = self.ui.leer(f"Directorio base [{self.config.base}]: ")
        nueva_base = Path(resp).expanduser().resolve() if resp else self.config.base
        resp = self.ui.leer(f"Editor [{self.config.editor}]: ")
        nuevo_editor = resp or self.config.editor
        print(f"\nLlave GPG primaria")
        resp = self.ui.leer(f"[{self.config.gpg_key or 'ninguna'}]: ")
        nueva_primaria = resp or self.config.gpg_key
        print(f"\nLlaves secundarias actuales:")
        if self.config.gpg_keys_secondary:
            for k in self.config.gpg_keys_secondary:
                print(f"  [{k}]")
        else:
            print(f"  (ninguna)")
        nuevas_sec = []
        resp = ""
        while True:
            resp = self.ui.leer(f"Nueva llave (vacío termina, '-' borra todas): ")
            if not resp:
                break
            if resp == "-":
                nuevas_sec = []
                print("Secundarias eliminadas.")
                break
            if "@" in resp and "." in resp.split("@")[1]:
                nuevas_sec.append(resp)
            else:
                print("Formato inválido.")
        if not nuevas_sec and resp != "-":
            nuevas_sec = list(self.config.gpg_keys_secondary)
        resp_col = self.ui.leer(f"¿Columnas por defecto? (s/N): ").lower()
        nuevas_columnas = resp_col == "s"
        resp_enc = self.ui.leer(f"¿Buscar en cifrados? (s/N): ").lower()
        nuevas_search_enc = resp_enc == "s"
        resp = self.ui.leer(f"Ruta versiones [{self.config.versions_path}]: ")
        nuevas_versions = Path(resp).expanduser().resolve() if resp else self.config.versions_path
        print(f"\nHerramienta para diff (auto/delta/bat/diff)")
        resp_diff = self.ui.leer(f"[{self.config.diff_tool}]: ").strip().lower()
        nuevo_diff = resp_diff if resp_diff in ("auto", "delta", "bat", "diff") else self.config.diff_tool
        self.config.save(nueva_base, nuevo_editor, nueva_primaria, nuevas_sec,
                         nuevas_columnas, nuevas_search_enc, nuevas_versions, nuevo_diff)
        self.storage = ByteStorage(self.config.base, self.config)
        self.ui = ByteInterface(self.storage, self.config.columnas_default)
        print(f"✓ Guardado en {disp}")

    def cmd_version(self, args: List[str]) -> None:
        if not args:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            entrada = self.ui.leer("Entrada: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'", mostrar_arbol=False)
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_entrada(grupo)
            if not stem:
                return
        ev_path = self.storage.get_entrada_path(grupo, stem)
        if not ev_path or not ev_path.is_file():
            print(f"La entrada {grupo}/{stem} no existe.")
            return
        version_path = self.storage.guardar_version(grupo, stem)
        if version_path:
            print(f"✓ Versión guardada: {self.ui.render_ruta(grupo, stem)} → {self._fmt_version_fecha(version_path)}")
        else:
            print("Error al guardar la versión.")

    async def cmd_restore(self, args: List[str]) -> None:
        if not args:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            entrada = self.ui.leer("Entrada: ")
            if not entrada:
                return
            seleccion = ""
        else:
            entrada = args[0]
            seleccion = args[1] if len(args) > 1 else ""
        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'", mostrar_arbol=False)
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_entrada(grupo)
            if not stem:
                return
        versiones = self.storage.listar_versiones(grupo, stem)
        if not versiones:
            print(f"No hay versiones para {grupo}/{stem}.")
            return
        if seleccion:
            if seleccion.isdigit():
                idx = int(seleccion) - 1
                if 0 <= idx < len(versiones):
                    version_elegida = versiones[idx]
                else:
                    print("Índice inválido.")
                    return
            else:
                matching = [v for v in versiones if v.stem.startswith(seleccion)]
                if matching:
                    version_elegida = matching[0]
                else:
                    print(f"No se encontró versión '{seleccion}'.")
                    return
        else:
            print(f"\nVersiones de {self.ui.render_ruta(grupo, stem)}:")
            for i, vpath in enumerate(versiones, 1):
                print(f"  [{i}] {self._fmt_version_fecha(vpath)}")
            print()
            op = self.ui.leer("  Número, 'd' diff, 'c' cancelar: ")
            if op.lower() == 'c' or not op:
                print("Cancelado.")
                return
            if op.lower() == 'd':
                ev_actual = self.storage.get_entrada_path(grupo, stem)
                if ev_actual and ev_actual.is_file():
                    fecha_actual = datetime.fromtimestamp(ev_actual.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    fecha_version = self._fmt_version_fecha(versiones[0])
                    await self.mostrar_diff_async(
                        ev_actual, versiones[0],
                        label_a=f"actual           {fecha_actual}",
                        label_b=f"versión más rec. {fecha_version}",
                    )
                return await self.cmd_restore([entrada])
            if not op.isdigit():
                print("Opción inválida.")
                return
            idx = int(op) - 1
            if idx < 0 or idx >= len(versiones):
                print("Índice inválido.")
                return
            version_elegida = versiones[idx]
        fecha = self._fmt_version_fecha(version_elegida)
        print(f"\n  Versión elegida: {fecha}")
        if self.ui.leer(f"  ¿Restaurar? (s/N): ").lower() != 's':
            print("Cancelado.")
            return
        if self.storage.restaurar_version(grupo, stem, version_elegida):
            print(f"✓ Restaurada {fecha} en {self.ui.render_ruta(grupo, stem)}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
        else:
            print("Error al restaurar.")

    def cmd_search(self, args: List[str]) -> None:
        if not args:
            print("Uso: byte s <patrón> [grupo/]")
            return
        pattern = args[0]
        grupo_filtro = None
        if len(args) > 1:
            g = self.find_grupo(args[1])
            if g:
                grupo_filtro = g
            else:
                print(f"Grupo no válido: {args[1]}")
                return
        use_rg = shutil.which("rg") is not None
        grupos = [grupo_filtro] if grupo_filtro else self.storage.get_grupos()
        files_to_search = []
        for grupo in grupos:
            gp_path = self.storage.grupo_path(grupo)
            if not gp_path.is_dir():
                continue
            for ev in self.storage.get_entradas(grupo):
                ev_path = self.storage.get_entrada_path(grupo, ev)
                if not ev_path or not ev_path.is_file():
                    continue
                if ev_path.suffix.lower() == ".gpg" and not self.config.search_encrypted:
                    continue
                if ev_path.suffix.lower() in EXT_TEXTO | {".gpg"}:
                    files_to_search.append((grupo, ev, ev_path))
        if not files_to_search:
            print("No hay archivos de texto para buscar.")
            return
        found = False
        for grupo, ev, path in files_to_search:
            if path.suffix.lower() == ".gpg":
                cmd_decrypt = ["gpg", "--decrypt", "--batch", "--quiet", str(path)]
                cmd_grep = (["rg", "--color=always", "-n", pattern]
                            if use_rg else ["grep", "-n", "-H", "--color=always", pattern])
                proc_d = subprocess.Popen(cmd_decrypt, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    result = subprocess.run(cmd_grep, stdin=proc_d.stdout, capture_output=True, text=True)
                    proc_d.stdout.close()
                    proc_d.wait()
                except Exception as e:
                    proc_d.kill()
                    print(f"Error buscando en {grupo}/{ev}: {e}")
                    continue
            else:
                cmd = (["rg", "--color=always", "-n", pattern, str(path)]
                       if use_rg else ["grep", "-n", "-H", "--color=always", pattern, str(path)])
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True)
                except Exception as e:
                    print(f"Error buscando en {grupo}/{ev}: {e}")
                    continue
            if result.returncode == 0 and result.stdout.strip():
                found = True
                lock = "🔒 " if path.suffix.lower() == ".gpg" else ""
                print(f"\n{lock}{self.ui.render_ruta(grupo, ev)}")
                for line in result.stdout.splitlines():
                    parts = line.split(':', 2)
                    if len(parts) >= 3:
                        line = f"{parts[1]}:{parts[2]}"
                    print(f"  {line}")
        if not found:
            print(f"No se encontraron coincidencias para '{pattern}'.")

    def mostrar_ayuda(self) -> None:
        print("BYTE — Notas en Markdown y archivos binarios\n")
        print("  byte              árbol")
        print("  byte -t           árbol con fechas")
        print("  byte --columnas   árbol en columnas")
        print("  byte -h           esta ayuda")
        print()
        print("  Abrir / añadir")
        print("  byte entrada              abre en editor")
        print("  byte entrada texto...     añade línea timestampeada")
        print("  byte Grupo/entrada        abre entrada explícita")
        print()
        print("  Comandos  --comando · letra")
        print("  --link    l  [sin argumentos]    lista todos los enlaces registrados")
        print("  --link    l  archivo [nombre]    enlaza archivo externo (ruta absoluta siempre)")
        print("  --unlink  u  [entrada]            quita enlace")
        print("  --del     d  [ruta]              envía al .trash/")
        print("  --mv      m  [origen] [destino]  mueve o fusiona")
        print("  --info    i  [entrada] [texto]    nota corta (y enlaces)")
        print("  --gpg     g  entrada              cifra con GPG")
        print("  --nogpg   q  entrada              descifra")
        print("  --check   c                       verifica configuración y enlaces")
        print("  --config  x                       configuración (incluye diff_tool)")
        print("  --search  s  texto [grupo]       busca con rg/grep")
        print("  --version v  entrada              guarda versión")
        print("  --restore r  entrada [n|timestamp] restaura versión")
        print()
        print("  Indicadores")
        print("  g gpg  b binario  i info  c → copia  r → remoto  x enlace roto")

# ============================================================================
# MAIN
# ============================================================================
async def async_main() -> None:
    config = Config()
    app = ByteApp(config)
    app.storage.asegurar_base()
    args = sys.argv[1:]
    if not args:
        app.ui.print_arbol(column_mode=config.columnas_default)
        return
    cmd = args[0]
    rest = args[1:]
    if cmd == "--columnas":
        app.ui.print_arbol(show_dates="-t" in rest, column_mode=True)
        return
    if cmd in ("-t", "--total"):
        app.ui.print_arbol(show_dates=True)
        return
    if cmd in ("-h", "--help", "help", "h"):
        app.mostrar_ayuda()
        return
    cmd_clean = cmd[2:] if cmd.startswith("--") else cmd
    async_cmds = {
        "link": app.cmd_link,   "l": app.cmd_link,
        "check": app.cmd_check, "c": app.cmd_check,
        "restore": app.cmd_restore, "r": app.cmd_restore,
    }
    sync_cmds = {
        "del":     app.cmd_del,     "d": app.cmd_del,
        "mv":      app.cmd_mv,      "m": app.cmd_mv,
        "info":    app.cmd_info,    "i": app.cmd_info,
        "gpg":     app.cmd_gpg,     "g": app.cmd_gpg,
        "nogpg":   app.cmd_nogpg,   "q": app.cmd_nogpg,
        "unlink":  app.cmd_unlink,  "u": app.cmd_unlink,
        "config":  app.cmd_config,  "x": app.cmd_config,
        "search":  app.cmd_search,  "s": app.cmd_search,
        "version": app.cmd_version, "v": app.cmd_version,
    }
    if cmd_clean in async_cmds:
        await async_cmds[cmd_clean](rest)
    elif cmd_clean in sync_cmds:
        sync_cmds[cmd_clean](rest)
    else:
        app.cmd_open([cmd] + rest)

def main() -> None:
    asyncio.run(async_main())

if __name__ == "__main__":
    main()
