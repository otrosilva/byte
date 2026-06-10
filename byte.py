#!/usr/bin/env python3
# byte.py — gestor de notas Markdown y archivos binarios (Linux/macOS)

import os
import sys
import re
import json
import shutil
import subprocess
import tempfile
import unicodedata
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set
from functools import lru_cache

# tomllib para leer TOML
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# ============================================================================
# COLORES ANSI
# ============================================================================

_ANSI = {
    "rst":    "\033[0m",
    "bold":   "\033[1;37m",
    "event":  "\033[0;37m",
    "header": "\033[1;37m",
    "plus":   "\033[1;37m",
    "minus":  "\033[38;5;167m",
    "tree":   "\033[38;5;238m",
    "group":  "\033[38;5;250m",
    "count":  "\033[1;37m",
    "date":   "\033[38;5;243m",
    "link":   "\033[38;5;245m",
    "warn":   "\033[33m",
}

def C(key: str) -> str:
    return _ANSI.get(key, "")

# ============================================================================
# UTILIDADES ANSI (con caché)
# ============================================================================

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

@lru_cache(maxsize=1024)
def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)

@lru_cache(maxsize=1024)
def pad_ansi(s: str, width: int) -> str:
    return s + " " * max(0, width - len(strip_ansi(s)))

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

class Config:
    DEFAULT_BASE = Path.home() / "Documentos/Filen/Obsidian/bytes"
    DEFAULT_EDITOR = os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")

    def __init__(self):
        """Inicializa la app con la configuración, crea storage e interfaz."""
        self.base: Path = self.DEFAULT_BASE
        self.editor: str = self.DEFAULT_EDITOR
        self.gpg_key: str = ""
        self.gpg_keys_secondary: List[str] = []
        self.used_config_path: Optional[Path] = None
        self.columnas_default: bool = False
        self._load()

    def _load_toml_file(self, path: Path) -> Dict[str, Any]:
        """Carga y parsea un archivo TOML, devuelve dict vacío si no existe o no hay parser."""
        if not path.is_file() or tomllib is None:
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _create_default_config(self, path: Path) -> None:
        """Crea un archivo byte.toml por defecto en la ruta indicada."""
        path.parent.mkdir(parents=True, exist_ok=True)
        contenido = f'base   = "{self.DEFAULT_BASE}"\neditor = "{self.DEFAULT_EDITOR}"\ngpg_key = ""\ngpg_keys_secondary = []\ncolumnas = false\n'
        path.write_text(contenido, encoding="utf-8")

    def _load(self) -> None:
        """Carga byte.json en memoria, o crea estructura por defecto si no existe."""
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
            self.columnas_default = cfg.get("columnas", False)
            if not isinstance(self.columnas_default, bool):
                self.columnas_default = False

    def save(self, base: Path, editor: str, gpg_key: str, gpg_keys_secondary: List[str]) -> None:
        """Guarda la configuración actual en el archivo byte.toml (usado por cmd_config)."""
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        target = system_path if system_path.is_file() else self.base / ".byte" / "byte.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'base   = "{base}"', f'editor = "{editor}"']
        if gpg_key:
            lines.append(f'gpg_key = "{gpg_key}"')
        lines.append(f'gpg_keys_secondary = [{", ".join(f"\"{k}\"" for k in gpg_keys_secondary)}]' if gpg_keys_secondary else 'gpg_keys_secondary = []')
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Nota: columnas_default no se guarda en este método porque cmd_config escribe directamente
        self.base = base
        self.editor = editor
        self.gpg_key = gpg_key
        self.gpg_keys_secondary = gpg_keys_secondary
        self.used_config_path = target

# ============================================================================
# EXTENSIONES RECONOCIDAS COMO TEXTO
# ============================================================================

EXT_TEXTO = {
    ".md", ".txt", ".csv", ".tsv", ".log", ".org", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".css", ".js", ".py", ".sh", ".lua", ".rb", ".go", ".rs",
    ".zshrc", ".bashrc", ".profile", ".bash_profile", ".zshenv",
    ".gitconfig", ".gitignore", ".editorconfig",
}

# ============================================================================
# UTILIDADES
# ============================================================================

def _diff_tool() -> Optional[str]:
    """Devuelve 'delta' o 'bat' si están instalados, sino None."""
    for tool in ("delta", "bat"):
        if shutil.which(tool):
            return tool
    return None

def mostrar_diff(a: Path, b: Path) -> None:
    """Muestra las diferencias entre dos archivos usando diff -u y opcionalmente delta/bat."""
    tool = _diff_tool()
    a_str, b_str = str(a), str(b)
    # Obtener el diff
    r = subprocess.run(["diff", "-u", a_str, b_str], capture_output=True, text=True)
    if not r.stdout.strip():
        print("  (sin diferencias)")
        return
    if tool == "delta":
        # delta por defecto puede paginar; forzamos a que muestre todo sin paginador
        os.system(f'diff -u "{a_str}" "{b_str}" | delta --paging=never')
    elif tool == "bat":
        # bat también puede paginar; usamos --pager=never
        tmp = tempfile.NamedTemporaryFile(suffix=".diff", delete=False, mode="w")
        tmp.write(r.stdout)
        tmp.close()
        os.system(f'bat --language=diff --pager=never "{tmp.name}"')
        Path(tmp.name).unlink()
    else:
        # Si no hay herramienta, imprimir crudo
        print(r.stdout, end="")

def calcular_md5(path: Path) -> str:
    """Calcula el hash MD5 de un archivo (usado para comparar binarios)."""
    hash_md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def detectar_tipo_archivo(path: Path) -> str:
    """Determina si un archivo es texto (UTF-8) o binario leyendo los primeros 1024 bytes."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read(1024)
        return "text"
    except (UnicodeDecodeError, OSError):
        return "binary"

def _resaltar(texto: str, abrev: Optional[str], long: int, color_norm: str, color_res: str) -> str:
    """Resalta una subcadena (abreviatura) dentro del texto con colores ANSI."""
    if not abrev:
        return color_norm + texto + C("rst")
    idx = texto.find(abrev)
    if idx != -1:
        pre = texto[:idx]
        lbl = texto[idx:idx+long]
        post = texto[idx+long:]
        return f"{color_norm}{pre}{C('rst')}{color_res}{lbl}{C('rst')}{color_norm}{post}{C('rst')}"
    return f"{color_norm}{texto} {color_res}{abrev}{C('rst')}"

# ============================================================================
# REGISTRO ÚNICO (byte.json)
# ============================================================================

class Registry:
    def __init__(self, base: Path):
        """Inicializa la app con la configuración, crea storage e interfaz."""
        self.path = base / ".byte" / "byte.json"
        self._data = None
        self._mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        """Carga byte.json en memoria, o crea estructura por defecto si no existe."""
        if not self.path.is_file():
            self._data = self._default_data()
            self._mtime = 0.0
            return
        mtime_actual = self.path.stat().st_mtime
        if self._data is not None and mtime_actual == self._mtime:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._mtime = mtime_actual
        except Exception:
            self._data = self._default_data()

    def _save(self) -> None:
        """Guarda los datos en byte.json."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        self._mtime = self.path.stat().st_mtime

    def _default_data(self):
        """Estructura inicial del registro."""
        return {"links": {}, "info": {}, "gpg": {}, "abbr_cache": {}}

    def _key(self, grupo: str, stem: str) -> str:
        """Genera la clave compuesta 'grupo/stem' para las entradas del registro."""
        return f"{grupo}/{stem}"

    # --- links ---
    def add_origin(self, grupo: str, stem: str, ruta: Path, es_copia: bool = False) -> None:
        """Registra un archivo externo como origen para un evento."""
        self._load()
        key = self._key(grupo, stem)
        entry = {"path": str(ruta), "copy": es_copia}
        if key not in self._data["links"]:
            self._data["links"][key] = []
        if entry not in self._data["links"][key]:
            self._data["links"][key].append(entry)
            self._save()

    def remove_origin(self, grupo: str, stem: str, ruta: Path) -> None:
        """Elimina un origen específico del registro (archivos externos intactos)."""
        self._load()
        key = self._key(grupo, stem)
        if key in self._data["links"]:
            self._data["links"][key] = [e for e in self._data["links"][key] if e["path"] != str(ruta)]
            if not self._data["links"][key]:
                del self._data["links"][key]
            self._save()

    def remove_all_origins(self, grupo: str, stem: str) -> None:
        """Elimina todos los orígenes registrados para un evento."""
        self._load()
        key = self._key(grupo, stem)
        self._data["links"].pop(key, None)
        self._save()

    def get_origins(self, grupo: str, stem: str) -> List[Dict[str, Any]]:
        """Devuelve la lista de orígenes registrados para un evento."""
        self._load()
        return self._data["links"].get(self._key(grupo, stem), [])

    def rename_links(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        """Actualiza la clave de los orígenes al renombrar un evento."""
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["links"]:
            self._data["links"][key_dst] = self._data["links"].pop(key_src)
            self._save()

    # --- info ---
    def get_info(self, grupo: str, stem: str) -> Optional[str]:
        """Devuelve la nota corta asociada a un evento."""
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        return entry.get("info") if isinstance(entry, dict) else None

    def get_type(self, grupo: str, stem: str) -> str:
        """Devuelve el tipo (text o binary) registrado para un evento."""
        self._load()
        entry = self._data["info"].get(self._key(grupo, stem))
        if isinstance(entry, dict) and "type" in entry:
            return entry["type"]
        return "text"

    def set_info(self, grupo: str, stem: str, texto: str) -> None:
        """Guarda o actualiza la nota corta de un evento."""
        self._load()
        key = self._key(grupo, stem)
        if key not in self._data["info"] or not isinstance(self._data["info"][key], dict):
            self._data["info"][key] = {}
        self._data["info"][key]["info"] = texto.strip()
        self._save()

    def set_type(self, grupo: str, stem: str, tipo: str) -> None:
        """Registra el tipo (text o binary) de un evento."""
        self._load()
        key = self._key(grupo, stem)
        if key not in self._data["info"] or not isinstance(self._data["info"][key], dict):
            self._data["info"][key] = {}
        self._data["info"][key]["type"] = tipo
        self._save()

    def has_info(self, grupo: str, stem: str) -> bool:
        """Indica si el evento tiene una nota asociada."""
        return self.get_info(grupo, stem) is not None

    def remove_info(self, grupo: str, stem: str) -> None:
        """Elimina la nota de un evento."""
        self._load()
        key = self._key(grupo, stem)
        self._data["info"].pop(key, None)
        self._save()

    def rename_info(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        """Actualiza la clave de la nota al renombrar un evento."""
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["info"]:
            self._data["info"][key_dst] = self._data["info"].pop(key_src)
            self._save()

    # --- gpg ---
    def mark_gpg(self, grupo: str, stem: str, key_id: str) -> None:
        """Marca un evento como cifrado con GPG y guarda la(s) clave(s) destinatario."""
        self._load()
        self._data["gpg"][self._key(grupo, stem)] = key_id
        self._save()

    def unmark_gpg(self, grupo: str, stem: str) -> None:
        """Elimina la marca GPG de un evento (el archivo ya no está cifrado)."""
        self._load()
        self._data["gpg"].pop(self._key(grupo, stem), None)
        self._save()

    def is_protected(self, grupo: str, stem: str) -> bool:
        """Indica si un evento está marcado como cifrado."""
        self._load()
        return self._key(grupo, stem) in self._data["gpg"]

    def key_id(self, grupo: str, stem: str) -> Optional[str]:
        """Devuelve la(s) clave(s) GPG asociadas a un evento cifrado."""
        self._load()
        return self._data["gpg"].get(self._key(grupo, stem))

    def rename_gpg(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        """Actualiza la clave GPG al renombrar un evento cifrado."""
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data["gpg"]:
            self._data["gpg"][key_dst] = self._data["gpg"].pop(key_src)
            self._save()

    # --- abbr_cache (persistente) ---
    def get_abbr_cache(self) -> Dict[str, Dict]:
        """Obtiene la caché de abreviaturas (por grupo) desde el registro."""
        return self._data.get("abbr_cache", {})

    def set_abbr_cache(self, abbr_cache: Dict[str, Dict]) -> None:
        """Guarda la caché de abreviaturas en el registro."""
        self._data["abbr_cache"] = abbr_cache
        self._save()

# ============================================================================
# ALMACENAMIENTO CON CACHÉ DE DIRECTORIOS
# ============================================================================

class ByteStorage:
    def __init__(self, base: Path):
        """Inicializa la app con la configuración, crea storage e interfaz."""
        self.base = base
        self.byte_dir = base / ".byte"
        self.registry = Registry(base)
        self._dir_cache: Dict[str, Tuple[float, List[Path]]] = {}

    def asegurar_base(self) -> None:
        """Crea el directorio base y .byte si no existen."""
        self.base.mkdir(parents=True, exist_ok=True)
        self.byte_dir.mkdir(parents=True, exist_ok=True)

    def _listar_grupo(self, grupo: str) -> List[Path]:
        """Devuelve la lista de rutas de archivos dentro de un grupo, con caché."""
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
        """Invalida la caché del directorio de un grupo."""
        self._dir_cache.pop(grupo, None)

    def normalize(self, txt: str) -> str:
        """Normaliza texto a minúsculas sin diacríticos."""
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", txt) if unicodedata.category(c) != "Mn")

    def titulo(self, txt: str) -> str:
        """Capitaliza la primera letra de un string."""
        return txt.strip().capitalize()

    def get_grupos(self) -> List[str]:
        """Devuelve la lista de grupos (directorios) dentro de la base."""
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir() and not d.name.startswith("."))

    def get_eventos(self, grupo: str) -> List[str]:
        """Devuelve los stems de los archivos en un grupo."""
        stems = []
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            if ext == ".gpg":
                stem = Path(f.stem).stem
            else:
                stem = f.stem
            stems.append(stem)
        seen = set()
        unicos = []
        for s in stems:
            norm = self.normalize(s)
            if norm not in seen:
                seen.add(norm)
                unicos.append(s)
        return unicos

    def get_evento_path(self, grupo: str, stem: str) -> Optional[Path]:
        """Devuelve la ruta completa del archivo de un evento."""
        for f in self._listar_grupo(grupo):
            ext = f.suffix.lower()
            if ext == ".gpg":
                if Path(f.stem).stem == stem:
                    return f
            elif f.stem == stem:
                return f
        return None

    def grupo_path(self, grupo: str) -> Path:
        """Devuelve la ruta del directorio de un grupo."""
        return self.base / self.titulo(grupo)

    def evento_path(self, grupo: str, stem: str, ext: str = ".md") -> Path:
        """Devuelve la ruta esperada de un evento (sin verificar existencia)."""
        return self.base / self.titulo(grupo) / f"{stem}{ext}"

    def trash(self, path: Path) -> None:
        """Mueve un archivo o directorio a .trash con una marca de tiempo."""
        if not path.exists():
            return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        shutil.move(path, dest)

    def mtime(self, path: Optional[Path]) -> Optional[datetime]:
        """Devuelve la fecha de modificación de un archivo o None."""
        if not path or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime)

    def limpiar_vacios(self) -> None:
        """Elimina directorios de grupos vacíos."""
        for g in self.get_grupos():
            gp = self.grupo_path(g)
            if gp.is_dir() and not any(f for f in gp.iterdir() if not f.name.startswith(".")):
                gp.rmdir()

    def leer_evento(self, grupo: str, stem: str) -> Optional[bytes]:
        """Lee el contenido completo de un evento (descifrando si es .gpg)."""
        path = self.get_evento_path(grupo, stem)
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

    def escribir_evento(self, grupo: str, stem: str, contenido: bytes,
                        key_id: Optional[str] = None, cifrar: bool = True) -> None:
        """Escribe contenido en un evento (cifrando si está protegido)."""
        ev_path = self.get_evento_path(grupo, stem)
        if cifrar:
            if key_id is None:
                key_id = self.registry.key_id(grupo, stem)
            debe_cifrar = key_id is not None
        else:
            debe_cifrar = False

        if not ev_path:
            ev_path = self.evento_path(grupo, stem, ext=".gpg" if debe_cifrar else ".md")
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
        """Cifra un archivo plano con GPG usando la(s) clave(s) indicada(s)."""
        keys = [k.strip() for k in key_id.split(",") if k.strip()] if "," in key_id else [key_id]
        out = output_path if output_path.suffix == ".gpg" else output_path.with_suffix(output_path.suffix + ".gpg")
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
        """Descifra un archivo .gpg a un archivo temporal."""
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

# ============================================================================
# INTERFAZ (con caché de abreviaturas dentro de byte.json)
# ============================================================================

class ByteInterface:
    def __init__(self, storage: ByteStorage):
        """Inicializa la app con la configuración, crea storage e interfaz."""
        self.storage = storage
        self.registry = storage.registry
        self._cache_abbr: Dict[Tuple[str, int], Dict[str, str]] = {}
        self._load_abbr_from_registry()

    def _load_abbr_from_registry(self) -> None:
        """Carga la caché de abreviaturas desde el registro (byte.json)."""
        self._persistent_cache = self.registry.get_abbr_cache()

    def _save_abbr_to_registry(self) -> None:
        """Guarda la caché de abreviaturas en el registro."""
        self.registry.set_abbr_cache(self._persistent_cache)

    def _get_abreviaturas_from_persistent(self, grupo: str, long: int) -> Optional[Dict[str, str]]:
        """Obtiene abreviaturas desde caché persistente si está actualizada."""
        gp = self.storage.base / grupo
        if not gp.is_dir():
            return None
        current_mtime = gp.stat().st_mtime
        cached = self._persistent_cache.get(grupo)
        if cached and abs(cached["mtime"] - current_mtime) < 0.001:
            # La caché es válida (usamos siempre long=2 para eventos)
            return cached["abbr"]
        return None

    def _save_abbr_to_persistent(self, grupo: str, abbr: Dict[str, str]) -> None:
        """Guarda las abreviaturas de un grupo en la caché persistente."""
        gp = self.storage.base / grupo
        if not gp.is_dir():
            return
        current_mtime = gp.stat().st_mtime
        self._persistent_cache[grupo] = {
            "mtime": current_mtime,
            "abbr": abbr
        }
        self._save_abbr_to_registry()

    def update_all_abbreviations(self) -> None:
        """Fuerza la actualización de la caché de abreviaturas para todos los grupos."""
        self._persistent_cache = {}
        grupos = self.storage.get_grupos()
        for grupo in grupos:
            evs = self.storage.get_eventos(grupo)
            abbr = self.calc_abreviaturas(evs, 2)
            self._save_abbr_to_persistent(grupo, abbr)
        self._cache_abbr.clear()

    def _get_abreviaturas(self, grupo: str, long: int) -> Dict[str, str]:
        """Retorna abreviaturas para un grupo, usando caché persistente y en memoria."""
        if long != 2:
            # Fallback al cálculo directo
            evs = self.storage.get_eventos(grupo)
            return self.calc_abreviaturas(evs, long)

        key = (grupo, long)
        # Primero, intentar desde caché persistente
        persistent_abbr = self._get_abreviaturas_from_persistent(grupo, long)
        if persistent_abbr is not None:
            self._cache_abbr[key] = persistent_abbr
            return persistent_abbr

        # No hay caché válida, calcular
        evs = self.storage.get_eventos(grupo)
        abbr = self.calc_abreviaturas(evs, long)
        self._save_abbr_to_persistent(grupo, abbr)
        self._cache_abbr[key] = abbr
        return abbr

    def invalidar_cache_abreviaturas(self, grupo: Optional[str] = None) -> None:
        """Invalida la caché de abreviaturas de un grupo o de todos."""
        if grupo is None:
            self._persistent_cache.clear()
            self._cache_abbr.clear()
        else:
            keys_to_del = [k for k in self._cache_abbr if k[0] == grupo]
            for k in keys_to_del:
                del self._cache_abbr[k]
            self._persistent_cache.pop(grupo, None)
        self._save_abbr_to_registry()

    def leer(self, prompt: str) -> str:
        """Lee una línea de entrada del usuario, maneja Ctrl+C / Ctrl+D."""
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C('tree')}(Interrumpido){C('rst')}")
            sys.exit(0)

    def calc_abreviaturas(self, lista: List[str], long: int) -> Dict[str, str]:
        """Calcula abreviaturas únicas de longitud variable para cada elemento."""
        max_long = max(long, 5)
        resultado = {}
        ordenados = sorted(lista, key=len)
        for item in ordenados:
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
            if encontrado:
                resultado[item] = encontrado
            else:
                resultado[item] = item[:max_long]
        final = {item: resultado[item] for item in lista}
        return final

    def render_ruta(self, grupo: str, stem: str) -> str:
        """Renderiza una ruta grupo/evento con abreviaturas resaltadas."""
        g_abbr = self.calc_abreviaturas(self.storage.get_grupos(), 3)
        g_render = _resaltar(grupo, g_abbr.get(grupo), 3, C("group"), C("bold"))
        evs = self.storage.get_eventos(grupo)
        if stem not in evs:
            evs.append(stem)
        e_abbr = self._get_abreviaturas(grupo, 2)
        e_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))
        return f"{g_render}{C('tree')}/{C('rst')}{e_render}"

    def _fmt_origin(self, path_str: str) -> str:
        """Abrevia una ruta larga mostrando solo los dos últimos componentes."""
        p = Path(path_str)
        parts = p.parts
        if len(parts) >= 2:
            return f"…/{parts[-2]}/{parts[-1]}"
        return path_str

    def _get_badges_compactos(self, grupo: str, stem: str) -> str:
        """Devuelve los 4 caracteres de estado (g,i,enlace,b) para modo columnas."""
        c = []
        if self.storage.registry.is_protected(grupo, stem):
            c.append(f"{C('warn')}g{C('rst')}")
        else:
            c.append(" ")
        if self.storage.registry.has_info(grupo, stem):
            c.append(f"{C('warn')}i{C('rst')}")
        else:
            c.append(" ")
        origins = self.storage.registry.get_origins(grupo, stem)
        if origins:
            first = origins[0]
            disponible = Path(first["path"]).is_file()
            if not disponible:
                c.append(f"{C('minus')}x{C('rst')}")
            elif first["copy"]:
                c.append(f"{C('link')}c{C('rst')}")
            else:
                c.append(f"{C('link')}l{C('rst')}")
        else:
            c.append(" ")
        if not self.storage.registry.is_protected(grupo, stem):
            if self.storage.registry.get_type(grupo, stem) == "binary":
                c.append(f"{C('date')}b{C('rst')}")
            else:
                c.append(" ")
        else:
            c.append(" ")
        return "".join(c)

    def _render_evento_linea(self, grupo: str, stem: str, ev_path: Optional[Path],
                             e_abbr: Dict[str, str], compact: bool = False) -> str:
        """Renderiza una línea del árbol para un evento."""
        event_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))
        ext_str = ""
        if ev_path and ev_path.suffix.lower() != ".gpg":
            ext_str = f"{C('date')}{ev_path.suffix.lower()}{C('rst')}"
        display_name = event_render + ext_str

        if compact:
            badges = self._get_badges_compactos(grupo, stem)
            return f"{badges} {display_name}"
        else:
            badges = ""
            if self.storage.registry.is_protected(grupo, stem):
                badges += f" {C('warn')}g{C('rst')}"
            elif self.storage.registry.get_type(grupo, stem) == "binary":
                badges += f" {C('date')}b{C('rst')}"
            if self.storage.registry.has_info(grupo, stem):
                badges += f" {C('warn')}i{C('rst')}"
            origins = self.storage.registry.get_origins(grupo, stem)
            origins_str = ""
            if origins:
                parts = []
                for o in origins:
                    path = o["path"]
                    es_copia = o["copy"]
                    origen_fmt = self._fmt_origin(path)
                    disponible = Path(path).is_file()
                    if not disponible:
                        parts.append(f"{C('minus')}x{C('rst')} {C('date')}{origen_fmt}{C('rst')}")
                    elif es_copia:
                        parts.append(f"{C('date')}c → {origen_fmt}{C('rst')}")
                    else:
                        parts.append(f"{C('date')}→ {origen_fmt}{C('rst')}")
                origins_str = f" {C('date')}·{C('rst')} " + f"{C('date')}, {C('rst')}".join(parts)
            return f"{display_name}{badges}{origins_str}"

    def print_arbol_columnas(self, show_dates: bool = False) -> None:
        """Imprime el árbol en formato de columnas compactas."""
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return

        term_width = shutil.get_terminal_size().columns
        grupos_lista = grupos
        g_abbr_tmp = self.calc_abreviaturas(grupos_lista, 3)
        g_abbr = {g: g_abbr_tmp.get(g, g[:3] if len(g) >= 3 else g) for g in grupos_lista}

        def ancho_grupo(grupo: str, evs: List[str]) -> int:
            e_abbr = self._get_abreviaturas(grupo, 2)
            header = f"{_resaltar(grupo, g_abbr.get(grupo), 3, C('group'), C('bold'))} {C('date')}({len(evs)}){C('rst')}"
            max_ancho = len(strip_ansi(header))
            for stem in evs:
                ev_path = self.storage.get_evento_path(grupo, stem)
                linea = self._render_evento_linea(grupo, stem, ev_path, e_abbr, compact=True)
                max_ancho = max(max_ancho, len(strip_ansi(linea)))
            return max_ancho

        grupos_data = [(g, self.storage.get_eventos(g)) for g in grupos]
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
                col_w = ancho - 2
                e_abbr = self._get_abreviaturas(grupo, 2)
                header = f"{_resaltar(grupo, g_abbr.get(grupo), 3, C('group'), C('bold'))} {C('date')}({len(evs)}){C('rst')}"
                lineas = [header]
                for stem in evs:
                    ev_path = self.storage.get_evento_path(grupo, stem)
                    lineas.append(self._render_evento_linea(grupo, stem, ev_path, e_abbr, compact=True))
                columnas.append(lineas)

            max_filas = max(len(col) for col in columnas)
            for fi in range(max_filas):
                partes = []
                for ci, (col, ancho) in enumerate(zip(columnas, fila_anchos)):
                    col_w = ancho - 2
                    celda = col[fi] if fi < len(col) else ""
                    partes.append(pad_ansi(celda, col_w))
                print(sep.join(partes).rstrip())
            print()

    def print_arbol(self, grupos_filter: Optional[List[str]] = None, show_dates: bool = False,
                    column_mode: bool = False) -> None:
        """Imprime el árbol de notas (modo normal o columnas)."""
        if column_mode:
            self.print_arbol_columnas(show_dates)
            return
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return

        d = C("date")
        r = C("rst")
        g_abbr_tmp = self.calc_abreviaturas(grupos, 3)
        g_abbr = {g: g_abbr_tmp.get(g, g[:3] if len(g)>=3 else g) for g in grupos}

        for gi, grupo in enumerate(grupos):
            evs = self.storage.get_eventos(grupo)
            ev_count = len(evs)
            if gi > 0:
                print()
            grupo_render = _resaltar(grupo, g_abbr.get(grupo), 3, C("group"), C("bold"))
            print(f"{C('tree')}{grupo_render} {d}({ev_count}){r}")

            if not evs:
                continue

            e_abbr = self._get_abreviaturas(grupo, 2)
            for stem in evs:
                ev_path = self.storage.get_evento_path(grupo, stem)
                # Verificar duplicados y forzar recálculo si es necesario
                abbr_val = e_abbr.get(stem)
                if abbr_val and list(e_abbr.values()).count(abbr_val) > 1:
                    self.invalidar_cache_abreviaturas(grupo)
                    e_abbr = self._get_abreviaturas(grupo, 2)
                line = self._render_evento_linea(grupo, stem, ev_path, e_abbr, compact=False)
                if show_dates:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        line += f"  {d}{mt.strftime('%Y-%m-%d %H:%M')}{r}"
                print(f"  {line}")
        print()

    def pedir_grupo(self, label: str = "Grupo") -> str:
        """Solicita al usuario un nombre de grupo (puede ser abreviatura)."""
        grupos = self.storage.get_grupos()
        self.print_arbol()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in grupos:
                return res
            g_abbr = self.calc_abreviaturas(grupos, 3)
            res_lower = res.lower()
            for g, ab in g_abbr.items():
                if ab.lower() == res_lower:
                    return g
            res_norm = self.storage.normalize(res)
            for g in grupos:
                if self.storage.normalize(g) == res_norm:
                    return g
            return self.storage.titulo(res)

    def pedir_evento(self, grupo: str, label: str = "Evento") -> str:
        """Solicita al usuario un nombre de evento dentro de un grupo (puede ser abreviatura)."""
        evs = self.storage.get_eventos(grupo)
        if evs:
            e_abbr = self._get_abreviaturas(grupo, 2)
            print(f"\n  Eventos en {grupo}:")
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
            e_abbr = self._get_abreviaturas(grupo, 2)
            for e, ab in e_abbr.items():
                if ab == res:
                    return e
            res_norm = self.storage.normalize(res)
            for e in evs:
                if self.storage.normalize(e) == res_norm:
                    return e
            return res

# ============================================================================
# APLICACIÓN PRINCIPAL
# ============================================================================

class ByteApp:
    def __init__(self, config: Config):
        """Inicializa la app con la configuración, crea storage e interfaz."""
        self.config = config
        self.storage = ByteStorage(config.base)
        self.ui = ByteInterface(self.storage)

    # --- resolución de argumentos ---
    def find_grupo(self, token: str) -> Optional[str]:
        """Busca un grupo por nombre completo o abreviatura de 3 letras."""
        grupos = self.storage.get_grupos()
        if token in grupos:
            return token
        g_abbr = self.ui.calc_abreviaturas(grupos, 3)
        token_lower = token.lower()
        for g, ab in g_abbr.items():
            if ab.lower() == token_lower:
                return g
        token_norm = self.storage.normalize(token)
        for g in grupos:
            if self.storage.normalize(g) == token_norm:
                return g
        return None

    def parse_arg(self, arg: str) -> Tuple[Optional[str], Optional[str]]:
        """Parsea un argumento 'grupo/evento' o 'grupo.evento' y resuelve abreviaturas."""
        if not arg:
            return None, None
        # Acepta separador '/' o '.'
        m = re.match(r"^([^/.]+)[/.](.+)$", arg)
        if m:
            g_raw, ev_raw = m.group(1), m.group(2)
            grupo = self.find_grupo(g_raw) or self.storage.titulo(g_raw)
            if grupo is None:
                return None, ev_raw
            # Obtener el nombre base del evento (sin extensión)
            ev_base = Path(ev_raw).stem
            # 1. Buscar si ev_base es una abreviatura válida en este grupo
            e_abbr = self.ui._get_abreviaturas(grupo, 2)
            for ev, ab in e_abbr.items():
                if ab.lower() == ev_base.lower():
                    return grupo, ev
            # 2. Buscar si ev_base es un nombre real (normalizado)
            evs = self.storage.get_eventos(grupo)
            ev_norm = self.storage.normalize(ev_base)
            for ev in evs:
                if self.storage.normalize(ev) == ev_norm:
                    return grupo, ev
            # 3. Si no, devolver el grupo y el nombre base (podría ser un evento nuevo)
            return grupo, ev_base
        m = re.match(r"^([^/]+)/$", arg)
        if m:
            return self.find_grupo(m.group(1)) or self.storage.titulo(m.group(1)), None
        return None, arg

    def resolver_arg(self, arg: str) -> Tuple[Optional[str], Optional[str]]:
        """Resuelve un token a (grupo, evento) buscando en todos los grupos y usando abreviaturas."""
        g, e = self.parse_arg(arg)
        if g and e:
            return g, e
        token = e or g
        if not token:
            return None, None
        for grupo in self.storage.get_grupos():
            evs = self.storage.get_eventos(grupo)
            if token in evs:
                return grupo, token
            e_abbr = self.ui._get_abreviaturas(grupo, 2)
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


    # --- comandos ---
    def cmd_open(self, args: List[str]) -> None:
        """Comando por defecto: abre evento en editor o añade línea con timestamp."""
        if not sys.stdout.isatty() and len(args) == 1:
            token = args[0]
            grupo, stem = self.resolver_arg(token)
            if not grupo:
                stem = token
                grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
                if not grupo:
                    return
            if not stem:
                stem = self.ui.pedir_evento(grupo, "Evento")
                if not stem:
                    return
            contenido = self.storage.leer_evento(grupo, stem)
            if contenido is None:
                print(f"Evento no existe: {grupo}/{stem}", file=sys.stderr)
                sys.exit(1)
            sys.stdout.buffer.write(contenido)
            return

        if not args:
            grupo = self.ui.pedir_grupo()
            if not grupo:
                return
            stem = self.ui.pedir_evento(grupo)
            if not stem:
                return
            texto = None
        else:
            token = args[0]
            texto = " ".join(args[1:]) if len(args) > 1 else None
            grupo, stem = self.resolver_arg(token)
            if not grupo:
                stem = Path(token).stem if Path(token).suffix in EXT_TEXTO | {".gpg"} else token
                grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
                if not grupo:
                    return
            if not stem:
                stem = self.ui.pedir_evento(grupo, "Evento")
                if not stem:
                    return

        ev_path = self.storage.get_evento_path(grupo, stem)
        if ev_path is None:
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")

        # --- NUEVO: Añadir línea con timestamp (si hay texto) ---
        if texto and texto.strip():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            linea_con_fecha = f"{timestamp} {texto}\n"

            # Caso 1: el evento no existe
            if not ev_path.is_file():
                ev_path.parent.mkdir(parents=True, exist_ok=True)
                ev_path.write_text(linea_con_fecha, encoding="utf-8")
                self.storage.registry.set_type(grupo, stem, "text")
                print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea_con_fecha.strip()}")
                return

            # Caso 2: existe y es .gpg (cifrado)
            if ev_path.suffix.lower() == ".gpg":
                tipo = self.storage.registry.get_type(grupo, stem)
                if tipo == "binary":
                    print(f"{C('warn')}No se puede añadir texto a un archivo cifrado binario.{C('rst')}")
                    return
                if tipo != "text":
                    print(f"{C('warn')}El archivo cifrado no tiene tipo registrado como texto. Use 'byte check' para actualizar.{C('rst')}")
                    return
                try:
                    tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                except RuntimeError as e:
                    print(f"GPG error: {e}")
                    return
                try:
                    contenido_actual = tmp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    print(f"{C('warn')}El contenido descifrado no es texto UTF-8. No se puede añadir línea.{C('rst')}")
                    tmp.unlink()
                    return
                nuevo_contenido = contenido_actual + linea_con_fecha
                tmp.write_text(nuevo_contenido, encoding="utf-8")
                key_id = self.storage.registry.key_id(grupo, stem)
                if not key_id:
                    print(f"{C('warn')}No hay clave GPG registrada para este evento.{C('rst')}")
                    tmp.unlink()
                    return
                try:
                    self.storage._gpg_encrypt(tmp, key_id, ev_path)
                except Exception as e:
                    print(f"Error al cifrar: {e}")
                finally:
                    tmp.unlink()
                print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea_con_fecha.strip()}")
                return

            # Caso 3: existe y no es .gpg
            tipo_real = detectar_tipo_archivo(ev_path)
            if tipo_real == "binary":
                print(f"{C('warn')}No se puede añadir texto a un archivo binario.{C('rst')}")
                return
            # Es texto, añadir al final
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(linea_con_fecha)
            print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)} {C('tree')}│{C('rst')} {linea_con_fecha.strip()}")
            return
        # --- FIN del bloque de añadir línea con timestamp ---

        # --- A partir de aquí, comportamiento original cuando NO hay texto (abrir editor) ---
        # --- Manejo de archivos no cifrados (incluyendo binarios) ---
        if ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo = self.storage.registry.get_type(grupo, stem)
            if not tipo or tipo == "text":
                tipo_real = detectar_tipo_archivo(ev_path)
                if tipo_real != "text":
                    self.storage.registry.set_type(grupo, stem, tipo_real)
                    tipo = tipo_real
            if tipo == "binary":
                ruta_fmt = self.ui.render_ruta(grupo, stem)
                print(f"\n{C('date')}{ruta_fmt} es un archivo binario.{C('rst')}")
                op = self.ui.leer(f"Exportar (s/{C('date')}N{C('rst')}): ").lower()
                if op == "s":
                    destino = Path.cwd() / ev_path.name
                    shutil.copy2(ev_path, destino)
                    print(f"{C('plus')}✓ Exportado a {destino}{C('rst')}")
                else:
                    print(f"{C('date')}Cancelado{C('rst')}")
                return
            # Si es texto, continúa (se abrirá editor más abajo)

        ruta_fmt = self.ui.render_ruta(grupo, stem)
        es_nuevo = not ev_path.is_file()

        if ev_path.suffix.lower() == ".gpg":
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                if not self.storage.registry.get_type(grupo, stem):
                    tipo_real = detectar_tipo_archivo(tmp)
                    self.storage.registry.set_type(grupo, stem, tipo_real)
            except RuntimeError as e:
                print(f"GPG error: {e}")
                return
            # Si el tipo registrado es binario, preguntar exportar sin descifrar antes (ya está descifrado en tmp)
            if self.storage.registry.get_type(grupo, stem) == "binary":
                print(f"\n{C('date')}{ruta_fmt} es un archivo cifrado que contiene datos binarios (registrado).{C('rst')}")
                op = self.ui.leer(f"¿Descifrar y exportar? (s/{C('date')}N{C('rst')}): ").lower()
                if op == "s":
                    inner_ext = Path(ev_path.stem).suffix or ".bin"
                    default_name = f"{stem}{inner_ext}"
                    destino = Path.cwd() / default_name
                    shutil.copy2(tmp, destino)
                    print(f"{C('plus')}✓ Exportado a {destino}{C('rst')}")
                else:
                    print(f"{C('date')}Cancelado{C('rst')}")
                tmp.unlink()
                return
            # Si no hay tipo o es texto pero al detectar es binario (fallback)
            if detectar_tipo_archivo(tmp) == "binary":
                print(f"\n{C('date')}{ruta_fmt} es un archivo cifrado que contiene datos binarios.{C('rst')}")
                op = self.ui.leer(f"¿Descifrar y exportar? (s/{C('date')}N{C('rst')}): ").lower()
                if op == "s":
                    inner_ext = Path(ev_path.stem).suffix or ".bin"
                    default_name = f"{stem}{inner_ext}"
                    destino = Path.cwd() / default_name
                    shutil.copy2(tmp, destino)
                    print(f"{C('plus')}✓ Exportado a {destino}{C('rst')}")
                else:
                    print(f"{C('date')}Cancelado{C('rst')}")
                tmp.unlink()
                return
            # Es texto cifrado: abrir editor
            mtime_antes = tmp.stat().st_mtime
            os.system(f'{self.config.editor} "{tmp}"')
            if tmp.stat().st_mtime != mtime_antes:
                key_id = self.storage.registry.key_id(grupo, stem)
                contenido = tmp.read_bytes()
                try:
                    self.storage.escribir_evento(grupo, stem, contenido, key_id=key_id, cifrar=True)
                    print(f"{C('count')}~ {ruta_fmt}{C('rst')} (cifrado)")
                except Exception as e:
                    print(f"Error al guardar: {e}")
            else:
                tmp.unlink()
                print(f"{C('date')}  (sin cambios){C('rst')}")
            return

        # Archivo normal (no cifrado)
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

    def cmd_link(self, args: List[str]) -> None:
        """Registra un archivo externo como origen (siempre copia, nunca hardlink)."""
        if not args:
            archivo = self.ui.leer("Archivo a enlazar: ")
            if not archivo:
                return
            grupo_hint = None
            stem_override = None
        elif len(args) == 1:
            archivo = args[0]
            grupo_hint = None
            stem_override = None
        else:
            archivo = args[0]
            segundo = args[1]
            # Intentar resolver como grupo/evento (con / o .)
            g_res, e_res = self.parse_arg(segundo)
            if g_res is not None and e_res is not None:
                grupo_hint = g_res
                stem_override = e_res
            else:
                # Si no se puede resolver, tratar como nombre directo
                if "/" in segundo:
                    partes = segundo.split("/", 1)
                    grupo_hint = self.find_grupo(partes[0]) or self.storage.titulo(partes[0])
                    stem_override = partes[1] if partes[1] else None
                else:
                    grupo_hint = None
                    stem_override = segundo

        src = Path(archivo).expanduser().resolve()
        src_exists = src.is_file()
        src_parent = src.parent

        if grupo_hint:
            grupo = grupo_hint
        else:
            self.ui.print_arbol()
            grupo = self.ui.pedir_grupo("Grupo para el evento")
            if not grupo:
                return

        # Procesar stem_override (puede ser nombre de evento o abreviatura)
        if stem_override:
            # Intentar resolver como evento dentro del grupo (abreviatura o nombre)
            evs = self.storage.get_eventos(grupo)
            e_abbr = self.ui._get_abreviaturas(grupo, 2)
            # Buscar si es abreviatura
            encontrado = None
            for ev, ab in e_abbr.items():
                if ab == stem_override.lower():
                    encontrado = ev
                    break
            if encontrado:
                stem = encontrado
                ext = ""  # Se recalculará con src.suffix
            else:
                # Puede ser nombre con extensión
                p_override = Path(stem_override)
                if p_override.suffix in EXT_TEXTO | {".gpg"}:
                    stem = p_override.stem
                    ext = p_override.suffix
                else:
                    stem = stem_override
                    if src.suffix and src.suffix.lower() in EXT_TEXTO:
                        ext = src.suffix.lower()
                    else:
                        ext = ""
        else:
            stem = src.stem
            if src.suffix and src.suffix.lower() in EXT_TEXTO:
                ext = src.suffix.lower()
            else:
                ext = src.suffix if src.suffix else ""

        ev_path = self.storage.get_evento_path(grupo, stem)
        ev_exists = ev_path is not None and ev_path.is_file()
        if not ev_exists:
            if not ext:
                tipo = detectar_tipo_archivo(src)
                if tipo == "text":
                    ext = ".md"
                else:
                    ext = src.suffix if src.suffix else ".bin"
            ev_path = self.storage.evento_path(grupo, stem, ext=ext)

        if not src_exists and ev_exists:
            print(f"{C('date')}  El archivo externo no existe, se creará a partir del vault.{C('rst')}")
            if self.ui.leer(f"  Crear {src} desde {grupo}/{stem}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                return
            src_parent.mkdir(parents=True, exist_ok=True)
            contenido = self.storage.leer_evento(grupo, stem)
            if contenido is None:
                print("  Error al leer el evento")
                return
            src.write_bytes(contenido)
            self.storage.registry.add_origin(grupo, stem, src, es_copia=True)   # siempre copia
            tipo = self.storage.registry.get_type(grupo, stem)
            if tipo == "text":
                tipo = detectar_tipo_archivo(ev_path)
                self.storage.registry.set_type(grupo, stem, tipo)
            print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(str(src))} (copia desde vault){C('rst')}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and not ev_exists:
            print(f"{C('date')}  El evento {grupo}/{stem} no existe, se creará desde el archivo externo.{C('rst')}")
            if self.ui.leer(f"  Crear {grupo}/{stem} desde {src}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                return
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            # Siempre copia, nunca hardlink
            shutil.copy2(src, ev_path)
            metodo = "copia"
            es_copia = True
            self.storage.registry.add_origin(grupo, stem, src, es_copia=es_copia)
            tipo = detectar_tipo_archivo(src)
            self.storage.registry.set_type(grupo, stem, tipo)
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            print(f"{C('plus')}+ {ruta_fmt}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(str(src))}  ({metodo}){C('rst')}")
            self.storage._invalidar_cache_grupo(grupo)
            self.ui.invalidar_cache_abreviaturas(grupo)
            return

        if src_exists and ev_exists:
            origins = self.storage.registry.get_origins(grupo, stem)
            if any(o["path"] == str(src) for o in origins):
                print(f"{C('date')}  {self.ui.render_ruta(grupo, stem)} ya tiene este origen: {src}{C('rst')}")
                return

            print(f"{C('warn')}  Conflicto: ambos archivos existen y no están vinculados.{C('rst')}")
            print(f"    Vault: {ev_path}")
            print(f"    Externo: {src}")
            op = self.ui.leer("  ¿[v]ault → origen, [o]rigen → vault, [a]ñadir como otro origen, [n]ada? (v/o/a/n): ").lower()
            if op == 'v':
                if self.ui.leer(f"  ¿Sobrescribir {src} con el contenido del vault? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                    return
                contenido = self.storage.leer_evento(grupo, stem)
                if contenido is not None:
                    src.write_bytes(contenido)
                    self.storage.registry.add_origin(grupo, stem, src, es_copia=True)
                    print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} → {src} (actualizado){C('rst')}")
                else:
                    print("  Error al leer el vault")
            elif op == 'o':
                if self.ui.leer(f"  ¿Sobrescribir {ev_path} con el contenido de {src}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                    return
                contenido = src.read_bytes()
                key_id = self.storage.registry.key_id(grupo, stem) if self.storage.registry.is_protected(grupo, stem) else None
                self.storage.escribir_evento(grupo, stem, contenido, key_id=key_id, cifrar=bool(key_id))
                self.storage.registry.add_origin(grupo, stem, src, es_copia=False)   # aquí origen → vault, no es copia
                tipo = detectar_tipo_archivo(src)
                self.storage.registry.set_type(grupo, stem, tipo)
                print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} actualizado desde {src}{C('rst')}")
            elif op == 'a':
                self.storage.registry.add_origin(grupo, stem, src, es_copia=True)
                print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                      f"  {C('link')}→ {self.ui._fmt_origin(str(src))} (nuevo origen){C('rst')}")
            else:
                print(f"{C('date')}  Cancelado.{C('rst')}")
            return

        print(f"{C('minus')}  Ni el archivo externo ni el evento existen. Nada que hacer.{C('rst')}")
        
    def cmd_unlink(self, args: List[str]) -> None:
        """Elimina el registro de un origen (archivos intactos)."""
        if not args:
            enlazados = []
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    origins = self.storage.registry.get_origins(g, stem)
                    if origins:
                        enlazados.append((g, stem, origins))
            if not enlazados:
                print(f"{C('date')}  No hay enlaces registrados.{C('rst')}")
                return
            for g, stem, origins in enlazados:
                ruta_fmt = self.ui.render_ruta(g, stem)
                print(f"  {ruta_fmt}")
                for idx, o in enumerate(origins):
                    marca = "c →" if o["copy"] else "→"
                    print(f"      [{idx+1}] {marca} {self.ui._fmt_origin(o['path'])}")
            entrada = self.ui.leer("Evento o número de origen a desenlazar (ej. '2' o 'Grupo/evento'): ")
            if not entrada:
                return
        else:
            entrada = args[0]

        if entrada.isdigit():
            print("  Para eliminar un origen específico, usa el nombre completo del evento.")
            return

        grupo, stem = self.resolver_arg(entrada)
        if not grupo or not stem:
            print(f"No encontrado: '{entrada}'")
            return

        origins = self.storage.registry.get_origins(grupo, stem)
        if not origins:
            print(f"  {self.ui.render_ruta(grupo, stem)}  {C('date')}(sin enlaces registrados){C('rst')}")
            return

        if len(origins) == 1:
            origen = origins[0]["path"]
            if self.ui.leer(f"  ¿Desenlazar {self.ui.render_ruta(grupo, stem)} de {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                self.storage.registry.remove_origin(grupo, stem, Path(origen))
                print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}desenlazado{C('rst')}")
        else:
            print(f"  Múltiples orígenes para {self.ui.render_ruta(grupo, stem)}:")
            for idx, o in enumerate(origins):
                print(f"    [{idx+1}] {'c →' if o['copy'] else '→'} {self.ui._fmt_origin(o['path'])}")
            op = self.ui.leer("  Número a desenlazar, 't' para todos, 'c' para cancelar: ")
            if op == 'c':
                return
            if op == 't':
                if self.ui.leer(f"  ¿Eliminar todos los enlaces de {grupo}/{stem}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                    self.storage.registry.remove_all_origins(grupo, stem)
                    print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}todos los enlaces eliminados{C('rst')}")
                return
            if op.isdigit():
                idx = int(op) - 1
                if 0 <= idx < len(origins):
                    origen = origins[idx]["path"]
                    if self.ui.leer(f"  ¿Desenlazar {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                        self.storage.registry.remove_origin(grupo, stem, Path(origen))
                        print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}desenlazado{C('rst')}")
                else:
                    print("  Número inválido.")

    def cmd_del(self, args: List[str]) -> None:
        """Envía un grupo o evento a la papelera (.trash)."""
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o evento: ")
        if not entrada:
            print("Cancelado.")
            return
        grupo, stem = self.resolver_arg(entrada)

        if grupo and not stem:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir():
                print(f"No existe el grupo '{grupo}'")
                return
            self.ui.print_arbol([grupo])
            if self.ui.leer(f"Enviar al trash '{grupo}/'? (s/{C('date')}N{C('rst')}): ") == "s":
                for ev in self.storage.get_eventos(grupo):
                    p = self.storage.get_evento_path(grupo, ev)
                    if p:
                        self.storage.registry.remove_all_origins(grupo, ev)
                        self.storage.registry.remove_info(grupo, ev)
                        self.storage.registry.unmark_gpg(grupo, ev)
                self.storage.trash(gp)
                print(f"Enviado al trash: {grupo}/")
        else:
            if not grupo or not stem:
                print(f"No encontrado: '{entrada}'")
                return
            ev_path = self.storage.get_evento_path(grupo, stem)
            if not ev_path or not ev_path.is_file():
                print(f"No existe {grupo}/{stem}")
                return
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            if self.ui.leer(f"Enviar al trash {grupo}/{ev_path.name}? (s/{C('date')}N{C('rst')}): ") == "s":
                self.storage.registry.remove_all_origins(grupo, stem)
                self.storage.registry.remove_info(grupo, stem)
                self.storage.registry.unmark_gpg(grupo, stem)
                self.storage.trash(ev_path)
                print(f"{C('minus')}- {ruta_fmt}{C('rst')}")
        self.storage.limpiar_vacios()

    def _renombrar(self, grupo: str, origen: str, destino: str) -> None:
        """Renombra un evento dentro del mismo grupo."""
        p_src = self.storage.get_evento_path(grupo, origen)
        if not p_src:
            print(f"No existe: {grupo}/{origen}")
            return
        ext = p_src.suffix
        p_dest = self.storage.evento_path(grupo, destino, ext=ext)
        if p_dest.is_file():
            print(f"Ya existe {grupo}/{destino}{ext}")
            resp = self.ui.leer(f"¿Sobrescribir? (s/{C('date')}N{C('rst')}): ").lower()
            if resp != 's':
                return
            self.storage.trash(p_dest)
        elif p_dest.exists():
            print(f"El destino {p_dest} existe y no es un archivo regular. No se puede renombrar.")
            return
        shutil.move(p_src, p_dest)
        self.storage.registry.rename_links(grupo, origen, grupo, destino)
        self.storage.registry.rename_info(grupo, origen, grupo, destino)
        if self.storage.registry.is_protected(grupo, origen):
            key_id = self.storage.registry.key_id(grupo, origen)
            self.storage.registry.mark_gpg(grupo, destino, key_id)
            self.storage.registry.unmark_gpg(grupo, origen)
        self.storage._invalidar_cache_grupo(grupo)
        self.ui.invalidar_cache_abreviaturas(grupo)
        print(f"{C('plus')}✓ Renombrado: {grupo}/{origen} → {grupo}/{destino}{C('rst')}")

    def _mover(self, g_src: str, e_src: str, g_dest: str, e_dest: str) -> None:
        """Mueve un evento a otro grupo (posiblemente renombrando)."""
        p_src = self.storage.get_evento_path(g_src, e_src)
        if not p_src or not p_src.is_file():
            print(f"No existe: {g_src}/{e_src}")
            return
        p_dest = self.storage.evento_path(g_dest, e_dest, ext=p_src.suffix)
        if p_dest.is_file():
            print(f"Ya existe {g_dest}/{e_dest} → fusionando.")
            self._fusionar(g_dest, e_dest, g_src, e_src)
            return
        p_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(p_src, p_dest)
        origins = self.storage.registry.get_origins(g_src, e_src)
        for o in origins:
            self.storage.registry.add_origin(g_dest, e_dest, Path(o["path"]), o["copy"])
        self.storage.registry.remove_all_origins(g_src, e_src)
        info_txt = self.storage.registry.get_info(g_src, e_src)
        if info_txt:
            self.storage.registry.set_info(g_dest, e_dest, info_txt)
            self.storage.registry.remove_info(g_src, e_src)
        tipo = self.storage.registry.get_type(g_src, e_src)
        if tipo:
            self.storage.registry.set_type(g_dest, e_dest, tipo)
        if self.storage.registry.is_protected(g_src, e_src):
            key_id = self.storage.registry.key_id(g_src, e_src)
            self.storage.registry.mark_gpg(g_dest, e_dest, key_id)
            self.storage.registry.unmark_gpg(g_src, e_src)
        self.storage._invalidar_cache_grupo(g_src)
        self.storage._invalidar_cache_grupo(g_dest)
        self.ui.invalidar_cache_abreviaturas(g_src)
        self.ui.invalidar_cache_abreviaturas(g_dest)
        self.storage.limpiar_vacios()
        r_src = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{C('plus')}✓ Movido: {r_src} ➔ {r_dest}{C('rst')}")

    def _fusionar(self, g_dest: str, e_dest: str, g_src: str, e_src: str) -> None:
        """Fusiona el contenido de un evento en otro (con separador ---)."""
        if g_src == g_dest and e_src == e_dest:
            print(f"{C('warn')}No se puede fusionar un evento consigo mismo.{C('rst')}")
            return
        p_src = self.storage.get_evento_path(g_src, e_src)
        p_dest = self.storage.get_evento_path(g_dest, e_dest)
        if not p_src or not p_src.is_file():
            print(f"No existe: {g_src}/{e_src}")
            return
        p_dest_real = p_dest if p_dest else self.storage.evento_path(g_dest, e_dest)
        p_dest_real.parent.mkdir(parents=True, exist_ok=True)
        contenido_src = p_src.read_bytes()
        if p_dest_real.is_file():
            contenido_dest = p_dest_real.read_bytes()
            sep = b"\n\n---\n\n"
            if contenido_dest.strip():
                p_dest_real.write_bytes(contenido_dest + sep + contenido_src)
            else:
                p_dest_real.write_bytes(contenido_src)
        else:
            p_dest_real.write_bytes(contenido_src)
        src_origins = self.storage.registry.get_origins(g_src, e_src)
        for o in src_origins:
            self.storage.registry.add_origin(g_dest, e_dest, Path(o["path"]), o["copy"])
        self.storage.registry.remove_all_origins(g_src, e_src)
        if not self.storage.registry.get_info(g_dest, e_dest):
            self.storage.registry.rename_info(g_src, e_src, g_dest, e_dest)
        else:
            self.storage.registry.remove_info(g_src, e_src)
        if self.storage.registry.is_protected(g_src, e_src):
            key_id = self.storage.registry.key_id(g_src, e_src)
            if not self.storage.registry.is_protected(g_dest, e_dest):
                self.storage.registry.mark_gpg(g_dest, e_dest, key_id)
            self.storage.registry.unmark_gpg(g_src, e_src)
        p_src.unlink()
        self.storage._invalidar_cache_grupo(g_src)
        self.storage._invalidar_cache_grupo(g_dest)
        self.ui.invalidar_cache_abreviaturas(g_src)
        self.ui.invalidar_cache_abreviaturas(g_dest)
        self.storage.limpiar_vacios()
        r_src = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{C('plus')}✓ Fusionado: {r_src} ➔ {r_dest}{C('rst')}")

    def cmd_mv(self, args: List[str]) -> None:
        """Mueve o fusiona eventos (comando --mv)."""
        if not args:
            self.ui.print_arbol()
            opcion = self.ui.leer("¿[m]over o [f]usionar? (m/f): ").lower()
            if opcion == "f":
                g_dest = self.ui.pedir_grupo("Grupo destino")
                e_dest = self.ui.pedir_evento(g_dest, "Evento destino")
                g_src = self.ui.pedir_grupo("Grupo fuente")
                e_src = self.ui.pedir_evento(g_src, "Evento fuente")
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                g_src = self.ui.pedir_grupo("Grupo origen")
                e_src = self.ui.pedir_evento(g_src, "Evento origen")
                g_dest = self.ui.pedir_grupo("Grupo destino")
                nuevo = self.ui.leer(f"Nuevo nombre (Enter = '{e_src}'): ") or e_src
                if g_src == g_dest and self.storage.normalize(e_src) == self.storage.normalize(nuevo) and e_src != nuevo:
                    self._renombrar(g_src, e_src, nuevo)
                else:
                    self._mover(g_src, e_src, g_dest, nuevo)
            return

        if len(args) == 1:
            print("Uso: byte --mv [origen] [destino]")
            return

        origen_arg = args[0]
        destino_arg = args[1]

        g_origen, e_origen = self.resolver_arg(origen_arg)
        if not g_origen or not e_origen:
            print(f"Origen no encontrado: '{origen_arg}'")
            return

        if destino_arg.endswith("/"):
            g_dest = self.find_grupo(destino_arg.rstrip("/")) or self.storage.titulo(destino_arg.rstrip("/"))
            self._mover(g_origen, e_origen, g_dest, e_origen)
            return

        if "/" in destino_arg:
            g_dest, e_dest = self.resolver_arg(destino_arg)
            if g_dest is None:
                g_dest = self.storage.titulo(destino_arg.split("/")[0])
                e_dest = destino_arg.split("/")[1]
            else:
                e_dest = Path(destino_arg).stem
            self._mover(g_origen, e_origen, g_dest, e_dest)
            return

        g_dest = g_origen
        e_dest = Path(destino_arg).stem
        if self.storage.normalize(e_dest) == self.storage.normalize(e_origen) and e_dest != e_origen:
            self._renombrar(g_origen, e_origen, e_dest)
            return
        p_dest = self.storage.get_evento_path(g_dest, e_dest)
        if p_dest and p_dest.is_file():
            print(f"Ya existe {g_dest}/{e_dest} → fusionando.")
            self._fusionar(g_dest, e_dest, g_origen, e_origen)
        else:
            self._mover(g_origen, e_origen, g_dest, e_dest)

    def cmd_gpg(self, args: List[str]) -> None:
        """Cifra un evento con GPG usando la clave primaria/secundarias."""
        if not shutil.which("gpg"):
            print("gpg no está disponible en el sistema.")
            return

        if not args:
            self.ui.print_arbol()
            entrada = self.ui.leer("Evento: ")
            if not entrada:
                return
            extra_keys = []
        else:
            entrada = args[0]
            extra_keys = args[1:]

        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem:
                return

        ev_path = self.storage.get_evento_path(grupo, stem)
        ruta_fmt = self.ui.render_ruta(grupo, stem)
        ya_cifrado = ev_path and ev_path.suffix.lower() == ".gpg"
        d = C("date")
        r = C("rst")
        w = C("warn")

        if ya_cifrado:
            key_actual = self.storage.registry.key_id(grupo, stem) or self.config.gpg_key
            actuales = [k for k in (key_actual or "").split(",") if k]
            if actuales:
                print(f"  {w}g{r} destinatarios actuales:")
                for k in actuales:
                    es_prim = (k == self.config.gpg_key)
                    etiq = f"{w}primaria{r}" if es_prim else f"{d}secundaria{r}"
                    print(f"    {d}{k}{r}  {etiq}")
            nuevas = list(extra_keys)
            while True:
                resp = self.ui.leer(f"  Añadir llave secundaria {d}(Enter para terminar){r}: ")
                if not resp:
                    break
                nuevas.append(resp)
            if not nuevas:
                print(f"{d}  Sin cambios.{r}")
                return
            print(f"  {d}Nota: las llaves añadidas son secundarias — solo reciben el cifrado.{r}")
            todos_keys = list(actuales)
            for k in nuevas:
                if k not in todos_keys:
                    todos_keys.append(k)
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                print(f"GPG error al descifrar: {e}")
                return
            inner_ext = Path(ev_path.stem).suffix or ".md"
            inner = ev_path.parent / f"{stem}{inner_ext}"
            shutil.move(tmp, inner)
            ev_path.unlink()
            r_args = []
            for k in todos_keys:
                r_args += ["-r", k]
            out = Path(str(inner) + ".gpg")
            res = subprocess.run(
                ["gpg", "--yes", "--batch", "--trust-model", "always"] + r_args +
                ["-o", str(out), "-e", str(inner)], capture_output=True)
            inner.unlink()
            if res.returncode != 0:
                print(f"GPG error al re-cifrar: {res.stderr.decode()}")
                return
            self.storage.registry.mark_gpg(grupo, stem, ",".join(todos_keys))
            print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} → {'  '.join(todos_keys)}")
            return

        if not self.config.gpg_key:
            print(f"{w}  Sin llave primaria configurada.{r}")
            print(f"  Configúrala con {d}byte x{r} o añade {d}gpg_key = 'tu@correo'{r} en byte.toml")
            return
        if extra_keys:
            print(f"  {d}Nota: para cifrar por primera vez se usa la configuración de byte.toml.{r}")
            print(f"  {d}Para añadir destinatarios a un archivo ya cifrado, vuelve a ejecutar byte g.{r}")

        if ev_path and ev_path.is_file() and ev_path.suffix.lower() != ".gpg":
            tipo_actual = self.storage.registry.get_type(grupo, stem)
            if not tipo_actual or tipo_actual == "text":
                tipo_real = detectar_tipo_archivo(ev_path)
                if tipo_real != "text":
                    self.storage.registry.set_type(grupo, stem, tipo_real)

        all_keys = [self.config.gpg_key] + [k for k in self.config.gpg_keys_secondary if k != self.config.gpg_key]

        if not ev_path or not ev_path.is_file():
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            ev_path.touch()
            self.storage.registry.set_type(grupo, stem, "text")

        r_args = []
        for k in all_keys:
            r_args += ["-r", k]
        out = Path(str(ev_path) + ".gpg")
        res = subprocess.run(
            ["gpg", "--yes", "--batch", "--trust-model", "always"] + r_args +
            ["-o", str(out), "-e", str(ev_path)], capture_output=True)
        if res.returncode != 0:
            print(f"GPG error: {res.stderr.decode()}")
            return
        ev_path.unlink()

        self.storage.registry.mark_gpg(grupo, stem, ",".join(all_keys))
        origins = self.storage.registry.get_origins(grupo, stem)
        if origins:
            for o in origins:
                self.storage.registry.add_origin(grupo, stem, Path(o["path"]), es_copia=True)
        prim_fmt = f"{w}{self.config.gpg_key}{r}"
        sec_fmt = ("  " + "  ".join(f"{d}{k}{r}" for k in all_keys[1:])) if all_keys[1:] else ""
        print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} {prim_fmt}{sec_fmt}")

    def cmd_nogpg(self, args: List[str]) -> None:
        """Descifra un evento y elimina la protección GPG."""
        if not shutil.which("gpg"):
            print("gpg no está disponible en el sistema.")
            return
        if not args:
            self.ui.print_arbol()
            entrada = self.ui.leer("Evento a desproteger: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem = entrada
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem:
                return
        ev_path = self.storage.get_evento_path(grupo, stem)
        if not ev_path or ev_path.suffix.lower() != ".gpg":
            print(f"  {self.ui.render_ruta(grupo, stem)} no está cifrado.")
            return
        if self.ui.leer(f"  Descifrar y desproteger {grupo}/{stem}? (s/{C('date')}N{C('rst')}): ") != "s":
            print(f"{C('date')}  Cancelado{C('rst')}")
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
        tipo = detectar_tipo_archivo(clear_path)
        self.storage.registry.set_type(grupo, stem, tipo)
        self.storage._invalidar_cache_grupo(grupo)
        self.ui.invalidar_cache_abreviaturas(grupo)
        print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)}{C('rst')} (descifrado, sin protección GPG)")

    def cmd_check(self, args: List[str]) -> None:
        """Verifica tipos, enlaces, actualiza caché y sincroniza copias (bidireccional)."""
        c = C("header")
        d = C("date")
        r = C("rst")
        w = C("warn")

        print(f"\n{c}=== CONFIGURACIÓN ==={r}")
        if self.config.used_config_path:
            cfg_display = str(self.config.used_config_path).replace(str(Path.home()), "~")
            print(f"Archivo de configuración: {cfg_display}")
        else:
            print("Archivo de configuración: (ninguno, usando por defecto)")
        # Mostrar base con ~ si está en home
        base_display = str(self.storage.base).replace(str(Path.home()), "~")
        print(f"Directorio: {base_display}")
        print(f"Editor: {self.config.editor}")
        if self.config.gpg_key:
            print(f"Clave GPG primaria: {self.config.gpg_key}")
        else:
            print("Clave GPG primaria: (no configurada)")
        if self.config.gpg_keys_secondary:
            sec_list = ", ".join(self.config.gpg_keys_secondary)
            print(f"Claves GPG secundarias: {sec_list}")
        else:
            print("Claves GPG secundarias: (ninguna)")
        print()

        tipo_cambiado = False
        for g in self.storage.get_grupos():
            for stem in self.storage.get_eventos(g):
                ev_path = self.storage.get_evento_path(g, stem)
                if not ev_path:
                    continue
                if ev_path.suffix.lower() == ".gpg":
                    continue
                tipo_reg = self.storage.registry.get_type(g, stem)
                tipo_real = detectar_tipo_archivo(ev_path)
                if tipo_reg != tipo_real:
                    if not tipo_cambiado:
                        print(f"{c}Verificando tipos...{r}")
                        tipo_cambiado = True
                    print(f"  {self.ui.render_ruta(g, stem)}: tipo registrado '{tipo_reg}' pero es '{tipo_real}'")
                    if self.ui.leer(f"  ¿Actualizar tipo a '{tipo_real}'? (s/{C('date')}N{C('rst')}): ").lower() == "s":
                        self.storage.registry.set_type(g, stem, tipo_real)
                        print(f"    {C('plus')}✓ Actualizado{r}")
        if tipo_cambiado:
            print()

        candidatos = []
        for g in self.storage.get_grupos():
            for stem in self.storage.get_eventos(g):
                ev_path = self.storage.get_evento_path(g, stem)
                for o in self.storage.registry.get_origins(g, stem):
                    if not ev_path:
                        continue
                    candidatos.append((g, stem, ev_path, o["path"], o["copy"]))

        if not candidatos:
            if not tipo_cambiado:
                print(f"{d}No hay enlaces registrados.{r}")
            self.ui.update_all_abbreviations()
            print(f"{d}Caché de abreviaturas actualizada (sin enlaces).{r}")
            return

        def archivos_iguales(a: Path, b: Path) -> bool:
            if a.stat().st_size != b.stat().st_size:
                return False
            return calcular_md5(a) == calcular_md5(b)

        cambios_entrantes = []
        salientes = []

        for g, stem, ev_path, origin_str, es_copia in candidatos:
            src = Path(origin_str)
            if not src.is_file():
                print(f"{d}{g}/{stem} → origen no disponible: {self.ui._fmt_origin(origin_str)} (omitido){r}")
                continue

            if ev_path.suffix.lower() == ".gpg":
                contenido_ev = self.storage.leer_evento(g, stem)
                if contenido_ev is None:
                    print(f"{w}{g}/{stem} — no se pudo descifrar (GPG), omitido.{r}")
                    continue
                contenido_src = src.read_bytes()
                diff = contenido_ev != contenido_src
                es_gpg = True
            else:
                es_gpg = False
                diff = not archivos_iguales(ev_path, src)

            if diff:
                cambios_entrantes.append((g, stem, ev_path, src, es_gpg, es_copia))
            else:
                salientes.append((g, stem, ev_path, src, es_copia))

        # Procesar cambios donde el origen es más reciente o hay diferencias
        for g, stem, ev_path, src, es_gpg, es_copia in cambios_entrantes:
            if not es_copia:
                continue
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt = self.ui.render_ruta(g, stem)
            gpg_tag = f" {w}g{r}" if es_gpg else ""
            print(f"\n{C('bold')}{ruta_fmt}{r}{gpg_tag}"
                  f"  {C('link')}c → {origen_fmt}{r}"
                  f"  {d}(modificado){r}")
            # Mostrar fechas de modificación
            mtime_ev = datetime.fromtimestamp(ev_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            mtime_src = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {d}evento: {mtime_ev}  |  origen: {mtime_src}{r}")

            es_binario_origen = detectar_tipo_archivo(src) == "binary"
            es_binario_evento = not es_gpg and self.storage.registry.get_type(g, stem) == "binary"

            while True:
                if es_binario_origen or es_binario_evento:
                    res = self.ui.leer("  ¿[o] origen→evento, [e] evento→origen, [m]d5, [N]o? (o/e/m/N): ").lower()
                else:
                    res = self.ui.leer("  ¿[o] origen→evento, [e] evento→origen, [d]iff, [N]o? (o/e/d/N): ").lower()

                if res == "m" and (es_binario_origen or es_binario_evento):
                    md5_src = calcular_md5(src)
                    if es_gpg:
                        tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        md5_ev = calcular_md5(tmp)
                        tmp.unlink()
                    else:
                        md5_ev = calcular_md5(ev_path)
                    print(f"  MD5 origen:  {md5_src}")
                    print(f"  MD5 evento:  {md5_ev}")
                elif res == "d" and not es_binario_origen and not es_binario_evento:
                    if es_gpg:
                        tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        mostrar_diff(tmp, src)
                        tmp.unlink()
                    else:
                        mostrar_diff(ev_path, src)
                elif res == "o":
                    # Origen → evento
                    if es_gpg:
                        key_id = self.storage.registry.key_id(g, stem)
                        keys = [k.strip() for k in key_id.split(",") if k.strip()]
                        missing = []
                        for k in keys:
                            res_check = subprocess.run(
                                ["gpg", "--list-keys", "--with-colons", k],
                                capture_output=True, text=True
                            )
                            if res_check.returncode != 0 or "pub:" not in res_check.stdout:
                                missing.append(k)
                        if missing:
                            print(f"{C('warn')}  No se puede cifrar: faltan las claves públicas {', '.join(missing)}{C('rst')}")
                            break
                        inner_ext = Path(ev_path.stem).suffix or ".md"
                        inner = ev_path.parent / f"{stem}{inner_ext}"
                        shutil.copy2(src, inner)
                        ev_path.unlink()
                        self.storage._gpg_encrypt(inner, key_id, ev_path)
                        print(f"{C('plus')}  ✓ Actualizado evento desde origen (re-cifrado){r}")
                    else:
                        shutil.copy2(src, ev_path)
                        print(f"{C('plus')}  ✓ Evento actualizado desde origen{r}")
                    self.storage._invalidar_cache_grupo(g)
                    self.ui.invalidar_cache_abreviaturas(g)
                    tipo = detectar_tipo_archivo(ev_path)
                    self.storage.registry.set_type(g, stem, tipo)
                    break
                elif res == "e":
                    # Evento → origen
                    if es_gpg:
                        try:
                            tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        except RuntimeError as e:
                            print(f"GPG error al descifrar: {e}")
                            break
                        shutil.copy2(tmp, src)
                        tmp.unlink()
                        print(f"{C('plus')}  ✓ Origen actualizado desde evento (descifrado){r}")
                    else:
                        shutil.copy2(ev_path, src)
                        print(f"{C('plus')}  ✓ Origen actualizado desde evento{r}")
                    # No es necesario invalidar caché del vault
                    break
                else:
                    print(f"{d}  Omitido{r}")
                    break

        # Procesar cambios donde el evento (vault) es más reciente (o diferentes)
        for g, stem, ev_path, src, es_copia in salientes:
            if not es_copia:
                continue
            if ev_path.suffix.lower() == ".gpg":
                contenido_ev = self.storage.leer_evento(g, stem)
                if contenido_ev is None:
                    continue
                contenido_src = src.read_bytes()
                diff = contenido_ev != contenido_src
            else:
                diff = not archivos_iguales(ev_path, src)
            if not diff:
                continue
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt = self.ui.render_ruta(g, stem)
            print(f"\n{C('bold')}{ruta_fmt}{r}"
                  f"  {C('link')}c → {origen_fmt}{r}"
                  f"  {d}(modificado){r}")
            # Mostrar fechas de modificación
            mtime_ev = datetime.fromtimestamp(ev_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            mtime_src = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {d}evento: {mtime_ev}  |  origen: {mtime_src}{r}")

            es_binario_evento = self.storage.registry.get_type(g, stem) == "binary"

            while True:
                if es_binario_evento:
                    res = self.ui.leer("  ¿[o] origen→evento, [e] evento→origen, [m]d5, [N]o? (o/e/m/N): ").lower()
                else:
                    res = self.ui.leer("  ¿[o] origen→evento, [e] evento→origen, [d]iff, [N]o? (o/e/d/N): ").lower()

                if res == "m" and es_binario_evento:
                    md5_src = calcular_md5(src)
                    if ev_path.suffix.lower() == ".gpg":
                        tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        md5_ev = calcular_md5(tmp)
                        tmp.unlink()
                    else:
                        md5_ev = calcular_md5(ev_path)
                    print(f"  MD5 origen:  {md5_src}")
                    print(f"  MD5 evento:  {md5_ev}")
                elif res == "d" and not es_binario_evento:
                    mostrar_diff(src, ev_path)
                elif res == "o":
                    # Origen → evento (sobrescribe evento con origen)
                    if ev_path.suffix.lower() == ".gpg":
                        key_id = self.storage.registry.key_id(g, stem)
                        if not key_id:
                            print(f"{C('warn')}  No hay clave GPG registrada. No se puede re-cifrar.{r}")
                            break
                        inner_ext = Path(ev_path.stem).suffix or ".md"
                        inner = ev_path.parent / f"{stem}{inner_ext}"
                        shutil.copy2(src, inner)
                        ev_path.unlink()
                        self.storage._gpg_encrypt(inner, key_id, ev_path)
                        print(f"{C('plus')}  ✓ Evento actualizado desde origen (re-cifrado){r}")
                    else:
                        shutil.copy2(src, ev_path)
                        print(f"{C('plus')}  ✓ Evento actualizado desde origen{r}")
                    self.storage._invalidar_cache_grupo(g)
                    self.ui.invalidar_cache_abreviaturas(g)
                    tipo = detectar_tipo_archivo(ev_path)
                    self.storage.registry.set_type(g, stem, tipo)
                    break
                elif res == "e":
                    # Evento → origen
                    if ev_path.suffix.lower() == ".gpg":
                        try:
                            tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        except RuntimeError as e:
                            print(f"GPG error al descifrar: {e}")
                            break
                        shutil.copy2(tmp, src)
                        tmp.unlink()
                        print(f"{C('plus')}  ✓ Origen actualizado desde evento (descifrado){r}")
                    else:
                        shutil.copy2(ev_path, src)
                        print(f"{C('plus')}  ✓ Origen actualizado desde evento{r}")
                    break
                else:
                    print(f"{d}  Omitido{r}")
                    break

        print(f"{d}Revisión completada.{r}")
        self.ui.update_all_abbreviations()
        print(f"{d}Caché de abreviaturas actualizada.{r}")

    def cmd_info(self, args: List[str]) -> None:
        """Muestra o asigna una nota corta a un evento."""
        if not args:
            encontrado = False
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    txt = self.storage.registry.get_info(g, stem)
                    if txt:
                        ruta_fmt = self.ui.render_ruta(g, stem)
                        print(f"  {ruta_fmt}  {C('date')}{txt}{C('rst')}")
                        encontrado = True
            if not encontrado:
                print(f"{C('date')}  (ningún evento tiene info){C('rst')}")
            return

        primero = args[0]
        grupo, stem = self.resolver_arg(primero)

        if grupo is None:
            print(f"No encontrado: '{primero}'")
            return

        if len(args) >= 2 and stem is None:
            print("No se puede guardar información para un grupo completo. Especifique un evento.")
            return

        if stem is not None:
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            d = C("date")
            r = C("rst")
            w = C("warn")

            if len(args) >= 2:
                texto = " ".join(args[1:])
                self.storage.registry.set_info(grupo, stem, texto)
                print(f"{d}Nota guardada para {ruta_fmt}: {texto}{r}")
                return

            txt = self.storage.registry.get_info(grupo, stem)
            if txt:
                print(f"{d}{txt}{r}")

            info_lines = []
            if self.storage.registry.is_protected(grupo, stem):
                info_lines.append(f"{w}Cifrado: {self.storage.registry.key_id(grupo, stem)}{r}")
            tipo = self.storage.registry.get_type(grupo, stem)
            if tipo == "binary":
                info_lines.append(f"Tipo: binario")
            origins = self.storage.registry.get_origins(grupo, stem)
            if origins:
                for o in origins:
                    path = o["path"]
                    es_copia = o["copy"]
                    disp = Path(path).is_file()
                    marca = "c →" if es_copia else "→"
                    if not disp:
                        info_lines.append(f"  {marca} {d}{self.ui._fmt_origin(path)}{r} {w}(no disponible){r}")
                    else:
                        info_lines.append(f"  {marca} {d}{self.ui._fmt_origin(path)}{r}")

            if info_lines:
                if txt:
                    print()
                for line in info_lines:
                    print(line)
            return

        evs = self.storage.get_eventos(grupo)
        if not evs:
            print(f"{C('date')}El grupo {grupo} no tiene eventos.{C('rst')}")
            return
        print(f"\n{C('header')}Grupo: {grupo}{C('rst')}")
        for stem in evs:
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            txt = self.storage.registry.get_info(grupo, stem)
            nota = txt if txt else "(sin nota)"
            badges = self.ui._get_badges_compactos(grupo, stem)
            print(f"  {badges} {ruta_fmt}: {C('date')}{nota}{C('rst')}")

    def cmd_config(self, args: List[str]) -> None:
        """Configura interactivamente la base, editor, GPG y vista por columnas."""
        c = C("bold")
        r = C("rst")
        d = C("date")
        w = C("warn")

        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        if system_path.is_file():
            target = system_path
            disp = str(target).replace(str(Path.home()), "~")
            print(f"\n{c}BYTE — Configuración (sistema){r}")
        else:
            vault_dir = self.config.base / ".byte"
            vault_dir.mkdir(parents=True, exist_ok=True)
            target = vault_dir / "byte.toml"
            disp = str(target).replace(str(Path.home()), "~")
            print(f"\n{c}BYTE — Configuración (portable en vault){r}")

        print(f"Archivo: {disp}\n")

        col_actual = "sí" if self.config.columnas_default else "no"
        print(f"Vista por columnas por defecto: {C('date')}{col_actual}{C('rst')}\n")

        base_actual = str(self.config.base)
        resp = self.ui.leer(f"Directorio base [{base_actual}]: ")
        nueva_base = Path(resp).expanduser().resolve() if resp else self.config.base

        editor_actual = self.config.editor
        resp = self.ui.leer(f"Editor [{editor_actual}]: ")
        nuevo_editor = resp if resp else editor_actual

        gpg_actual = self.config.gpg_key or ""
        print(f"\n{w}Llave GPG primaria{r}  (cifra y descifra)")
        resp = self.ui.leer(f"[{gpg_actual or 'ninguna'}]: ")
        nueva_primaria = resp if resp else gpg_actual

        sec_actual = list(self.config.gpg_keys_secondary)
        print(f"\n{d}Llaves secundarias actuales{r}")
        if sec_actual:
            for k in sec_actual:
                print(f"[{k}]")
        else:
            print(f"{d}(ninguna){r}")

        nuevas_sec = []
        while True:
            resp = self.ui.leer(f"Nueva llave ({d}vacío termina, '-' borra todas{r}): ")
            if not resp:
                break
            if resp == "-":
                nuevas_sec = []
                print(f"{d}Todas las secundarias serán eliminadas.{r}")
                break
            if "@" in resp and "." in resp.split("@")[1]:
                nuevas_sec.append(resp)
            else:
                print(f"{w}Formato de correo inválido (debe contener @ y un dominio).{r}")

        if not nuevas_sec and resp != "-":
            nuevas_sec = sec_actual

        resp_col = self.ui.leer(f"¿Usar vista por columnas por defecto? (s/{C('date')}N{C('rst')}): ").lower()
        nuevas_columnas = resp_col == "s"
        print()

        lines = [f'base   = "{nueva_base}"', f'editor = "{nuevo_editor}"']
        if nueva_primaria:
            lines.append(f'gpg_key = "{nueva_primaria}"')
        lines.append(f'gpg_keys_secondary = [{", ".join(f"\"{k}\"" for k in nuevas_sec)}]' if nuevas_sec else 'gpg_keys_secondary = []')
        lines.append(f'columnas = {str(nuevas_columnas).lower()}')
        contenido = "\n".join(lines) + "\n"
        print(f"\n{d}--- byte.toml ---{r}")
        print(contenido)

        resp = self.ui.leer(f"¿Guardar? (s/{d}N{r}): ").lower()
        if resp == "s":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contenido, encoding="utf-8")
            print(f"{C('plus')}✓ Guardado en {disp}{r}")
            self.config.base = nueva_base
            self.config.editor = nuevo_editor
            self.config.gpg_key = nueva_primaria
            self.config.gpg_keys_secondary = nuevas_sec
            self.config.columnas_default = nuevas_columnas
            self.config.used_config_path = target
            self.storage = ByteStorage(self.config.base)
            self.ui = ByteInterface(self.storage)
            if nueva_base != self.config.base:
                print(f"{w}Cambio de BASE aplicado (recreado el storage).{r}")
        else:
            print(f"{d}Cancelado{r}")

    def cmd_complete(self, args: List[str]) -> None:
        """Genera lista de grupos/eventos para autocompletado de shell."""
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self) -> None:
        """Muestra la ayuda del programa."""
        h = C("header")
        t = C("tree")
        d = C("date")
        r = C("rst")
        w = C("warn")
        print(f"{h}BYTE — Notas en Markdown y archivos binarios{r}\n")
        print(f"  {t}byte{r}              {d}árbol{r}")
        print(f"  {t}byte -t{r}           {d}árbol con fechas{r}")
        print(f"  {t}byte --columnas{r}   {d}árbol en columnas (compacto){r}")
        print(f"  {t}byte -h{r}           {d}esta ayuda{r}")
        print(f"  {t}byte.toml: columnas = true/false  {d}(cambia la vista por defecto){r}")
        print()
        print(f"  {h}Abrir / añadir{r}")
        print(f"  {t}byte{r} {d}evento{r}              abre en editor  {d}(crea .md si no existe){r}")
        print(f"  {t}byte{r} {d}evento{r} texto...      añade línea al final sin abrir editor")
        print(f"  {t}byte{r} {d}Grupo/evento{r}         abre evento explícito")
        print()
        print(f"  {h}Comandos{r}  {d}--comando  ·  letra{r}")
        print(f"  {t}--link    {d}l{r}  archivo {d}[nombre]{r}    hardlink/copia de archivo externo → evento")
        print(f"  {t}--unlink  {d}u{r}  {d}[evento]{r}            quita el registro del enlace {d}(archivos intactos){r}")
        print(f"  {t}--del     {d}d{r}  {d}[ruta]{r}              envía al .trash/")
        print(f"  {t}--mv      {d}m{r}  {d}[origen] [destino]{r}  mueve o fusiona eventos")
        print(f"  {t}--info    {d}i{r}  {d}[evento] [texto]{r}    nota corta asociada al evento (y grupos){r}")
        print(f"  {t}--gpg     {d}g{r}  evento {d}[llave]{r}      cifra con primaria+secundarias; sobre cifrado: añade secundaria")
        print(f"  {t}--nogpg   {d}q{r}  evento              descifra y elimina protección GPG")
        print(f"  {t}--check   {d}c{r}                      muestra configuración y sincroniza copias/enlaces")
        print(f"  {t}--config  {d}x{r}                      configuración inicial")
        print()
        print(f"  {h}Indicadores en el árbol normal (y en --columnas){r}")
        print(f"  {w}g{r} gpg   {d}b{r} binario   {w}i{r} info   {d}→{r} hardlink   {d}c →{r} copia   {d}x{r} enlace roto")
        print(f"  En modo columnas: 4 caracteres fijos con colores: [g][i][l/c/x][b]")


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    """Punto de entrada: configura, parsea argumentos y ejecuta el comando correspondiente."""
    config = Config()
    app = ByteApp(config)
    app.storage.asegurar_base()

    args = sys.argv[1:]

    if not args:
        if config.columnas_default:
            app.ui.print_arbol(column_mode=True)
        else:
            app.ui.print_arbol()
        return

    cmd = args[0]
    rest = args[1:]

    if cmd == "--columnas":
        show_dates = "-t" in rest or "--total" in rest
        app.ui.print_arbol(show_dates=show_dates, column_mode=True)
        return

    if cmd in ("-t", "--total", "-v"):
        app.ui.print_arbol(show_dates=True)
        return
    if cmd in ("-h", "--help", "help", "h"):
        app.mostrar_ayuda()
        return

    cmd_clean = cmd[2:] if cmd.startswith("--") else cmd
    dispatch = {
        "link": app.cmd_link, "del": app.cmd_del, "mv": app.cmd_mv,
        "info": app.cmd_info, "gpg": app.cmd_gpg, "nogpg": app.cmd_nogpg,
        "check": app.cmd_check, "unlink": app.cmd_unlink, "config": app.cmd_config,
        "_complete": app.cmd_complete,
        "l": app.cmd_link, "d": app.cmd_del, "m": app.cmd_mv,
        "i": app.cmd_info, "g": app.cmd_gpg, "q": app.cmd_nogpg,
        "c": app.cmd_check, "u": app.cmd_unlink, "x": app.cmd_config,
    }

    if cmd_clean in dispatch:
        dispatch[cmd_clean](rest)
    else:
        app.cmd_open([cmd] + rest)

if __name__ == "__main__":
    main()
