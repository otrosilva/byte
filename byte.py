#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
byte.py — gestor de notas Markdown y archivos binarios (Linux/macOS)
v1.0
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
from typing import Dict, List, Optional, Tuple, Any
from functools import lru_cache

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None
        
__version__ = "1.0"

# ============================================================================
# COLORES ANSI
# ============================================================================
# Colores ANSI estándar (no de paleta de 256 fijos) para que se adapten
# correctamente tanto a temas oscuros como claros de terminal.
_ANSI = {
    "rst":    "\033[0m",
    "bold":   "\033[1m",
    "event":  "\033[0m",
    "header": "\033[1m",
    "plus":   "\033[1;32m",
    "minus":  "\033[31m",
    "tree":   "\033[2m",
    "group":  "\033[36m",
    "count":  "\033[1m",
    "date":   "\033[2m",
    "link":   "\033[2;36m",
    "warn":   "\033[33m",
}

## función C - color ANSI por clave
def C(key: str) -> str:
    return _ANSI.get(key, "")
### fin de función C

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

## función strip_ansi - quita códigos ANSI de un string
@lru_cache(maxsize=1024)
def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)
### fin de función strip_ansi

## función pad_ansi - rellena string con espacios ignorando ANSI
def pad_ansi(s: str, width: int) -> str:
    return s + " " * max(0, width - len(strip_ansi(s)))
### fin de función pad_ansi

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

class Config:
    DEFAULT_BASE = Path.home() / "Documentos/Filen/Obsidian/bytes"
    DEFAULT_EDITOR = os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")
    DEFAULT_VERSIONS_PATH = Path.home() / ".config" / "byte" / "versions"

    ## función __init__ - inicializa Config y carga valores
    def __init__(self):
        self.base: Path = self.DEFAULT_BASE
        self.editor: str = self.DEFAULT_EDITOR
        self.gpg_key: str = ""
        self.gpg_keys_secondary: List[str] = []
        self.used_config_path: Optional[Path] = None
        self.columnas_default: bool = False
        self.search_encrypted: bool = False
        self.versions_path: Path = self.DEFAULT_VERSIONS_PATH
        self.diff_tool: str = "auto"   # auto, delta, bat, diff
        self._load()
    ### fin de función __init__

    ## función _load_toml_file - carga un archivo TOML a dict
    def _load_toml_file(self, path: Path) -> Dict[str, Any]:
        if not path.is_file() or tomllib is None:
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)
    ### fin de función _load_toml_file

    ## función _create_default_config - crea archivo de configuración por defecto
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
    ### fin de función _create_default_config

    ## función _load - carga configuración desde disco (sistema o vault)
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
                print(f"{C('date')}Configuración por defecto creada en {vault_path}{C('rst')}", file=sys.stderr)

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
    ### fin de función _load

    ## función save - guarda configuración en disco
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
    ### fin de función save

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
# UTILIDADES GENERALES (síncronas — solo para uso en contexto no-async)
# ============================================================================

## función es_remoto - detecta si una ruta es remota (ssh)
def es_remoto(path: str) -> bool:
    if path.startswith('ssh://'):
        return True
    if ':' in path and not path.startswith('/') and not path.startswith('./') and not path.startswith('../'):
        return True
    return False
### fin de función es_remoto

## función remote_parse - separa user@host y path de una ruta remota
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
### fin de función remote_parse

## función remote_abbrev - abrevia una ruta remota para mostrar
def remote_abbrev(remote: str) -> str:
    if ':' not in remote:
        return remote
    user_host, path = remote_parse(remote)
    parts = Path(path).parts
    short_path = f"…/{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else path
    return f"{user_host}:{short_path}"
### fin de función remote_abbrev

## función calcular_md5 - calcula el hash MD5 de un archivo
def calcular_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
### fin de función calcular_md5

## función detectar_tipo_archivo - detecta si un archivo es texto o binario
def detectar_tipo_archivo(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read(1024)
        return "text"
    except (UnicodeDecodeError, OSError):
        return "binary"
### fin de función detectar_tipo_archivo

## función _resaltar - resalta la abreviatura dentro de un texto
def _resaltar(texto: str, abrev: Optional[str], long: int, color_norm: str, color_res: str) -> str:
    if not abrev:
        return color_norm + texto + C("rst")
    idx = texto.find(abrev)
    if idx != -1:
        pre, lbl, post = texto[:idx], texto[idx:idx+long], texto[idx+long:]
        return f"{color_norm}{pre}{C('rst')}{color_res}{lbl}{C('rst')}{color_norm}{post}{C('rst')}"
    return f"{color_norm}{texto} {color_res}{abrev}{C('rst')}"
### fin de función _resaltar

# ============================================================================
# UTILIDADES ASYNC (SSH, diff, subprocesos)
# ============================================================================

## función async_run - ejecuta comando async y captura stdout/stderr
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
### fin de función async_run

## función remote_exists_async - verifica si un archivo remoto existe
async def remote_exists_async(remote: str) -> bool:
    user_host, path = remote_parse(remote)
    proc = await async_run("ssh", user_host, "test", "-f", path)
    return proc.returncode == 0
### fin de función remote_exists_async

## función remote_read_async - lee contenido de un archivo remoto
async def remote_read_async(remote: str) -> bytes:
    user_host, path = remote_parse(remote)
    proc = await async_run("ssh", user_host, "cat", path)
    if proc.returncode != 0:
        raise RuntimeError(f"Error leyendo {remote}: {proc._stderr_data.decode()}")
    return proc._stdout_data
### fin de función remote_read_async

## función remote_write_async - escribe contenido en un archivo remoto
async def remote_write_async(remote: str, data: bytes) -> None:
    user_host, path = remote_parse(remote)
    proc = await asyncio.create_subprocess_exec(
        "ssh", user_host, f"cat > {shlex.quote(path)}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(input=data)
    if proc.returncode != 0:
        raise RuntimeError(f"Error escribiendo en {remote}: {stderr.decode()}")
### fin de función remote_write_async

## función remote_check_async - consulta mtime y contenido remoto en una sola conexión SSH
async def remote_check_async(remote: str) -> Optional[Tuple[float, bytes]]:
    """Una sola conexión SSH: devuelve (mtime, contenido) o None si no existe."""
    user_host, path = remote_parse(remote)
    remote_cmd = (
        f'f={shlex.quote(path)}; '
        f'if [ -f "$f" ]; then stat -c %Y "$f" && cat "$f"; else exit 2; fi'
    )
    proc = await async_run("ssh", user_host, remote_cmd)
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
### fin de función remote_check_async

# ============================================================================
# REGISTRO
# ============================================================================

class Registry:
    ## función __init__ - inicializa Registry y carga datos
    def __init__(self, base: Path):
        self.path = base / ".byte" / "byte.json"
        self.links_path = Path.home() / ".config" / "byte" / "links.json"
        self._data: Optional[Dict] = None
        self._links: Optional[Dict] = None
        self._mtime: float = 0.0
        self._mtime_links: float = 0.0
        self._load()
    ### fin de función __init__

    ## función _load_links - carga links.json desde disco
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
    ### fin de función _load_links

    ## función _save_links - guarda links.json en disco
    def _save_links(self) -> None:
        if self._links is None:
            return
        self.links_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.links_path, "w", encoding="utf-8") as f:
            json.dump(self._links, f, indent=2, ensure_ascii=False)
        self._mtime_links = self.links_path.stat().st_mtime
    ### fin de función _save_links

    ## función _load - carga byte.json desde disco
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
    ### fin de función _load

    ## función _save - guarda byte.json en disco
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        self._mtime = self.path.stat().st_mtime
    ### fin de función _save

    ## función _key - genera clave grupo/stem
    def _key(self, grupo: str, stem: str) -> str:
        return f"{grupo}/{stem}"
    ### fin de función _key

    # --- links ---
    ## función add_origin - añade un origen enlazado a una entrada
    def add_origin(self, grupo: str, stem: str, ruta: str) -> None:
        self._load_links()
        key = self._key(grupo, stem)
        if key not in self._links:
            self._links[key] = []
        if ruta not in self._links[key]:
            self._links[key].append(ruta)
            self._save_links()
    ### fin de función add_origin

    ## función remove_origin - elimina un origen enlazado de una entrada
    def remove_origin(self, grupo: str, stem: str, ruta: str) -> None:
        self._load_links()
        key = self._key(grupo, stem)
        if key in self._links:
            self._links[key] = [p for p in self._links[key] if p != ruta]
            if not self._links[key]:
                del self._links[key]
            self._save_links()
    ### fin de función remove_origin

    ## función remove_all_origins - elimina todos los orígenes de una entrada
    def remove_all_origins(self, grupo: str, stem: str) -> None:
        self._load_links()
        self._links.pop(self._key(grupo, stem), None)
        self._save_links()
    ### fin de función remove_all_origins

    ## función get_origins - obtiene los orígenes enlazados de una entrada
    def get_origins(self, grupo: str, stem: str) -> List[str]:
        self._load_links()
        return self._links.get(self._key(grupo, stem), [])
    ### fin de función get_origins

    ## función rename_links - renombra la clave de enlaces al mover/renombrar entrada
    def rename_links(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load_links()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._links:
            self._links[key_dst] = self._links.pop(key_src)
            self._save_links()
    ### fin de función rename_links

    # --- info ---
    ## función get_info - obtiene la nota/info de una entrada
    def get_info(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        return entry.get("info") if isinstance(entry, dict) else None
    ### fin de función get_info

    ## función get_type - obtiene el tipo (text/binary) de una entrada
    def get_type(self, grupo: str, stem: str) -> str:
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        if isinstance(entry, dict) and "type" in entry:
            return entry["type"]
        return "text"
    ### fin de función get_type

    ## función set_info - guarda la nota/info de una entrada
    def set_info(self, grupo: str, stem: str, texto: str) -> None:
        self._load()
        key = self._key(grupo, stem)
        if not isinstance(self._data["info"].get(key), dict):
            self._data["info"][key] = {}
        self._data["info"][key]["info"] = texto.strip()
        self._save()
    ### fin de función set_info

    ## función set_type - guarda el tipo de una entrada
    def set_type(self, grupo: str, stem: str, tipo: str) -> None:
        self._load()
        key = self._key(grupo, stem)
        if not isinstance(self._data["info"].get(key), dict):
            self._data["info"][key] = {}
        self._data["info"][key]["type"] = tipo
        self._save()
    ### fin de función set_type

    ## función has_info - indica si una entrada tiene nota
    def has_info(self, grupo: str, stem: str) -> bool:
        return self.get_info(grupo, stem) is not None
    ### fin de función has_info

    ## función remove_info - elimina la nota de una entrada
    def remove_info(self, grupo: str, stem: str) -> None:
        self._load()
        self._data["info"].pop(self._key(grupo, stem), None)
        self._save()
    ### fin de función remove_info

    ## función rename_info - renombra la clave de info al mover/renombrar entrada
    def rename_info(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["info"]:
            self._data["info"][key_dst] = self._data["info"].pop(key_src)
            self._save()
    ### fin de función rename_info

    # --- gpg ---
    ## función mark_gpg - marca una entrada como cifrada con una clave GPG
    def mark_gpg(self, grupo: str, stem: str, key_id: str) -> None:
        self._load()
        self._data["gpg"][self._key(grupo, stem)] = key_id
        self._save()
    ### fin de función mark_gpg

    ## función unmark_gpg - desmarca el cifrado GPG de una entrada
    def unmark_gpg(self, grupo: str, stem: str) -> None:
        self._load()
        self._data["gpg"].pop(self._key(grupo, stem), None)
        self._save()
    ### fin de función unmark_gpg

    ## función is_protected - indica si una entrada está cifrada
    def is_protected(self, grupo: str, stem: str) -> bool:
        self._load()
        return self._key(grupo, stem) in self._data["gpg"]
    ### fin de función is_protected

    ## función key_id - obtiene el id de clave GPG de una entrada
    def key_id(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        return self._data["gpg"].get(self._key(grupo, stem))
    ### fin de función key_id

    ## función rename_gpg - renombra la clave gpg al mover/renombrar entrada
    def rename_gpg(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["gpg"]:
            self._data["gpg"][key_dst] = self._data["gpg"].pop(key_src)
            self._save()
    ### fin de función rename_gpg

    # --- abbr_cache ---
    ## función get_abbr_cache - obtiene la caché de abreviaturas guardada
    def get_abbr_cache(self) -> Dict:
        return self._data.get("abbr_cache", {})
    ### fin de función get_abbr_cache

    ## función set_abbr_cache - guarda la caché de abreviaturas
    def set_abbr_cache(self, abbr_cache: Dict) -> None:
        self._data["abbr_cache"] = abbr_cache
        self._save()
    ### fin de función set_abbr_cache

# ============================================================================
# ALMACENAMIENTO
# ============================================================================

class ByteStorage:
    ## función __init__ - inicializa ByteStorage
    def __init__(self, base: Path, config: Config):
        self.base = base
        self.byte_dir = base / ".byte"
        self.registry = Registry(base)
        self.versions_path = config.versions_path
        self._dir_cache: Dict[str, Tuple[float, List[Path]]] = {}
    ### fin de función __init__

    ## función asegurar_base - crea directorios base si no existen
    def asegurar_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self.byte_dir.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".config" / "byte").mkdir(parents=True, exist_ok=True)
        self.versions_path.mkdir(parents=True, exist_ok=True)
    ### fin de función asegurar_base

    ## función _listar_grupo - lista archivos de un grupo (con caché por mtime)
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
    ### fin de función _listar_grupo

    ## función _invalidar_cache_grupo - invalida la caché de listado de un grupo
    def _invalidar_cache_grupo(self, grupo: str) -> None:
        self._dir_cache.pop(grupo, None)
    ### fin de función _invalidar_cache_grupo

    ## función normalize - normaliza texto (minúsculas, sin acentos)
    def normalize(self, txt: str) -> str:
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", txt) if unicodedata.category(c) != "Mn")
    ### fin de función normalize

    ## función titulo - capitaliza el título de un grupo/entrada
    def titulo(self, txt: str) -> str:
        return txt.strip().capitalize()
    ### fin de función titulo

    ## función get_grupos - lista los grupos existentes
    def get_grupos(self) -> List[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir() and not d.name.startswith("."))
    ### fin de función get_grupos

    ## función get_entradas - lista los stems de entradas de un grupo (sin duplicados)
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
    ### fin de función get_entradas

    ## función get_entrada_path - busca la ruta de archivo de una entrada
    def get_entrada_path(self, grupo: str, stem: str) -> Optional[Path]:
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            if ext == ".gpg":
                if Path(f.stem).stem == stem:
                    return f
            elif f.stem == stem:
                return f
        return None
    ### fin de función get_entrada_path

    ## función grupo_path - ruta de directorio de un grupo
    def grupo_path(self, grupo: str) -> Path:
        return self.base / self.titulo(grupo)
    ### fin de función grupo_path

    ## función entrada_path - ruta de archivo de una entrada
    def entrada_path(self, grupo: str, stem: str, ext: str = ".md") -> Path:
        return self.base / self.titulo(grupo) / f"{stem}{ext}"
    ### fin de función entrada_path

    ## función trash - mueve un archivo/directorio al trash
    def trash(self, path: Path) -> None:
        if not path.exists():
            return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        shutil.move(path, dest)
    ### fin de función trash

    ## función mtime - obtiene el mtime de un archivo como datetime
    def mtime(self, path: Optional[Path]) -> Optional[datetime]:
        if not path or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime)
    ### fin de función mtime

    ## función limpiar_vacios - elimina directorios de grupo vacíos
    def limpiar_vacios(self) -> None:
        for g in self.get_grupos():
            gp = self.grupo_path(g)
            if gp.is_dir() and not any(f for f in gp.iterdir() if not f.name.startswith(".")):
                gp.rmdir()
    ### fin de función limpiar_vacios

    ## función leer_entrada - lee el contenido de una entrada (descifra si aplica)
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
    ### fin de función leer_entrada

    ## función escribir_entrada - escribe contenido en una entrada (cifra si aplica)
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
    ### fin de función escribir_entrada

    ## función _gpg_encrypt - cifra un archivo con GPG
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
    ### fin de función _gpg_encrypt

    ## función _gpg_decrypt_to_tmp - descifra un archivo GPG a uno temporal
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
    ### fin de función _gpg_decrypt_to_tmp

    # --- versionado ---
    ## función guardar_version - guarda una versión histórica de una entrada
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
    ### fin de función guardar_version

    ## función listar_versiones - lista las versiones guardadas de una entrada
    def listar_versiones(self, grupo: str, stem: str) -> List[Path]:
        version_dir = self.versions_path / grupo / stem
        if not version_dir.is_dir():
            return []
        pattern = re.compile(r'^\d{8}_\d{6}\.[^.]+$')
        files = [f for f in version_dir.iterdir() if f.is_file() and pattern.match(f.name)]
        files.sort(key=lambda p: p.name, reverse=True)
        return files
    ### fin de función listar_versiones

    ## función restaurar_version - restaura una versión histórica de una entrada
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
    ### fin de función restaurar_version

# ============================================================================
# INTERFAZ
# ============================================================================

class ByteInterface:
    ## función __init__ - inicializa ByteInterface
    def __init__(self, storage: ByteStorage, columnas_default: bool = False):
        self.storage = storage
        self.registry = storage.registry
        self.columnas_default = columnas_default
        # caché persistente de abreviaturas: {grupo: {"mtime": float, "abbr": dict}}
        self._persistent_cache: Dict = self.registry.get_abbr_cache()
    ### fin de función __init__

    ## función _get_abreviaturas - obtiene abreviaturas de un grupo (con caché persistente)
    def _get_abreviaturas(self, grupo: str, long: int = 2) -> Dict[str, str]:
        # Para long != 2 se calcula directamente sin persistir
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
    ### fin de función _get_abreviaturas

    ## función update_all_abbreviations - recalcula y guarda abreviaturas de todos los grupos
    def update_all_abbreviations(self) -> None:
        self._persistent_cache = {}
        for grupo in self.storage.get_grupos():
            evs = self.storage.get_entradas(grupo)
            abbr = self.calc_abreviaturas(evs, 2)
            gp = self.storage.base / grupo
            if gp.is_dir():
                self._persistent_cache[grupo] = {"mtime": gp.stat().st_mtime, "abbr": abbr}
        self.registry.set_abbr_cache(self._persistent_cache)
    ### fin de función update_all_abbreviations

    ## función invalidar_cache_abreviaturas - invalida la caché de abreviaturas (uno o todos los grupos)
    def invalidar_cache_abreviaturas(self, grupo: Optional[str] = None) -> None:
        if grupo is None:
            self._persistent_cache.clear()
        else:
            self._persistent_cache.pop(grupo, None)
        self.registry.set_abbr_cache(self._persistent_cache)
    ### fin de función invalidar_cache_abreviaturas

    ## función leer - lee una línea de input del usuario
    def leer(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C('tree')}(Interrumpido){C('rst')}")
            sys.exit(0)
    ### fin de función leer

    ## función calc_abreviaturas - calcula abreviaturas únicas para una lista de nombres
    def calc_abreviaturas(self, lista: List[str], long: int) -> Dict[str, str]:
        max_long = max(long, 5)
        resultado: Dict[str, str] = {}
        for item in sorted(lista, key=len):
            encontrado = None
            for l in range(long, max_long + 1):
                if len(item) < l:
                    continue
                for i in range(len(item) - l + 1):
                    sub = item[i:i+l]
                    if sub not in resultado.values():
                        encontrado = sub
                        break
                if encontrado:
                    break
            resultado[item] = encontrado or item[:max_long]
        return {item: resultado[item] for item in lista}
    ### fin de función calc_abreviaturas

    ## función render_ruta - renderiza la ruta Grupo/entrada con colores y abreviaturas
    def render_ruta(self, grupo: str, stem: str) -> str:
        g_abbr = self.calc_abreviaturas(self.storage.get_grupos(), 3)
        g_render = _resaltar(grupo, g_abbr.get(grupo), 3, C("group"), C("bold"))
        evs = self.storage.get_entradas(grupo)
        if stem not in evs:
            evs = evs + [stem]
        e_abbr = self._get_abreviaturas(grupo)
        e_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))
        return f"{g_render}{C('tree')}/{C('rst')}{e_render}"
    ### fin de función render_ruta

    ## función _fmt_origin - formatea un origen para mostrar (abreviado)
    def _fmt_origin(self, path_str: str) -> str:
        if es_remoto(path_str):
            return remote_abbrev(path_str)
        p = Path(path_str)
        parts = p.parts
        return f"…/{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else path_str
    ### fin de función _fmt_origin

    ## función _get_badges_compactos - genera badges compactos de estado de una entrada
    def _get_badges_compactos(self, grupo: str, stem: str) -> str:
        r = self.storage.registry
        b1 = f"{C('warn')}g{C('rst')}" if r.is_protected(grupo, stem) else " "
        b2 = f"{C('warn')}i{C('rst')}" if r.has_info(grupo, stem) else " "
        origins = r.get_origins(grupo, stem)
        if origins:
            first = origins[0]
            if es_remoto(first):
                b3 = f"{C('link')}r{C('rst')}"
            elif Path(first).is_file():
                b3 = f"{C('link')}c{C('rst')}"
            else:
                b3 = f"{C('minus')}x{C('rst')}"
        else:
            b3 = " "
        b4 = f"{C('date')}b{C('rst')}" if (not r.is_protected(grupo, stem) and r.get_type(grupo, stem) == "binary") else " "
        return b1 + b2 + b3 + b4
    ### fin de función _get_badges_compactos

    ## función _render_entrada_linea - renderiza la línea de una entrada (compacta o completa)
    def _render_entrada_linea(self, grupo: str, stem: str, ev_path: Optional[Path],
                              e_abbr: Dict[str, str], compact: bool = False) -> str:
        event_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))
        ext_str = ""
        if ev_path and ev_path.suffix.lower() != ".gpg":
            ext_str = f"{C('date')}{ev_path.suffix.lower()}{C('rst')}"
        display_name = event_render + ext_str

        r = self.storage.registry
        if compact:
            return f"{self._get_badges_compactos(grupo, stem)} {display_name}"

        badges = ""
        if r.is_protected(grupo, stem):
            badges += f" {C('warn')}g{C('rst')}"
        elif r.get_type(grupo, stem) == "binary":
            badges += f" {C('date')}b{C('rst')}"
        if r.has_info(grupo, stem):
            badges += f" {C('warn')}i{C('rst')}"

        origins = r.get_origins(grupo, stem)
        origins_str = ""
        if origins:
            parts = []
            for path_str in origins:
                if es_remoto(path_str):
                    parts.append(f"{C('date')}r → {remote_abbrev(path_str)}{C('rst')}")
                else:
                    disponible = Path(path_str).is_file()
                    origen_fmt = self._fmt_origin(path_str)
                    if not disponible:
                        parts.append(f"{C('minus')}x{C('rst')} {C('date')}{origen_fmt}{C('rst')}")
                    else:
                        parts.append(f"{C('date')}c → {origen_fmt}{C('rst')}")
            origins_str = f" {C('date')}·{C('rst')} " + f"{C('date')}, {C('rst')}".join(parts)
        return f"{display_name}{badges}{origins_str}"
    ### fin de función _render_entrada_linea

    ## función print_arbol_columnas - imprime el árbol de grupos/entradas en columnas
    def print_arbol_columnas(self, show_dates: bool = False) -> None:
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        term_width = shutil.get_terminal_size().columns
        g_abbr_tmp = self.calc_abreviaturas(grupos, 3)

        ## función ancho_grupo - calcula el ancho máximo de un grupo en columnas
        def ancho_grupo(grupo: str, evs: List[str]) -> int:
            e_abbr = self._get_abreviaturas(grupo)
            header = f"{_resaltar(grupo, g_abbr_tmp.get(grupo), 3, C('group'), C('bold'))} {C('date')}({len(evs)}){C('rst')}"
            max_ancho = len(strip_ansi(header))
            for stem in evs:
                ev_path = self.storage.get_entrada_path(grupo, stem)
                linea = self._render_entrada_linea(grupo, stem, ev_path, e_abbr, compact=True)
                max_ancho = max(max_ancho, len(strip_ansi(linea)))
            return max_ancho
        ### fin de función ancho_grupo

        grupos_data = [(g, self.storage.get_entradas(g)) for g in grupos]
        anchos = [ancho_grupo(g, evs) + 2 for g, evs in grupos_data]
        n_cols = len(grupos)
        while n_cols > 1 and sum(anchos[:n_cols]) > term_width:
            n_cols -= 1

        grupos_en_filas = [grupos_data[i:i+n_cols] for i in range(0, len(grupos_data), n_cols)]
        anchos_en_filas = [anchos[i:i+n_cols] for i in range(0, len(anchos), n_cols)]
        sep = "  "

        for fila_grupos, fila_anchos in zip(grupos_en_filas, anchos_en_filas):
            columnas: List[List[str]] = []
            for (grupo, evs), ancho in zip(fila_grupos, fila_anchos):
                e_abbr = self._get_abreviaturas(grupo)
                header = f"{_resaltar(grupo, g_abbr_tmp.get(grupo), 3, C('group'), C('bold'))} {C('date')}({len(evs)}){C('rst')}"
                lineas = [header]
                for stem in evs:
                    ev_path = self.storage.get_entrada_path(grupo, stem)
                    lineas.append(self._render_entrada_linea(grupo, stem, ev_path, e_abbr, compact=True))
                columnas.append(lineas)
            max_filas = max(len(col) for col in columnas)
            for fi in range(max_filas):
                partes = []
                for col, ancho in zip(columnas, fila_anchos):
                    celda = col[fi] if fi < len(col) else ""
                    partes.append(pad_ansi(celda, ancho - 2))
    ### fin de función ancho_grupo
                print(sep.join(partes).rstrip())
            print()

    ## función print_arbol - imprime el árbol de grupos/entradas
    def print_arbol(self, grupos_filter: Optional[List[str]] = None,
                    show_dates: bool = False, column_mode: bool = False) -> None:
        if column_mode:
            self.print_arbol_columnas(show_dates)
            return
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        d, r = C("date"), C("rst")
        g_abbr_tmp = self.calc_abreviaturas(grupos, 3)

        for gi, grupo in enumerate(grupos):
            evs = self.storage.get_entradas(grupo)
            if gi > 0:
                print()
            grupo_render = _resaltar(grupo, g_abbr_tmp.get(grupo), 3, C("group"), C("bold"))
            print(f"{C('tree')}{grupo_render} {d}({len(evs)}){r}")
            if not evs:
                continue
            e_abbr = self._get_abreviaturas(grupo)
            for stem in evs:
                ev_path = self.storage.get_entrada_path(grupo, stem)
                line = self._render_entrada_linea(grupo, stem, ev_path, e_abbr, compact=False)
                if show_dates:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        line += f"  {d}{mt.strftime('%Y-%m-%d %H:%M')}{r}"
                print(f"  {line}")
        print()
    ### fin de función print_arbol

    ## función pedir_grupo - solicita al usuario un grupo (con autocompletado)
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
            g_abbr = self.calc_abreviaturas(grupos, 3)
            for g, ab in g_abbr.items():
                if ab.lower() == res.lower():
                    return g
            res_norm = self.storage.normalize(res)
            for g in grupos:
                if self.storage.normalize(g) == res_norm:
                    return g
            return self.storage.titulo(res)
    ### fin de función pedir_grupo

    ## función pedir_entrada - solicita al usuario una entrada (con autocompletado)
    def pedir_entrada(self, grupo: str, label: str = "Entrada") -> str:
        evs = self.storage.get_entradas(grupo)
        if evs:
            e_abbr = self._get_abreviaturas(grupo)
            print(f"\n  Entradas en {grupo}:")
            for e in evs:
                render = _resaltar(e, e_abbr.get(e), 2, C("event"), C("bold"))
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
    ### fin de función pedir_entrada

# ============================================================================
# APLICACIÓN
# ============================================================================

class ByteApp:
    ## función __init__ - inicializa ByteApp
    def __init__(self, config: Config):
        self.config = config
        self.storage = ByteStorage(config.base, config)
        self.ui = ByteInterface(self.storage, config.columnas_default)
    ### fin de función __init__

    # --- resolución de argumentos ---
    ## función find_grupo - busca un grupo por nombre o abreviatura
    def find_grupo(self, token: str) -> Optional[str]:
        grupos = self.storage.get_grupos()
        if token in grupos:
            return token
        g_abbr = self.ui.calc_abreviaturas(grupos, 3)
        for g, ab in g_abbr.items():
            if ab.lower() == token.lower():
                return g
        token_norm = self.storage.normalize(token)
        for g in grupos:
            if self.storage.normalize(g) == token_norm:
                return g
        return None
    ### fin de función find_grupo

    ## función parse_arg - parsea un argumento tipo Grupo/entrada
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
    ### fin de función parse_arg

    ## función resolver_arg - resuelve un argumento a (grupo, stem)
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
    ### fin de función resolver_arg

    ## función _validar_stem - valida que un stem sea válido (largo, no reservado)
    def _validar_stem(self, stem: str) -> bool:
        if len(stem) < 4:
            print(f"{C('warn')}El nombre debe tener al menos 4 caracteres.{C('rst')}")
            return False
        alias = {"l", "u", "d", "m", "i", "g", "q", "c", "x", "s", "v", "r"}
        if stem in alias:
            print(f"{C('warn')}'{stem}' es un alias de comando reservado.{C('rst')}")
            return False
        return True
    ### fin de función _validar_stem

    ## función _fmt_version_fecha - formatea la fecha de una versión
    def _fmt_version_fecha(self, vpath: Path) -> str:
        try:
            return datetime.strptime(vpath.stem, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return vpath.stem
    ### fin de función _fmt_version_fecha

    # --- Métodos de diff ---
    ## función _diff_tool_async - detecta la herramienta de diff disponible
    async def _diff_tool_async(self) -> Optional[str]:
        if self.config.diff_tool != "auto":
            if shutil.which(self.config.diff_tool):
                return self.config.diff_tool
        for tool in ("delta", "bat"):
            if shutil.which(tool):
                return tool
        return None
    ### fin de función _diff_tool_async

    ## función mostrar_diff_async - muestra el diff entre dos archivos locales
    async def mostrar_diff_async(self, a: Path, b: Path) -> None:
        proc = await async_run("diff", "-u", str(a), str(b))
        if not proc._stdout_data.strip():
            print("  (sin diferencias)")
            return
        tool = await self._diff_tool_async()
        diff_text = proc._stdout_data
        if tool == "delta":
            p = await asyncio.create_subprocess_exec(
                "delta", "--paging=never",
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
    ### fin de función mostrar_diff_async

    ## función mostrar_diff_remoto_async - muestra el diff entre un archivo local y contenido remoto
    async def mostrar_diff_remoto_async(self, local: Path, contenido_remoto: bytes) -> None:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.tmp', delete=False) as tf:
            tf.write(contenido_remoto)
            tmp = Path(tf.name)
        try:
            await self.mostrar_diff_async(local, tmp)
        finally:
            tmp.unlink()
    ### fin de función mostrar_diff_remoto_async

    # --- comandos ---
    ## función cmd_open - abre, crea o añade texto a una entrada
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
                print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea.strip()}")
                return

            if ev_path.suffix.lower() == ".gpg":
                if self.storage.registry.get_type(grupo, stem) == "binary":
                    print(f"{C('warn')}No se puede añadir texto a un archivo cifrado binario.{C('rst')}")
                    return
                try:
                    tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                except RuntimeError as e:
                    print(f"GPG error: {e}")
                    return
                try:
                    contenido_actual = tmp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    print(f"{C('warn')}Contenido descifrado no es texto UTF-8.{C('rst')}")
                    tmp.unlink()
                    return
                tmp.write_text(contenido_actual + linea, encoding="utf-8")
                key_id = self.storage.registry.key_id(grupo, stem)
                if not key_id:
                    print(f"{C('warn')}No hay clave GPG registrada.{C('rst')}")
                    tmp.unlink()
                    return
                try:
                    self.storage._gpg_encrypt(tmp, key_id, ev_path)
                except Exception as e:
                    print(f"Error al cifrar: {e}")
                finally:
                    tmp.unlink()
                print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea.strip()}")
                return

            if detectar_tipo_archivo(ev_path) == "binary":
                print(f"{C('warn')}No se puede añadir texto a un archivo binario.{C('rst')}")
                return
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(linea)
            print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea.strip()}")
            return

        # abrir en editor
        if ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo = self.storage.registry.get_type(grupo, stem)
            tipo_real = detectar_tipo_archivo(ev_path)
            if tipo_real != "text":
                if tipo != tipo_real:
                    self.storage.registry.set_type(grupo, stem, tipo_real)
                ruta_fmt = self.ui.render_ruta(grupo, stem)
                print(f"\n{C('date')}{ruta_fmt} es un archivo binario.{C('rst')}")
                if self.ui.leer(f"Exportar (s/{C('date')}N{C('rst')}): ").lower() == "s":
                    destino = Path.cwd() / ev_path.name
                    shutil.copy2(ev_path, destino)
                    print(f"{C('plus')}✓ Exportado a {destino}{C('rst')}")
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
                print(f"\n{C('date')}{ruta_fmt} contiene datos binarios.{C('rst')}")
                if self.ui.leer(f"¿Descifrar y exportar? (s/{C('date')}N{C('rst')}): ").lower() == "s":
                    inner_ext = Path(ev_path.stem).suffix or ".bin"
                    destino = Path.cwd() / f"{stem}{inner_ext}"
                    shutil.copy2(tmp, destino)
                    print(f"{C('plus')}✓ Exportado a {destino}{C('rst')}")
                tmp.unlink()
                return
            mtime_antes = tmp.stat().st_mtime
            os.system(f'{self.config.editor} "{tmp}"')
            if tmp.stat().st_mtime != mtime_antes:
                key_id = self.storage.registry.key_id(grupo, stem)
                try:
                    self.storage.escribir_entrada(grupo, stem, tmp.read_bytes(), key_id=key_id, cifrar=True)
                    print(f"{C('count')}~ {ruta_fmt}{C('rst')} (cifrado)")
                except Exception as e:
                    print(f"Error al guardar: {e}")
            else:
                print(f"{C('date')}  (sin cambios){C('rst')}")
            tmp.unlink()
            return

        ev_path.parent.mkdir(parents=True, exist_ok=True)
        ev_path.touch()
        os.system(f'{self.config.editor} "{ev_path}"')
        if es_nuevo and ev_path.stat().st_size == 0:
            ev_path.unlink()
            print(f"{C('minus')}(Archivo vacío descartado){C('rst')}")
        else:
            accion = f"{C('plus')}+" if es_nuevo else f"{C('count')}~"
            print(f"{accion} {ruta_fmt}{C('rst')}")
            if es_nuevo:
                self.storage.registry.set_type(grupo, stem, "text")
    ### fin de función cmd_open

    ## función cmd_link - enlaza un archivo externo a una entrada (expande rutas a absolutas)
    async def cmd_link(self, args: List[str]) -> None:
        if not args:
            archivo = self.ui.leer("Archivo a enlazar: ")
            if not archivo:
                return
            grupo_hint = stem_override = None
        elif len(args) == 1:
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

        # Expandir y normalizar la ruta siempre a absoluta antes de guardar
        src_str = archivo
        if es_remoto(src_str):
            # Expandir ~ en rutas remotas: user@host:~/archivo → user@host:/home/user/archivo
            user_host, path = remote_parse(src_str)
            if path.startswith('~'):
                user = user_host.split('@')[0] if '@' in user_host else user_host
                home = '/root' if user == 'root' else f'/home/{user}'
                path = path.replace('~', home, 1)
                src_str = (f"ssh://{user_host}{path}" if src_str.startswith('ssh://')
                           else f"{user_host}:{path}")
            src_exists = await remote_exists_async(src_str)
            src_parent = None
        else:
            # Expandir ~ y resolver ruta local a absoluta
            src = Path(src_str).expanduser().resolve()
            src_exists = src.is_file()
            src_parent = src.parent
            src_str = str(src)

        if grupo_hint:
            grupo = grupo_hint
        else:
            self.ui.print_arbol(column_mode=self.ui.columnas_default)
            grupo = self.ui.pedir_grupo("Grupo para la entrada", mostrar_arbol=False)
            if not grupo:
                return

        if stem_override:
            evs = self.storage.get_entradas(grupo)
            e_abbr = self.ui._get_abreviaturas(grupo)
            encontrado = next((ev for ev, ab in e_abbr.items() if ab == stem_override.lower()), None)
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

        # --- casos ---
        if not src_exists and ev_exists:
            print(f"{C('date')}  El archivo externo no existe, se creará desde el vault.{C('rst')}")
            prompt = f"  Crear {src_str} desde {grupo}/{stem}? (s/{C('date')}N{C('rst')}): "
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
            print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(src_str)} (copia desde vault){C('rst')}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and not ev_exists:
            print(f"{C('date')}  La entrada {grupo}/{stem} no existe, se creará desde el archivo externo.{C('rst')}")
            prompt = f"  Crear {grupo}/{stem} desde {src_str}? (s/{C('date')}N{C('rst')}): "
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
            print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(src_str)}  ({metodo}){C('rst')}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and ev_exists:
            origins = self.storage.registry.get_origins(grupo, stem)
            if src_str in origins:
                print(f"{C('date')}  {self.ui.render_ruta(grupo, stem)} ya tiene este origen.{C('rst')}")
                return
            print(f"{C('warn')}  Conflicto: ambos archivos existen.{C('rst')}")
            print(f"    Vault: {ev_path}")
            print(f"    Externo: {src_str}")
            while True:
                op = self.ui.leer("  [v]ault→origen, [o]rigen→vault, [a]ñadir, [d]iff, [n]ada: ").lower()
                if op == 'd':
                    if es_remoto(src_str):
                        contenido_remoto = await remote_read_async(src_str)
                        await self.mostrar_diff_remoto_async(ev_path, contenido_remoto)
                    else:
                        await self.mostrar_diff_async(ev_path, Path(src_str))
                    continue
                if op == 'v':
                    if self.ui.leer(f"  ¿Sobrescribir {src_str}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                        continue
                    contenido = self.storage.leer_entrada(grupo, stem)
                    if contenido is not None:
                        if es_remoto(src_str):
                            await remote_write_async(src_str, contenido)
                        else:
                            Path(src_str).write_bytes(contenido)
                        self.storage.registry.add_origin(grupo, stem, src_str)
                        print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} → {src_str}{C('rst')}")
                    break
                if op == 'o':
                    if self.ui.leer(f"  ¿Sobrescribir vault? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                        continue
                    if es_remoto(src_str):
                        contenido = await remote_read_async(src_str)
                    else:
                        contenido = Path(src_str).read_bytes()
                    key_id = self.storage.registry.key_id(grupo, stem) if self.storage.registry.is_protected(grupo, stem) else None
                    self.storage.escribir_entrada(grupo, stem, contenido, key_id=key_id, cifrar=bool(key_id))
                    self.storage.registry.add_origin(grupo, stem, src_str)
                    self.storage.registry.set_type(grupo, stem, detectar_tipo_archivo(ev_path))
                    print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} actualizado desde {src_str}{C('rst')}")
                    break
                if op == 'a':
                    self.storage.registry.add_origin(grupo, stem, src_str)
                    print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                          f"  {C('link')}→ {self.ui._fmt_origin(src_str)}{C('rst')}")
                    break
                if op == 'n':
                    print(f"{C('date')}  Cancelado.{C('rst')}")
                    break
                print("  Opción no válida.")
            return

        print(f"{C('minus')}  Ni el externo ni la entrada existen.{C('rst')}")
    ### fin de función cmd_link

    ## función cmd_unlink - desenlaza el origen de una entrada
    def cmd_unlink(self, args: List[str]) -> None:
        if not args:
            enlazados = [
                (g, stem, self.storage.registry.get_origins(g, stem))
                for g in self.storage.get_grupos()
                for stem in self.storage.get_entradas(g)
                if self.storage.registry.get_origins(g, stem)
            ]
            if not enlazados:
                print(f"{C('date')}  No hay enlaces registrados.{C('rst')}")
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
            print(f"  {self.ui.render_ruta(grupo, stem)}  {C('date')}(sin enlaces){C('rst')}")
            return

        if len(origins) == 1:
            origen = origins[0]
            if self.ui.leer(f"  ¿Desenlazar {self.ui.render_ruta(grupo, stem)} de {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                self.storage.registry.remove_origin(grupo, stem, origen)
                print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}desenlazado{C('rst')}")
        else:
            print(f"  Múltiples orígenes:")
            for idx, origin in enumerate(origins):
                print(f"    [{idx+1}] → {self.ui._fmt_origin(origin)}")
            op = self.ui.leer("  Número, 't' todos, 'c' cancelar: ")
            if op == 'c':
                return
            if op == 't':
                if self.ui.leer(f"  ¿Eliminar todos? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                    self.storage.registry.remove_all_origins(grupo, stem)
                    print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}todos eliminados{C('rst')}")
                return
            if op.isdigit():
                idx = int(op) - 1
                if 0 <= idx < len(origins):
                    origen = origins[idx]
                    if self.ui.leer(f"  ¿Desenlazar {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                        self.storage.registry.remove_origin(grupo, stem, origen)
                        print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}desenlazado{C('rst')}")
                else:
                    print("  Número inválido.")
    ### fin de función cmd_unlink

    ## función cmd_del - envía una entrada o grupo al trash
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
            if self.ui.leer(f"Enviar al trash '{grupo}/'? (s/{C('date')}N{C('rst')}): ") == "s":
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
            if self.ui.leer(f"Enviar al trash {grupo}/{ev_path.name}? (s/{C('date')}N{C('rst')}): ") == "s":
                self.storage.registry.remove_all_origins(grupo, stem)
                self.storage.registry.remove_info(grupo, stem)
                self.storage.registry.unmark_gpg(grupo, stem)
                self.storage.trash(ev_path)
                print(f"{C('minus')}- {ruta_fmt}{C('rst')}")
            self.storage.limpiar_vacios()
            return

        print(f"\n{C('header')}La entrada {ruta_fmt} tiene {len(versiones)} versiones.{C('rst')}")
        print("  [t] Borrar todo  [v] Borrar versión  [c] Cancelar")
        op = self.ui.leer("  Elige (t/v/c): ").lower()
        if op == 'c' or not op:
            print(f"{C('date')}Cancelado.{C('rst')}")
            return

        if op == 't':
            if self.ui.leer(f"¿Eliminar {ruta_fmt} y todas sus versiones? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                version_dir = self.storage.versions_path / grupo / stem
                if version_dir.is_dir():
                    shutil.rmtree(version_dir)
                self.storage.registry.remove_all_origins(grupo, stem)
                self.storage.registry.remove_info(grupo, stem)
                self.storage.registry.unmark_gpg(grupo, stem)
                self.storage.trash(ev_path)
                print(f"{C('minus')}- {ruta_fmt} (y versiones){C('rst')}")
                self.storage.limpiar_vacios()
            else:
                print(f"{C('date')}Cancelado.{C('rst')}")

        elif op == 'v':
            self._borrar_version_interactivo(grupo, stem, versiones)
    ### fin de función cmd_del

    ## función _borrar_version_interactivo - borra una versión de forma interactiva
    def _borrar_version_interactivo(self, grupo: str, stem: str, versiones: List[Path]) -> None:
        if len(versiones) == 1:
            vpath = versiones[0]
            fecha = self._fmt_version_fecha(vpath)
            if self.ui.leer(f"  ¿Eliminar la única versión ({fecha})? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                self._unlink_version(vpath)
                print(f"{C('minus')}✓ Eliminada versión {fecha}{C('rst')}")
            else:
                print(f"{C('date')}Cancelado.{C('rst')}")
            return

        print(f"\n{C('header')}Versiones:{C('rst')}")
        for i, vpath in enumerate(versiones, 1):
            print(f"  [{i}] {C('date')}{self._fmt_version_fecha(vpath)}{C('rst')}")
        seleccion = self.ui.leer("  Número a eliminar (o 'c'): ")
        if seleccion.lower() == 'c' or not seleccion:
            print(f"{C('date')}Cancelado.{C('rst')}")
            return
        if seleccion.isdigit():
            idx = int(seleccion) - 1
            if 0 <= idx < len(versiones):
                vpath = versiones[idx]
                fecha = self._fmt_version_fecha(vpath)
                if self.ui.leer(f"  ¿Eliminar versión {fecha}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                    self._unlink_version(vpath)
                    print(f"{C('minus')}✓ Eliminada {fecha}{C('rst')}")
                else:
                    print(f"{C('date')}Cancelado.{C('rst')}")
            else:
                print(f"{C('warn')}Índice inválido.{C('rst')}")
    ### fin de función _borrar_version_interactivo

    ## función _unlink_version - elimina un archivo de versión y limpia directorios vacíos
    def _unlink_version(self, vpath: Path) -> None:
        vpath.unlink()
        parent = vpath.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            gp = parent.parent
            if gp.is_dir() and not any(gp.iterdir()):
                gp.rmdir()
    ### fin de función _unlink_version

    ## función _renombrar - renombra una entrada dentro del mismo grupo
    def _renombrar(self, grupo: str, origen: str, destino: str) -> None:
        if not self._validar_stem(destino):
            return
        p_src = self.storage.get_entrada_path(grupo, origen)
        if not p_src:
            print(f"No existe: {grupo}/{origen}")
            return
        p_dest = self.storage.entrada_path(grupo, destino, ext=p_src.suffix)
        if p_dest.is_file():
            if self.ui.leer(f"Ya existe {grupo}/{destino}. ¿Sobrescribir? (s/{C('date')}N{C('rst')}): ").lower() != 's':
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
        print(f"{C('plus')}✓ Renombrado: {grupo}/{origen} → {grupo}/{destino}{C('rst')}")
    ### fin de función _renombrar

    ## función _mover - mueve una entrada a otro grupo
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
        print(f"{C('plus')}✓ Movido: {self.ui.render_ruta(g_src, e_src)} ➔ {self.ui.render_ruta(g_dest, e_dest)}{C('rst')}")
    ### fin de función _mover

    ## función _fusionar - fusiona dos entradas en una
    def _fusionar(self, g_dest: str, e_dest: str, g_src: str, e_src: str) -> None:
        if g_src == g_dest and e_src == e_dest:
            print(f"{C('warn')}No se puede fusionar consigo mismo.{C('rst')}")
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
        print(f"{C('plus')}✓ Fusionado: {self.ui.render_ruta(g_src, e_src)} ➔ {self.ui.render_ruta(g_dest, e_dest)}{C('rst')}")
    ### fin de función _fusionar

    ## función cmd_mv - mueve o fusiona entradas (interactivo o por argumentos)
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
    ### fin de función cmd_mv

    ## función _clave_existe - verifica si una clave GPG existe en el llavero
    def _clave_existe(self, clave: str) -> bool:
        try:
            result = subprocess.run(
                ["gpg", "--list-keys", "--with-colons", clave],
                capture_output=True, text=True
            )
            return result.returncode == 0 and ("pub" in result.stdout or "uid" in result.stdout)
        except Exception:
            return False
    ### fin de función _clave_existe

    ## función cmd_gpg - cifra una entrada con GPG o añade destinatarios
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
        d, r, w = C("date"), C("rst"), C("warn")

        if ya_cifrado:
            key_actual = self.storage.registry.key_id(grupo, stem) or self.config.gpg_key
            actuales = [k for k in (key_actual or "").split(",") if k]
            if actuales:
                print(f"  {w}g{r} destinatarios actuales:")
                for k in actuales:
                    etiq = f"{w}primaria{r}" if k == self.config.gpg_key else f"{d}secundaria{r}"
                    print(f"    {d}{k}{r}  {etiq}")
            nuevas = list(extra_keys)
            while True:
                resp = self.ui.leer(f"  Añadir llave secundaria {d}(Enter termina){r}: ")
                if not resp:
                    break
                nuevas.append(resp)
            if not nuevas:
                print(f"{d}  Sin cambios.{r}")
                return
            todos = list(actuales)
            for k in nuevas:
                if k not in todos:
                    todos.append(k)
            validas = [k for k in todos if self._clave_existe(k)]
            invalidas = [k for k in todos if k not in validas]
            if invalidas:
                print(f"{w}Claves no encontradas (ignoradas):{r}")
                for k in invalidas:
                    print(f"  {d}{k}{r}")
                if not validas:
                    print(f"{w}Sin claves válidas. Cancelado.{r}")
                    return
                if self.ui.leer(f"  Continuar con las válidas? (s/{d}N{r}): ").lower() != 's':
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
            print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} → {' '.join(validas)}")
            return

        if not self.config.gpg_key:
            print(f"{w}Sin llave primaria configurada. Usa 'byte x' para configurarla.{r}")
            return

        if ev_path and ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo_real = detectar_tipo_archivo(ev_path)
            if self.storage.registry.get_type(grupo, stem) != tipo_real:
                self.storage.registry.set_type(grupo, stem, tipo_real)

        all_keys = [self.config.gpg_key] + [k for k in self.config.gpg_keys_secondary if k != self.config.gpg_key]
        validas = [k for k in all_keys if self._clave_existe(k)]
        invalidas = [k for k in all_keys if k not in validas]
        if invalidas:
            print(f"{w}Claves no encontradas (ignoradas):{r}")
            for k in invalidas:
                print(f"  {d}{k}{r}")
            if not validas:
                print(f"{w}Sin claves válidas. Cancelado.{r}")
                return
            if self.ui.leer(f"  Continuar? (s/{d}N{r}): ").lower() != 's':
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
        print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} {w}{self.config.gpg_key}{r}")
    ### fin de función cmd_gpg

    ## función cmd_nogpg - descifra una entrada protegida con GPG
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
        if self.ui.leer(f"  ¿Descifrar {grupo}/{stem}? (s/{C('date')}N{C('rst')}): ") != "s":
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
        print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)}{C('rst')} (descifrado)")
    ### fin de función cmd_nogpg

    ## función cmd_check - verifica configuración y sincronización de enlaces
    async def cmd_check(self, args: List[str]) -> None:
        """Muestra configuración y verifica sincronización de enlaces."""
        c, d, r, w = C("header"), C("date"), C("rst"), C("warn")

        print(f"\n{c}=== CONFIGURACIÓN ==={r}")
        if self.config.used_config_path:
            print(f"Archivo: {str(self.config.used_config_path).replace(str(Path.home()), '~')}")
        print(f"Directorio: {str(self.storage.base).replace(str(Path.home()), '~')}")
        print(f"Editor: {self.config.editor}")
        print(f"Clave GPG primaria: {self.config.gpg_key or '(no configurada)'}")
        if self.config.gpg_keys_secondary:
            print(f"Claves GPG secundarias: {', '.join(self.config.gpg_keys_secondary)}")
        print()

        # Verificar tipos de archivo
        tipo_cambiado = False
        for g in self.storage.get_grupos():
            for stem in self.storage.get_entradas(g):
                ev_path = self.storage.get_entrada_path(g, stem)
                if not ev_path or ev_path.suffix.lower() == ".gpg":
                    continue
                tipo_reg = self.storage.registry.get_type(g, stem)
                tipo_real = detectar_tipo_archivo(ev_path)
                if tipo_reg != tipo_real:
                    if not tipo_cambiado:
                        print(f"{c}Verificando tipos...{r}")
                        tipo_cambiado = True
                    print(f"  {self.ui.render_ruta(g, stem)}: registrado '{tipo_reg}' pero es '{tipo_real}'")
                    if self.ui.leer(f"  ¿Actualizar? (s/{d}N{r}): ").lower() == "s":
                        self.storage.registry.set_type(g, stem, tipo_real)
                        print(f"    {C('plus')}✓ Actualizado{r}")
        if tipo_cambiado:
            print()

        # Recopilar candidatos con enlaces (links.json ya tiene rutas absolutas desde cmd_link)
        candidatos = []
        for g in self.storage.get_grupos():
            for stem in self.storage.get_entradas(g):
                ev_path = self.storage.get_entrada_path(g, stem)
                if not ev_path:
                    continue
                for origin in self.storage.registry.get_origins(g, stem):
                    candidatos.append((g, stem, ev_path, origin))

        if not candidatos:
            print(f"{d}No hay enlaces registrados.{r}")
            self.ui.update_all_abbreviations()
            print(f"{d}Caché de abreviaturas actualizada.{r}")
            return

        # Verificar todos los enlaces en paralelo
        ## función verificar - verifica si una entrada está sincronizada con su origen
        async def verificar(g: str, stem: str, ev_path: Path, src_str: str):
            if not es_remoto(src_str):
                src = Path(src_str)
                if not src.is_file():
                    return g, stem, ev_path, src_str, None, None, None  # no disponible
                es_gpg = ev_path.suffix.lower() == ".gpg"
                if es_gpg:
                    contenido_ev = self.storage.leer_entrada(g, stem)
                    if contenido_ev is None:
                        return g, stem, ev_path, src_str, None, None, "gpg_error"
                    diff = contenido_ev != src.read_bytes()
                else:
                    ## función archivos_iguales - compara si dos archivos locales son iguales
                    def archivos_iguales(a: Path, b: Path) -> bool:
                        return a.stat().st_size == b.stat().st_size and calcular_md5(a) == calcular_md5(b)
                    ### fin de función archivos_iguales
                    diff = not archivos_iguales(ev_path, src)
                return g, stem, ev_path, src_str, diff, None, None
            else:
                try:
                    resultado = await remote_check_async(src_str)
                except Exception as e:
                    return g, stem, ev_path, src_str, None, None, str(e)
                if resultado is None:
                    return g, stem, ev_path, src_str, None, None, None  # no disponible
                mtime_remoto, contenido_remoto = resultado
                es_gpg = ev_path.suffix.lower() == ".gpg"
                if es_gpg:
                    contenido_ev = self.storage.leer_entrada(g, stem)
                    if contenido_ev is None:
                        return g, stem, ev_path, src_str, None, None, "gpg_error"
                    diff = contenido_ev != contenido_remoto
                else:
        ### fin de función archivos_iguales
                    diff = ev_path.read_bytes() != contenido_remoto
                return g, stem, ev_path, src_str, diff, (mtime_remoto, contenido_remoto), None

        resultados = await asyncio.gather(*[verificar(g, stem, ep, o) for g, stem, ep, o in candidatos])

        cambios = []
        for g, stem, ev_path, src_str, diff, remote_data, error in resultados:
            if error == "gpg_error":
                print(f"{w}{g}/{stem} — no se pudo descifrar (GPG), omitido.{r}")
            elif error is not None:
                print(f"{w}{g}/{stem} — error: {error}{r}")
            elif diff is None:
                print(f"{d}{g}/{stem} → origen no disponible: {self.ui._fmt_origin(src_str)} (omitido){r}")
            elif diff:
                cambios.append((g, stem, ev_path, src_str, remote_data))

        # Procesar cambios secuencialmente (requiere input)
        for g, stem, ev_path, src_str, remote_data in cambios:
            es_gpg = ev_path.suffix.lower() == ".gpg"
            ruta_fmt = self.ui.render_ruta(g, stem)
            origen_fmt = self.ui._fmt_origin(src_str)
            gpg_tag = f" {w}g{r}" if es_gpg else ""
            print(f"\n{C('bold')}{ruta_fmt}{r}{gpg_tag}"
                  f"  {C('link')}c → {origen_fmt}{r}"
                  f"  {d}(modificado){r}")

            mtime_ev = datetime.fromtimestamp(ev_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if not es_remoto(src_str):
                mtime_src = datetime.fromtimestamp(Path(src_str).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                contenido_remoto_cache = None
            else:
                mtime_remoto, contenido_remoto_cache = remote_data
                mtime_src = datetime.fromtimestamp(mtime_remoto).strftime("%Y-%m-%d %H:%M")
            print(f"  {d}entrada: {mtime_ev}  |  origen: {mtime_src}{r}")

            es_bin = (not es_gpg and self.storage.registry.get_type(g, stem) == "binary") or \
                     (not es_remoto(src_str) and detectar_tipo_archivo(Path(src_str)) == "binary")

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
                    print(f"  MD5 origen: {md5_src}")
                    print(f"  MD5 entrada: {md5_ev}")

                elif res == "d" and not es_bin:
                    if es_remoto(src_str):
                        await self.mostrar_diff_remoto_async(ev_path, contenido_remoto_cache)
                    else:
                        await self.mostrar_diff_async(ev_path, Path(src_str))

                elif res == "o":
                    if es_remoto(src_str):
                        self.storage.escribir_entrada(g, stem, contenido_remoto_cache, cifrar=bool(es_gpg))
                    else:
                        shutil.copy2(Path(src_str), ev_path)
                    self.storage._invalidar_cache_grupo(g)
                    self.ui.invalidar_cache_abreviaturas(g)
                    if not es_gpg:
                        self.storage.registry.set_type(g, stem, detectar_tipo_archivo(ev_path))
                    print(f"{C('plus')}  ✓ Entrada actualizada{r}")
                    break

                elif res == "e":
                    if es_remoto(src_str):
                        try:
                            contenido = self.storage.leer_entrada(g, stem)
                            if contenido is None:
                                print("Error leyendo entrada")
                                break
                            await remote_write_async(src_str, contenido)
                            print(f"{C('plus')}  ✓ Origen remoto actualizado{r}")
                        except Exception as e:
                            print(f"Error subiendo: {e}")
                    else:
                        shutil.copy2(ev_path, Path(src_str))
                        print(f"{C('plus')}  ✓ Origen actualizado{r}")
                    break

                else:
                    print(f"{d}  Omitido{r}")
                    break
    ### fin de función archivos_iguales

        print(f"{d}Revisión completada.{r}")
        self.ui.update_all_abbreviations()
        print(f"{d}Caché de abreviaturas actualizada.{r}")

    ## función cmd_info - muestra o guarda la nota de una entrada/grupo
    def cmd_info(self, args: List[str]) -> None:
        if not args:
            encontrado = False
            for g in self.storage.get_grupos():
                for stem in self.storage.get_entradas(g):
                    txt = self.storage.registry.get_info(g, stem)
                    if txt:
                        print(f"  {self.ui.render_ruta(g, stem)}  {C('date')}{txt}{C('rst')}")
                        encontrado = True
            if not encontrado:
                print(f"{C('date')}  (ninguna entrada tiene info){C('rst')}")
            return

        grupo, stem = self.resolver_arg(args[0])
        if grupo is None:
            print(f"No encontrado: '{args[0]}'")
            return

        if stem is not None:
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            d, r, w = C("date"), C("rst"), C("warn")
            if len(args) >= 2:
                self.storage.registry.set_info(grupo, stem, " ".join(args[1:]))
                print(f"{d}Nota guardada para {ruta_fmt}{r}")
                return
            txt = self.storage.registry.get_info(grupo, stem)
            if txt:
                print(f"{d}{txt}{r}")
            if self.storage.registry.is_protected(grupo, stem):
                print(f"{w}Cifrado: {self.storage.registry.key_id(grupo, stem)}{r}")
            if self.storage.registry.get_type(grupo, stem) == "binary":
                print("Tipo: binario")
            for origin in self.storage.registry.get_origins(grupo, stem):
                print(f"  → {d}{self.ui._fmt_origin(origin)}{r}")
            versiones = self.storage.listar_versiones(grupo, stem)
            if versiones:
                print(f"\n  {C('header')}Versiones:{C('rst')}")
                for i, vpath in enumerate(versiones, 1):
                    print(f"    [{i}] {C('date')}{self._fmt_version_fecha(vpath)}{C('rst')}")
            return

        evs = self.storage.get_entradas(grupo)
        if not evs:
            print(f"{C('date')}El grupo {grupo} no tiene entradas.{C('rst')}")
            return
        print(f"\n{C('header')}Grupo: {grupo}{C('rst')}")
        for s in evs:
            ruta_fmt = self.ui.render_ruta(grupo, s)
            txt = self.storage.registry.get_info(grupo, s) or "(sin nota)"
            badges = self.ui._get_badges_compactos(grupo, s)
            versiones = self.storage.listar_versiones(grupo, s)
            ver_str = f" {C('date')}[{len(versiones)}v]{C('rst')}" if versiones else ""
            print(f"  {badges} {ruta_fmt}: {C('date')}{txt}{C('rst')}{ver_str}")
    ### fin de función cmd_info

    ## función cmd_config - configuración interactiva de byte
    def cmd_config(self, args: List[str]) -> None:
        c, r, d, w = C("bold"), C("rst"), C("date"), C("warn")
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        target = system_path if system_path.is_file() else self.config.base / ".byte" / "byte.toml"
        disp = str(target).replace(str(Path.home()), "~")
        print(f"\n{c}BYTE — Configuración{r}")
        print(f"Archivo: {disp}\n")
        print(f"Vista por columnas: {d}{'sí' if self.config.columnas_default else 'no'}{r}")
        print(f"Buscar en cifrados: {d}{'sí' if self.config.search_encrypted else 'no'}{r}\n")

        resp = self.ui.leer(f"Directorio base [{self.config.base}]: ")
        nueva_base = Path(resp).expanduser().resolve() if resp else self.config.base

        resp = self.ui.leer(f"Editor [{self.config.editor}]: ")
        nuevo_editor = resp or self.config.editor

        print(f"\n{w}Llave GPG primaria{r}")
        resp = self.ui.leer(f"[{self.config.gpg_key or 'ninguna'}]: ")
        nueva_primaria = resp or self.config.gpg_key

        print(f"\n{d}Llaves secundarias actuales:{r}")
        if self.config.gpg_keys_secondary:
            for k in self.config.gpg_keys_secondary:
                print(f"  [{k}]")
        else:
            print(f"  {d}(ninguna){r}")

        nuevas_sec = []
        resp = ""
        while True:
            resp = self.ui.leer(f"Nueva llave ({d}vacío termina, '-' borra todas{r}): ")
            if not resp:
                break
            if resp == "-":
                nuevas_sec = []
                print(f"{d}Secundarias eliminadas.{r}")
                break
            if "@" in resp and "." in resp.split("@")[1]:
                nuevas_sec.append(resp)
            else:
                print(f"{w}Formato inválido.{r}")
        if not nuevas_sec and resp != "-":
            nuevas_sec = list(self.config.gpg_keys_secondary)

        resp_col = self.ui.leer(f"¿Columnas por defecto? (s/{d}N{r}): ").lower()
        nuevas_columnas = resp_col == "s"

        resp_enc = self.ui.leer(f"¿Buscar en cifrados? (s/{d}N{r}): ").lower()
        nuevas_search_enc = resp_enc == "s"

        resp = self.ui.leer(f"Ruta versiones [{self.config.versions_path}]: ")
        nuevas_versions = Path(resp).expanduser().resolve() if resp else self.config.versions_path

        print(f"\n{d}Herramienta para diff (auto/delta/bat/diff){r}")
        resp_diff = self.ui.leer(f"[{self.config.diff_tool}]: ").strip().lower()
        nuevo_diff = resp_diff if resp_diff in ("auto", "delta", "bat", "diff") else self.config.diff_tool

        self.config.save(nueva_base, nuevo_editor, nueva_primaria, nuevas_sec,
                         nuevas_columnas, nuevas_search_enc, nuevas_versions, nuevo_diff)
        self.storage = ByteStorage(self.config.base, self.config)
        self.ui = ByteInterface(self.storage, self.config.columnas_default)
        print(f"{C('plus')}✓ Guardado en {disp}{r}")
    ### fin de función cmd_config

    ## función cmd_version - guarda una versión de una entrada
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
            print(f"{C('warn')}La entrada {grupo}/{stem} no existe.{C('rst')}")
            return

        version_path = self.storage.guardar_version(grupo, stem)
        if version_path:
            print(f"{C('plus')}✓ Versión guardada: {self.ui.render_ruta(grupo, stem)}"
                  f" → {C('date')}{self._fmt_version_fecha(version_path)}{C('rst')}")
        else:
            print(f"{C('warn')}Error al guardar la versión.{C('rst')}")
    ### fin de función cmd_version

    ## función cmd_restore - restaura una versión anterior de una entrada
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
            print(f"{C('date')}No hay versiones para {grupo}/{stem}.{C('rst')}")
            return

        if seleccion:
            if seleccion.isdigit():
                idx = int(seleccion) - 1
                if 0 <= idx < len(versiones):
                    version_elegida = versiones[idx]
                else:
                    print(f"{C('warn')}Índice inválido.{C('rst')}")
                    return
            else:
                matching = [v for v in versiones if v.stem.startswith(seleccion)]
                if matching:
                    version_elegida = matching[0]
                else:
                    print(f"{C('warn')}No se encontró versión '{seleccion}'.{C('rst')}")
                    return
        else:
            print(f"\n{C('header')}Versiones de {self.ui.render_ruta(grupo, stem)}:{C('rst')}")
            for i, vpath in enumerate(versiones, 1):
                print(f"  [{i}] {C('date')}{self._fmt_version_fecha(vpath)}{C('rst')}")
            print()
            op = self.ui.leer("  Número, 'd' diff, 'c' cancelar: ")
            if op.lower() == 'c' or not op:
                print(f"{C('date')}Cancelado.{C('rst')}")
                return
            if op.lower() == 'd':
                ev_actual = self.storage.get_entrada_path(grupo, stem)
                if ev_actual and ev_actual.is_file():
                    await self.mostrar_diff_async(ev_actual, versiones[0])
                return await self.cmd_restore([entrada])
            if not op.isdigit():
                print(f"{C('warn')}Opción inválida.{C('rst')}")
                return
            idx = int(op) - 1
            if idx < 0 or idx >= len(versiones):
                print(f"{C('warn')}Índice inválido.{C('rst')}")
                return
            version_elegida = versiones[idx]

        fecha = self._fmt_version_fecha(version_elegida)
        print(f"\n  Versión elegida: {C('date')}{fecha}{C('rst')}")
        if self.ui.leer(f"  ¿Restaurar? (s/{C('date')}N{C('rst')}): ").lower() != 's':
            print(f"{C('date')}Cancelado.{C('rst')}")
            return

        if self.storage.restaurar_version(grupo, stem, version_elegida):
            print(f"{C('plus')}✓ Restaurada {fecha} en {self.ui.render_ruta(grupo, stem)}{C('rst')}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
        else:
            print(f"{C('warn')}Error al restaurar.{C('rst')}")
    ### fin de función cmd_restore

    ## función cmd_search - busca un patrón de texto en las entradas
    def cmd_search(self, args: List[str]) -> None:
        if not args:
            print(f"{C('warn')}Uso: byte s <patrón> [grupo/]{C('rst')}")
            return
        pattern = args[0]
        grupo_filtro = None
        if len(args) > 1:
            g = self.find_grupo(args[1])
            if g:
                grupo_filtro = g
            else:
                print(f"{C('warn')}Grupo no válido: {args[1]}{C('rst')}")
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
            print(f"{C('date')}No hay archivos de texto para buscar.{C('rst')}")
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
                lock = f"{C('warn')}🔒 {C('rst')}" if path.suffix.lower() == ".gpg" else ""
                print(f"\n{C('bold')}{lock}{self.ui.render_ruta(grupo, ev)}{C('rst')}")
                for line in result.stdout.splitlines():
                    parts = line.split(':', 2)
                    if len(parts) >= 3:
                        line = f"{C('count')}{parts[1]}{C('rst')}:{parts[2]}"
                    print(f"  {line}")

        if not found:
            print(f"{C('date')}No se encontraron coincidencias para '{pattern}'.{C('rst')}")
    ### fin de función cmd_search

    ## función mostrar_ayuda - muestra la ayuda de comandos
    def mostrar_ayuda(self) -> None:
        h, t, d, r, w = C("header"), C("tree"), C("date"), C("rst"), C("warn")
        print(f"{h}BYTE — Notas en Markdown y archivos binarios{r}\n")
        print(f"  {t}byte{r}              {d}árbol{r}")
        print(f"  {t}byte -t{r}           {d}árbol con fechas{r}")
        print(f"  {t}byte --columnas{r}   {d}árbol en columnas{r}")
        print(f"  {t}byte -h{r}           {d}esta ayuda{r}")
        print()
        print(f"  {h}Abrir / añadir{r}")
        print(f"  {t}byte{r} {d}entrada{r}              abre en editor")
        print(f"  {t}byte{r} {d}entrada{r} texto...      añade línea timestampeada")
        print(f"  {t}byte{r} {d}Grupo/entrada{r}         abre entrada explícita")
        print()
        print(f"  {h}Comandos{r}  {d}--comando · letra{r}")
        print(f"  {t}--link    {d}l{r}  archivo {d}[nombre]{r}    enlaza archivo externo (ruta absoluta siempre)")
        print(f"  {t}--unlink  {d}u{r}  {d}[entrada]{r}            quita enlace")
        print(f"  {t}--del     {d}d{r}  {d}[ruta]{r}              envía al .trash/")
        print(f"  {t}--mv      {d}m{r}  {d}[origen] [destino]{r}  mueve o fusiona")
        print(f"  {t}--info    {d}i{r}  {d}[entrada] [texto]{r}    nota corta")
        print(f"  {t}--gpg     {d}g{r}  entrada              cifra con GPG")
        print(f"  {t}--nogpg   {d}q{r}  entrada              descifra")
        print(f"  {t}--check   {d}c{r}                      verifica configuración y enlaces")
        print(f"  {t}--config  {d}x{r}                      configuración (incluye diff_tool)")
        print(f"  {t}--search  {d}s{r}  texto {d}[grupo]{r}       busca con rg/grep")
        print(f"  {t}--version {d}v{r}  entrada              guarda versión")
        print(f"  {t}--restore {d}r{r}  entrada {d}[n|timestamp]{r} restaura versión")
        print()
        print(f"  {h}Indicadores{r}")
        print(f"  {w}g{r} gpg  {d}b{r} binario  {w}i{r} info  {d}c →{r} copia  {d}r →{r} remoto  {d}x{r} enlace roto")
    ### fin de función mostrar_ayuda

# ============================================================================
# MAIN
# ============================================================================

## función async_main - punto de entrada async: parsea argumentos y despacha comandos
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

    # Comandos async (necesitan await)
    async_cmds = {
        "link": app.cmd_link,   "l": app.cmd_link,
        "check": app.cmd_check, "c": app.cmd_check,
        "restore": app.cmd_restore, "r": app.cmd_restore,
    }
    # Comandos síncronos (llamada directa)
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
### fin de función async_main


## función main - punto de entrada del programa
def main() -> None:
    asyncio.run(async_main())
### fin de función main


if __name__ == "__main__":
    main()
