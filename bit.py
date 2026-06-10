#!/usr/bin/env python3
# bit.py — gestor de bitácoras con líneas fechadas, cifrado GPG y abreviaturas únicas
# Basado en byte.py, adaptado para el formato de logs de bit.

import os
import sys
import re
import json
import shutil
import subprocess
import tempfile
import unicodedata
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
# COLORES ANSI (igual que en byte)
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
# UTILIDADES ANSI
# ============================================================================

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

@lru_cache(maxsize=1024)
def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)

# ============================================================================
# CONFIGURACIÓN (bit.toml)
# ============================================================================

class Config:
    DEFAULT_BASE = Path.home() / "Documentos/Filen/Obsidian/bits"
    DEFAULT_EDITOR = os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")

    def __init__(self):
        self.base: Path = self.DEFAULT_BASE
        self.editor: str = self.DEFAULT_EDITOR
        self.gpg_key: str = ""
        self.gpg_keys_secondary: List[str] = []
        self.used_config_path: Optional[Path] = None
        self._load()

    def _load_toml_file(self, path: Path) -> Dict[str, Any]:
        if not path.is_file() or tomllib is None:
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _create_default_config(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        contenido = f'base   = "{self.DEFAULT_BASE}"\neditor = "{self.DEFAULT_EDITOR}"\ngpg_key = ""\ngpg_keys_secondary = []\n'
        path.write_text(contenido, encoding="utf-8")

    def _load(self) -> None:
        system_path = Path.home() / ".config" / "bit" / "bit.toml"
        cfg = self._load_toml_file(system_path)
        if cfg:
            self.used_config_path = system_path
        else:
            vault_path = self.DEFAULT_BASE / ".bit" / "bit.toml"
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

# ============================================================================
# REGISTRO (bit.json) – igual que byte.json pero para bit
# ============================================================================

class Registry:
    def __init__(self, base: Path):
        self.path = base / ".bit" / "bit.json"
        self._data = None
        self._mtime: float = 0.0
        self._load()

    def _load(self) -> None:
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        self._mtime = self.path.stat().st_mtime

    def _default_data(self):
        return {"gpg": {}, "abbr_cache": {}}

    def _key(self, grupo: str, stem: str) -> str:
        return f"{grupo}/{stem}"

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

    # --- abbr_cache ---
    def get_abbr_cache(self) -> Dict[str, Dict]:
        return self._data.get("abbr_cache", {})

    def set_abbr_cache(self, abbr_cache: Dict[str, Dict]) -> None:
        self._data["abbr_cache"] = abbr_cache
        self._save()

# ============================================================================
# GESTOR DE ALMACENAMIENTO CON CIFRADO GPG
# ============================================================================

class BitStorage:
    def __init__(self, base_path: Path, registry: Registry):
        self.base = base_path
        self.registry = registry

    def asegurar_base(self):
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / ".bit").mkdir(parents=True, exist_ok=True)

    def normalize(self, txt: str) -> str:
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize('NFKD', txt) if unicodedata.category(c) != 'Mn')

    def titulo(self, txt: str) -> str:
        return txt.strip().capitalize()

    def get_grupos(self) -> List[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir() and not d.name.startswith("."))

    def get_eventos(self, grupo: str) -> List[str]:
        gp = self.base / grupo
        if not gp.is_dir():
            return []
        stems = []
        for f in gp.iterdir():
            if f.is_file() and not f.name.startswith("."):
                if f.suffix.lower() == ".gpg":
                    stems.append(Path(f.stem).stem)
                else:
                    stems.append(f.stem)
        return sorted(stems)

    def _get_evento_path(self, grupo: str, evento: str) -> Path:
        """Devuelve la ruta real (puede ser .md o .gpg) sin comprobar existencia."""
        gp = self.base / self.titulo(grupo)
        # Priorizar .gpg si está protegido
        if self.registry.is_protected(grupo, evento):
            return gp / f"{evento.lower()}.gpg"
        else:
            return gp / f"{evento.lower()}.md"

    def _gpg_encrypt(self, plain_path: Path, key_id: str, output_path: Path) -> None:
        keys = [k.strip() for k in key_id.split(",") if k.strip()] if "," in key_id else [key_id]
        cmd = ["gpg", "--yes", "--batch", "--trust-model", "always"]
        for k in keys:
            cmd += ["-r", k]
        cmd += ["-o", str(output_path), "-e", str(plain_path)]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())

    def _gpg_decrypt_to_tmp(self, path: Path) -> Path:
        inner_ext = ".md"
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

    def read_evento(self, grupo: str, evento: str) -> List[Dict[str, str]]:
        """Lee el evento (descifrando si es necesario) y devuelve lista de líneas con fecha y comentario."""
        p = self._get_evento_path(grupo, evento)
        if not p.is_file():
            return []
        # Si es .gpg, descifrar a temporal
        if p.suffix.lower() == ".gpg":
            try:
                tmp = self._gpg_decrypt_to_tmp(p)
                content = tmp.read_text(encoding="utf-8")
                tmp.unlink()
            except Exception as e:
                print(f"GPG error al leer {grupo}/{evento}: {e}", file=sys.stderr)
                return []
        else:
            content = p.read_text(encoding="utf-8")

        lines = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})\s+(.*)$", line)
            if m:
                lines.append({"fecha": m.group(1), "comentario": m.group(2)})
        return lines

    def write_evento(self, grupo: str, evento: str, lines: List[Dict[str, str]]) -> None:
        """Escribe el evento (cifrando si está protegido)."""
        p = self._get_evento_path(grupo, evento)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Generar contenido textual
        content = ""
        for l in lines:
            content += f"{l['fecha']} {l['comentario']}\n"

        # Si está protegido, cifrar
        if self.registry.is_protected(grupo, evento):
            key_id = self.registry.key_id(grupo, evento)
            if not key_id:
                print(f"Error: evento {grupo}/{evento} marcado como cifrado pero sin clave", file=sys.stderr)
                return
            # Escribir a temporal plano
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp_path = Path(tf.name)
            try:
                self._gpg_encrypt(tmp_path, key_id, p)
            finally:
                tmp_path.unlink()
        else:
            # Si existe un .gpg antiguo, eliminarlo
            gpg_path = p.with_suffix(".gpg")
            if gpg_path.is_file():
                gpg_path.unlink()
            p.write_text(content, encoding="utf-8")

    def evento_path(self, grupo: str, evento: str) -> Path:
        """Ruta del archivo (útil para editores, siempre devuelve .md si no está cifrado, .gpg si cifrado)."""
        return self._get_evento_path(grupo, evento)

    def trash(self, path: Path) -> None:
        if not path.exists():
            return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        shutil.move(str(path), str(dest))

    def grupo_path(self, grupo: str) -> Path:
        return self.base / self.titulo(grupo)

    def limpiar_vacios(self) -> None:
        for g in self.get_grupos():
            gp = self.grupo_path(g)
            if gp.is_dir() and not any(gp.iterdir()):
                gp.rmdir()

# ============================================================================
# INTERFAZ CON ABREVIATURAS ÚNICAS (COPIADO DE BYTE)
# ============================================================================

class BitInterface:
    def __init__(self, storage: BitStorage, registry: Registry):
        self.storage = storage
        self.registry = registry
        self._cache_abbr: Dict[Tuple[str, int], Dict[str, str]] = {}
        self._load_abbr_from_registry()

    def _load_abbr_from_registry(self) -> None:
        self._persistent_cache = self.registry.get_abbr_cache()

    def _save_abbr_to_registry(self) -> None:
        self.registry.set_abbr_cache(self._persistent_cache)

    def _get_abreviaturas_from_persistent(self, grupo: str, long: int) -> Optional[Dict[str, str]]:
        gp = self.storage.base / grupo
        if not gp.is_dir():
            return None
        current_mtime = gp.stat().st_mtime
        cached = self._persistent_cache.get(grupo)
        if cached and abs(cached["mtime"] - current_mtime) < 0.001:
            return cached["abbr"]
        return None

    def _save_abbr_to_persistent(self, grupo: str, abbr: Dict[str, str]) -> None:
        gp = self.storage.base / grupo
        if not gp.is_dir():
            return
        current_mtime = gp.stat().st_mtime
        self._persistent_cache[grupo] = {"mtime": current_mtime, "abbr": abbr}
        self._save_abbr_to_registry()

    def update_all_abbreviations(self) -> None:
        """Fuerza actualización de caché de abreviaturas para todos los grupos."""
        self._persistent_cache = {}
        grupos = self.storage.get_grupos()
        for grupo in grupos:
            evs = self.storage.get_eventos(grupo)
            abbr = self.calc_abreviaturas(evs, 2)
            self._save_abbr_to_persistent(grupo, abbr)
        self._cache_abbr.clear()

    def _get_abreviaturas(self, grupo: str, long: int) -> Dict[str, str]:
        """Retorna abreviaturas para un grupo (long=2 para eventos, long=3 para grupos)."""
        if long != 2:
            # para grupos (long=3) no cacheamos persistentemente
            evs = self.storage.get_eventos(grupo) if long == 2 else self.storage.get_grupos()
            return self.calc_abreviaturas(evs, long)

        key = (grupo, long)
        persistent_abbr = self._get_abreviaturas_from_persistent(grupo, long)
        if persistent_abbr is not None:
            self._cache_abbr[key] = persistent_abbr
            return persistent_abbr

        evs = self.storage.get_eventos(grupo)
        abbr = self.calc_abreviaturas(evs, long)
        self._save_abbr_to_persistent(grupo, abbr)
        self._cache_abbr[key] = abbr
        return abbr

    def calc_abreviaturas(self, lista: List[str], long: int) -> Dict[str, str]:
        """Calcula abreviaturas únicas de longitud variable (hasta 5)."""
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

    def _render_nombre_con_label(self, nombre: str, long: int, abbr_map: Optional[Dict[str, str]] = None) -> str:
        """Renderiza un nombre (grupo o evento) con su abreviatura resaltada."""
        if abbr_map is None:
            abbr_map = {}
        abrev = abbr_map.get(nombre)
        if not abrev:
            return C("event") + nombre + C("rst")
        nombre_plano = self.storage.normalize(nombre)
        idx = nombre_plano.find(abrev)
        if idx != -1:
            pre = nombre[:idx]
            lbl = nombre[idx:idx+long]
            post = nombre[idx+long:]
            return f"{C('event')}{pre}{C('rst')}{C('bold')}{lbl}{C('rst')}{C('event')}{post}{C('rst')}"
        else:
            return f"{C('event')}{nombre} {C('bold')}{abrev}{C('rst')}"

    def render_ruta_completa(self, grupo: str, evento: str) -> str:
        grupos = self.storage.get_grupos()
        g_abbr_map = {g: g[:3].lower() for g in grupos}
        g_render = self._render_nombre_con_label(grupo, 3, g_abbr_map)
        evs = self.storage.get_eventos(grupo)
        e_abbr_map = self._get_abreviaturas(grupo, 2)
        e_render = self._render_nombre_con_label(evento, 2, e_abbr_map)
        return f"{g_render}{C('tree')}/{C('rst')}{e_render}"

    def leer(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C('tree')}(Interrumpido){C('rst')}")
            sys.exit(0)

    def pedir_grupo(self, label: str = "Grupo") -> str:
        grupos = self.storage.get_grupos()
        self.print_arbol_compacto()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in grupos:
                return res
            g_abbr_map = {g: g[:3].lower() for g in grupos}
            for g, ab in g_abbr_map.items():
                if ab == res:
                    return g
            res_norm = self.storage.normalize(res)
            for g in grupos:
                if self.storage.normalize(g) == res_norm:
                    return g
            return self.storage.titulo(res)

    def pedir_evento(self, grupo: str, label: str = "Evento") -> str:
        evs = self.storage.get_eventos(grupo)
        if evs:
            e_abbr_map = self._get_abreviaturas(grupo, 2)
            print(f"\n  Eventos en {grupo}:")
            for e in evs:
                print(f"    {self._render_nombre_con_label(e, 2, e_abbr_map)}")
            print()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in evs:
                return res
            e_abbr_map = self._get_abreviaturas(grupo, 2)
            for e, ab in e_abbr_map.items():
                if ab == res:
                    return e
            res_norm = self.storage.normalize(res)
            for e in evs:
                if self.storage.normalize(e) == res_norm:
                    return e
            return res

    def print_arbol_compacto(self, grupos_filter: Optional[List[str]] = None) -> None:
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        g_abbr_map = {g: g[:3].lower() for g in grupos}
        for gi, grupo in enumerate(grupos):
            is_last_g = gi == len(grupos) - 1
            prefix_g = "└── " if is_last_g else "├── "
            g_line = f"{C('tree')}{prefix_g}{self._render_nombre_con_label(grupo, 3, g_abbr_map)}"
            print(g_line)
            evs = self.storage.get_eventos(grupo)
            e_abbr_map = self._get_abreviaturas(grupo, 2)
            for ei, ev in enumerate(evs):
                ev_lines = self.storage.read_evento(grupo, ev)
                extra = f"  {C('tree')}({C('count')}{len(ev_lines)}{C('tree')}){C('rst')}"
                prefix_e = "└── " if ei == len(evs) - 1 else "├── "
                indent = "    " if is_last_g else "│   "
                # Añadir candado si está cifrado
                lock = f"{C('warn')}🔒 {C('rst')}" if self.registry.is_protected(grupo, ev) else ""
                e_line = f"{C('tree')}{indent}{prefix_e}{lock}{self._render_nombre_con_label(ev, 2, e_abbr_map)}{extra}"
                print(e_line)

    def print_resumen(self) -> None:
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return
        g_abbr_map = {g: g[:3].lower() for g in grupos}
        for gi, grupo in enumerate(grupos):
            is_last_g = gi == len(grupos) - 1
            prefix_g = "└── " if is_last_g else "├── "
            g_line = f"{C('tree')}{prefix_g}{self._render_nombre_con_label(grupo, 3, g_abbr_map)}"
            print(g_line)
            evs = self.storage.get_eventos(grupo)
            e_abbr_map = self._get_abreviaturas(grupo, 2)
            for ei, ev in enumerate(evs):
                ev_lines = self.storage.read_evento(grupo, ev)
                ultima = f"  {C('date')}{ev_lines[-1]['fecha']}" if ev_lines else ""
                extra = f"  {C('tree')}({C('count')}{len(ev_lines)}{C('tree')}){C('rst')}{ultima}"
                prefix_e = "└── " if ei == len(evs) - 1 else "├── "
                indent = "    " if is_last_g else "│   "
                lock = f"{C('warn')}🔒 {C('rst')}" if self.registry.is_protected(grupo, ev) else ""
                e_line = f"{C('tree')}{indent}{prefix_e}{lock}{self._render_nombre_con_label(ev, 2, e_abbr_map)}{extra}"
                print(e_line)

    def print_evento_tabla(self, grupo: str, evento: str, show_dates: bool = False) -> None:
        lines = self.storage.read_evento(grupo, evento)
        if not lines:
            print("  (sin registros)")
            return
        ruta = self.render_ruta_completa(grupo, evento)
        lock = f"{C('warn')}🔒 {C('rst')}" if self.registry.is_protected(grupo, evento) else ""
        print(f"\n  {lock}{ruta}")
       
        for i, line in enumerate(lines, 1):
            date_str = f"{C('date')}{line['fecha']}{C('tree')} │ " if show_dates else ""
            print(f"  {C('count')}{i:2d}{C('tree')} │ {date_str}{C('event')}{line['comentario']}{C('rst')}")
        print()

 
# ============================================================================
# APLICACIÓN PRINCIPAL
# ============================================================================

class BitApp:
    def __init__(self, config: Config):
        self.config = config
        self.registry = Registry(config.base)
        self.storage = BitStorage(config.base, self.registry)
        self.ui = BitInterface(self.storage, self.registry)

    def asegurar_base(self):
        self.storage.asegurar_base()

    def find_grupo(self, token: str) -> Optional[str]:
        grupos = self.storage.get_grupos()
        if token in grupos:
            return token
        g_abbr = self.ui.calc_abreviaturas(grupos, 3)
        for g, ab in g_abbr.items():
            if ab == token:
                return g
        token_norm = self.storage.normalize(token)
        for g in grupos:
            if self.storage.normalize(g) == token_norm:
                return g
        return None

    def parse_arg(self, arg: str) -> Tuple[Optional[str], Optional[str]]:
        if not arg:
            return None, None
        m = re.match(r"^([^/]+)/(.+)$", arg)
        if m:
            g_raw, ev_raw = m.group(1), m.group(2)
            grupo = self.find_grupo(g_raw) or self.storage.titulo(g_raw)
            evs = self.storage.get_eventos(grupo) if grupo else []
            e_abbr = self.ui.calc_abreviaturas(evs, 2)
            for e, ab in e_abbr.items():
                if ab == ev_raw.lower():
                    return grupo, e
            return grupo, ev_raw
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
            evs = self.storage.get_eventos(grupo)
            e_abbr = self.ui.calc_abreviaturas(evs, 2)
            for ev, ab in e_abbr.items():
                if ab == token.lower():
                    return grupo, ev
            token_norm = self.storage.normalize(token)
            for ev in evs:
                if self.storage.normalize(ev) == token_norm:
                    return grupo, ev
        grupo = self.find_grupo(token)
        if grupo:
            return grupo, None
        return None, token

    def insert_sorted(self, lines: List[Dict], new_line: Dict) -> List[Dict]:
        lines.append(new_line)
        try:
            lines.sort(key=lambda x: datetime.strptime(x["fecha"], "%Y-%m-%d %H:%M"))
        except Exception:
            pass
        return lines

    def limpiar_vacios(self):
        self.storage.limpiar_vacios()

    def cmd_add(self, args: List[str]) -> None:
        if not args:
            grupo = self.ui.pedir_grupo()
            if not grupo:
                return
            evento = self.ui.pedir_evento(grupo)
            if not evento:
                return
            comentario = self.ui.leer("Comentario: ")
            if not comentario:
                return
        else:
            grupo, evento = self.resolver_arg(args[0])
            if not grupo:
                grupo = self.ui.pedir_grupo(f"Crear nuevo grupo para '{evento}'")
                if not grupo:
                    return
            if not evento:
                evento = self.ui.pedir_evento(grupo, "Evento")
                if not evento:
                    return
            comentario = " ".join(args[1:]) if len(args) > 1 else self.ui.leer("Comentario: ")
            if not comentario:
                return

        fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = self.storage.read_evento(grupo, evento)
        lines = self.insert_sorted(lines, {"fecha": fecha_str, "comentario": comentario})
        self.storage.write_evento(grupo, evento, lines)
        ruta_fmt = self.ui.render_ruta_completa(grupo, evento)
        print(f"{C('plus')}+ {ruta_fmt} {C('tree')}│{C('rst')} {comentario}")

    def cmd_listar_evento(self, arg: str, show_dates: bool = False) -> None:
        g, e = self.resolver_arg(arg)
        if g and e:
            self.ui.print_evento_tabla(g, e, show_dates)
        elif g:
            self.ui.print_arbol_compacto([g])
        else:
            print(f"No encontrado: {arg}")

    def cmd_edit(self, args: List[str]) -> None:
        entrada = args[0] if args else self.ui.pedir_grupo()
        g, e = self.resolver_arg(entrada)
        if not g or not e:
            print("Evento no encontrado.")
            return

        # Obtener la ruta real (puede ser .md o .gpg)
        path = self.storage.evento_path(g, e)

        # Si el evento está cifrado, trabajar con un temporal descifrado
        if self.registry.is_protected(g, e):
            # Descifrar a temporal
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(path)
            except RuntimeError as ex:
                print(f"Error al descifrar: {ex}")
                return

            # Guardar timestamp original del temporal
            original_mtime = tmp.stat().st_mtime

            # Abrir editor
            os.system(f'{self.config.editor} "{tmp}"')

            # Si hubo cambios, re-cifrar
            if tmp.stat().st_mtime != original_mtime:
                # Leer el contenido editado (debe ser texto plano con formato de bitácora)
                try:
                    new_content = tmp.read_text(encoding="utf-8")
                except Exception as e:
                    print(f"Error al leer el archivo temporal: {e}")
                    tmp.unlink()
                    return

                # Extraer líneas con el formato esperado (fecha + comentario)
                lines = []
                for line in new_content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})\s+(.*)$", line)
                    if m:
                        lines.append({"fecha": m.group(1), "comentario": m.group(2)})
                    else:
                        # Si no coincide, lo tratamos como comentario sin fecha? Mejor advertir
                        print(f"Línea ignorada (formato incorrecto): {line}", file=sys.stderr)

                if not lines:
                    print("El archivo editado no contiene líneas válidas. No se guardarán cambios.")
                    tmp.unlink()
                    return

                # Escribir de nuevo (el método write_evento se encarga de cifrar si es necesario)
                self.storage.write_evento(g, e, lines)
                ruta_fmt = self.ui.render_ruta_completa(g, e)
                print(f"{C('count')}~ {ruta_fmt}{C('rst')} (editado y recifrado)")
            else:
                print(f"{C('date')}  (sin cambios){C('rst')}")

            # Limpiar temporal
            tmp.unlink()
        else:
            # Evento no cifrado: abrir directamente
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            os.system(f'{self.config.editor} "{path}"')
            # Tras edición, leer y reescribir para normalizar formato (por si acaso)
            lines = self.storage.read_evento(g, e)
            self.storage.write_evento(g, e, lines)
            ruta_fmt = self.ui.render_ruta_completa(g, e)
            print(f"{C('count')}~ {ruta_fmt}{C('rst')}")

        self.limpiar_vacios()

    def cmd_pop(self, args: List[str]) -> None:
        entrada = args[0] if args else self.ui.pedir_grupo()
        g, e = self.resolver_arg(entrada)
        if not g or not e:
            print("No encontrado.")
            return
        lines = self.storage.read_evento(g, e)
        if not lines:
            print("Vacío.")
            return
        popped = lines.pop()
        self.storage.write_evento(g, e, lines)
        ruta_fmt = self.ui.render_ruta_completa(g, e)
        print(f"{C('minus')}- {ruta_fmt} {C('tree')}│{C('rst')} Quitada última línea: {popped['comentario']}")
        self.limpiar_vacios()

    def cmd_del(self, args: List[str]) -> None:
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o evento: ")
        if not entrada:
            print("Cancelado")
            return
        grupo, evento = self.resolver_arg(entrada)
        if grupo and not evento:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir():
                print(f"No existe el grupo '{grupo}'")
                return
            self.ui.print_arbol_compacto([grupo])
            if self.ui.leer(f"Enviar al trash el grupo '{grupo}' y todo su contenido? (s/n): ") == "s":
                self.storage.trash(gp)
                print(f"Enviado al trash: {grupo}/")
        else:
            if not grupo or not evento:
                print(f"No se pudo encontrar el grupo o evento para: '{entrada}'")
                return
            p = self.storage.evento_path(grupo, evento)
            if not p.is_file():
                print(f"No existe el archivo {grupo}/{evento}")
                return
            if self.ui.leer(f"Enviar al trash '{grupo}/{evento}'? (s/n): ") == "s":
                self.storage.trash(p)
                # Eliminar del registro GPG si existe
                if self.registry.is_protected(grupo, evento):
                    self.registry.unmark_gpg(grupo, evento)
                ruta_fmt = self.ui.render_ruta_completa(grupo, evento)
                print(f"Enviado al trash: {ruta_fmt}")
        self.limpiar_vacios()

    def cmd_mv(self, args: List[str]) -> None:
        # Mantiene la misma lógica que el bit original, solo adapta rutas y mensajes
        if not args:
            g_orig = self.ui.pedir_grupo("Grupo origen")
            e_orig = self.ui.pedir_evento(g_orig, "Evento origen")
            opcion = self.ui.leer("¿Mover [e]vento completo o una [l]ínea específica? (e/l): ").lower()
            g_dest = self.ui.pedir_grupo("Grupo destino")
            if opcion == "e":
                e_dest = self.ui.leer(f"Nombre en destino (Enter = '{e_orig}'): ") or e_orig
                args = [f"{g_orig}/{e_orig}", f"{g_dest}/{e_dest}"]
            else:
                self.ui.print_evento_tabla(g_orig, e_orig)
                n = int(self.ui.leer("Número de línea a mover: "))
                e_dest = self.ui.pedir_evento(g_dest, "Evento destino")
                args = [f"{g_orig}/{e_orig}", str(n), f"{g_dest}/{e_dest}"]
        if len(args) == 3 or (len(args) >= 2 and args[1].isdigit()):
            g_orig, e_orig = self.resolver_arg(args[0])
            n = int(args[1])
            g_dest, e_dest = self.resolver_arg(args[2]) if len(args) > 2 else (self.ui.pedir_grupo("Grupo destino"), e_orig)
            if not g_orig or not e_orig:
                print("Error: Origen no válido.")
                return
            lines_orig = self.storage.read_evento(g_orig, e_orig)
            if not lines_orig or n > len(lines_orig) or n <= 0:
                print("Línea origen inválida.")
                return
            moved = lines_orig.pop(n - 1)
            self.storage.write_evento(g_orig, e_orig, lines_orig)
            self.storage.grupo_path(g_dest).mkdir(parents=True, exist_ok=True)
            lines_dest = self.storage.read_evento(g_dest, e_dest)
            lines_dest = self.insert_sorted(lines_dest, moved)
            self.storage.write_evento(g_dest, e_dest, lines_dest)
            r_orig = self.ui.render_ruta_completa(g_orig, e_orig)
            r_dest = self.ui.render_ruta_completa(g_dest, e_dest)
            print(f"~ Línea #{n} movida: {r_orig} → {r_dest} {C('tree')}│{C('rst')} {moved['comentario']}")
            self.limpiar_vacios()
        elif len(args) == 2:
            g_orig, e_orig = self.resolver_arg(args[0])
            g_dest, e_dest = self.resolver_arg(args[1])
            if g_dest and not e_dest:
                e_dest = e_orig
            elif not g_dest and not e_dest:
                g_dest, e_dest = self.parse_arg(args[1])
                if g_dest and not e_dest:
                    e_dest = e_orig
            if not g_orig or not e_orig or not g_dest:
                print("Error: Rutas de origen o destino inválidas.")
                return
            src = self.storage.evento_path(g_orig, e_orig)
            dest = self.storage.evento_path(g_dest, e_dest)
            if not src.is_file():
                print(f"Error: No existe el evento {g_orig}/{e_orig}")
                return
            self.storage.grupo_path(g_dest).mkdir(parents=True, exist_ok=True)
            r_orig = self.ui.render_ruta_completa(g_orig, e_orig)
            r_dest = self.ui.render_ruta_completa(g_dest, e_dest)
            if dest.is_file():
                lines_orig = self.storage.read_evento(g_orig, e_orig)
                lines_dest = self.storage.read_evento(g_dest, e_dest)
                for line in lines_orig:
                    lines_dest = self.insert_sorted(lines_dest, line)
                self.storage.write_evento(g_dest, e_dest, lines_dest)
                src.unlink()
                print(f"✓ Evento fusionado: {r_orig} ➔ {r_dest}")
            else:
                # Mover también la protección GPG si existe
                if self.registry.is_protected(g_orig, e_orig):
                    key_id = self.registry.key_id(g_orig, e_orig)
                    self.registry.mark_gpg(g_dest, e_dest, key_id)
                    self.registry.unmark_gpg(g_orig, e_orig)
                shutil.move(str(src), str(dest))
                print(f"✓ Evento movido: {r_orig} ➔ {r_dest}")
            self.limpiar_vacios()
        else:
            print("Uso de mv:\n  bit mv [Origen] [N] [Destino]  -> Mover línea N\n  bit mv [Origen] [GrupoDestino/]-> Mover evento completo")

    def cmd_rm(self, args: List[str]) -> None:
        if not args:
            print("Indica evento.")
            return
        g, e = self.resolver_arg(args[0])
        if not g or not e:
            print("No encontrado.")
            return
        self.ui.print_evento_tabla(g, e)
        n_str = args[1] if len(args) > 1 else self.ui.leer("Número de línea a quitar: ")
        if not n_str.isdigit():
            print("Cancelado.")
            return
        n = int(n_str)
        lines = self.storage.read_evento(g, e)
        if n > len(lines) or n <= 0:
            print("Línea inválida.")
            return
        removed = lines.pop(n - 1)
        self.storage.write_evento(g, e, lines)
        ruta_fmt = self.ui.render_ruta_completa(g, e)
        print(f"{C('minus')}- {ruta_fmt} {C('tree')}│{C('rst')} Removida línea #{n}: {removed['comentario']}")
        self.limpiar_vacios()

    def cmd_dir(self, args: List[str]) -> None:
        print(f"Base path: {self.storage.base}")

    def cmd_raw(self, args: List[str]) -> None:
        if not args:
            print("Indica el evento o grupo/evento.")
            return
        g, e = self.resolver_arg(args[0])
        if not g or not e:
            print(f"No encontrado: {args[0]}")
            return
        lines = self.storage.read_evento(g, e)
        if not lines:
            print("  (sin registros)")
            return
        print(f"| Fecha y Hora | {g}/{e} |")
        print("| --- | --- |")
        for line in lines:
            print(f"| {line['fecha']} | {line['comentario']} |")

    def cmd_gpg(self, args: List[str]) -> None:
        """Cifra un evento (si es texto plano) con la clave primaria y secundarias."""
        if not shutil.which("gpg"):
            print("gpg no está disponible en el sistema.")
            return
        if not args:
            self.ui.print_arbol_compacto()
            entrada = self.ui.leer("Evento a cifrar: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        g, e = self.resolver_arg(entrada)
        if not g or not e:
            print(f"No encontrado: {entrada}")
            return
        if self.registry.is_protected(g, e):
            print(f"  {C('warn')}El evento ya está cifrado. Use 'bit q' para descifrar.{C('rst')}")
            return
        ev_path = self.storage.evento_path(g, e)
        if not ev_path.is_file():
            print(f"El evento {g}/{e} no existe.")
            return
        # Leer líneas (asegurar que es texto)
        lines = self.storage.read_evento(g, e)
        if not lines:
            print("El evento está vacío. Nada que cifrar.")
            return
        # Obtener claves
        if not self.config.gpg_key:
            print(f"{C('warn')}Sin llave primaria configurada. Use 'bit x' o edite bit.toml.{C('rst')}")
            return
        all_keys = [self.config.gpg_key] + [k for k in self.config.gpg_keys_secondary if k != self.config.gpg_key]
        # Escribir contenido plano temporal
        content = ""
        for l in lines:
            content += f"{l['fecha']} {l['comentario']}\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tf:
            tf.write(content)
            tmp_path = Path(tf.name)
        try:
            # Cifrar
            gpg_path = ev_path.with_suffix(".gpg")
            keys = []
            for k in all_keys:
                keys += ["-r", k]
            cmd = ["gpg", "--yes", "--batch", "--trust-model", "always"] + keys + ["-o", str(gpg_path), "-e", str(tmp_path)]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0:
                print(f"GPG error: {res.stderr.decode()}")
                return
            # Marcar en registro
            self.registry.mark_gpg(g, e, ",".join(all_keys))
            # Eliminar el .md original
            ev_path.unlink()
            print(f"{C('plus')}~ {self.ui.render_ruta_completa(g, e)}{C('rst')}  {C('warn')}g{C('rst')} cifrado")
        finally:
            tmp_path.unlink()

    def cmd_nogpg(self, args: List[str]) -> None:
        """Descifra un evento cifrado y elimina la protección GPG."""
        if not shutil.which("gpg"):
            print("gpg no está disponible en el sistema.")
            return
        if not args:
            self.ui.print_arbol_compacto()
            entrada = self.ui.leer("Evento a descifrar: ")
            if not entrada:
                return
        else:
            entrada = args[0]
        g, e = self.resolver_arg(entrada)
        if not g or not e:
            print(f"No encontrado: {entrada}")
            return
        if not self.registry.is_protected(g, e):
            print(f"  {C('date')}El evento no está cifrado.{C('rst')}")
            return
        ev_path = self.storage.evento_path(g, e)
        if not ev_path.is_file() or ev_path.suffix.lower() != ".gpg":
            print(f"No se encuentra el archivo cifrado {g}/{e}.gpg")
            return
        try:
            tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
        except RuntimeError as e:
            print(f"GPG error al descifrar: {e}")
            return
        # Leer contenido descifrado y reescribir como .md
        content = tmp.read_text(encoding="utf-8")
        tmp.unlink()
        md_path = ev_path.with_suffix(".md")
        md_path.write_text(content, encoding="utf-8")
        ev_path.unlink()
        self.registry.unmark_gpg(g, e)
        print(f"{C('plus')}~ {self.ui.render_ruta_completa(g, e)}{C('rst')}  (descifrado, sin GPG)")

    def cmd_config(self, args: List[str]) -> None:
        """Configuración básica (base, editor, GPG)."""
        print(f"{C('header')}Configuración de bit{ C('rst')}")
        print(f"Archivo de configuración: {self.config.used_config_path or '(ninguno)'}")
        print(f"Directorio base actual: {self.config.base}")
        print(f"Editor: {self.config.editor}")
        print(f"Clave GPG primaria: {self.config.gpg_key or '(no configurada)'}")
        print(f"Claves GPG secundarias: {', '.join(self.config.gpg_keys_secondary) or '(ninguna)'}")
        resp = self.ui.leer("¿Reconfigurar? (s/N): ").lower()
        if resp != "s":
            return
        nueva_base = self.ui.leer(f"Nueva base [{self.config.base}]: ")
        if nueva_base:
            self.config.base = Path(nueva_base).expanduser().resolve()
        nuevo_editor = self.ui.leer(f"Nuevo editor [{self.config.editor}]: ")
        if nuevo_editor:
            self.config.editor = nuevo_editor
        nueva_gpg = self.ui.leer(f"Nueva clave GPG primaria [{self.config.gpg_key}]: ")
        if nueva_gpg:
            self.config.gpg_key = nueva_gpg
        nuevas_sec = []
        while True:
            sec = self.ui.leer("Añadir clave secundaria (vacío termina): ")
            if not sec:
                break
            nuevas_sec.append(sec)
        if nuevas_sec:
            self.config.gpg_keys_secondary = nuevas_sec
        # Guardar configuración
        target = Path.home() / ".config" / "bit" / "bit.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'base   = "{self.config.base}"', f'editor = "{self.config.editor}"']
        if self.config.gpg_key:
            lines.append(f'gpg_key = "{self.config.gpg_key}"')
        lines.append(f'gpg_keys_secondary = [{", ".join(f"\"{k}\"" for k in self.config.gpg_keys_secondary)}]' if self.config.gpg_keys_secondary else 'gpg_keys_secondary = []')
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{C('plus')}Configuración guardada en {target}{C('rst')}")

    def cmd_complete(self, args: List[str]) -> None:
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self) -> None:
        print(f"""{C('header')}BIT — Bitácora con líneas fechadas, cifrado GPG y abreviaturas únicas{C('rst')}
  bit                       Árbol compacto (grupos 3 letras, eventos 2 letras)
  bit -t | -v               Árbol con fechas de última línea
  bit [evento]              Muestra registros del evento
  bit -t [evento]           Muestra registros con marcas de tiempo
  bit [evento] [texto...]   Añade una línea con fecha actual
  bit edit [evento]         Abre el archivo en el editor
  bit pop [evento]          Elimina la última línea
  bit rm [evento] [n]       Elimina una línea específica
  bit mv [origen] [destino] Mueve evento o línea
  bit del [ruta]            Envía evento o grupo al .trash/
  bit g [evento]            Cifra evento con GPG
  bit q [evento]            Descifra evento
  bit x                     Configuración inicial
  bit raw [evento]          Exporta en formato Markdown
  bit dir                   Muestra la ruta base
  bit -h                    Esta ayuda
""")

# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    config = Config()
    app = BitApp(config)
    app.asegurar_base()

    args = sys.argv[1:]

    if not args:
        app.ui.print_arbol_compacto()
        return

    cmd = args[0]
    rest = args[1:]

    if cmd in ("-t", "--total", "-v", "--verbose"):
        if rest:
            app.cmd_listar_evento(rest[0], show_dates=True)
        else:
            app.ui.print_resumen()
        return

    dispatch = {
        "a": app.cmd_add, "add": app.cmd_add,
        "e": app.cmd_edit, "edit": app.cmd_edit,
        "rm": app.cmd_rm, "pop": app.cmd_pop,
        "del": app.cmd_del, "mv": app.cmd_mv,
        "dir": app.cmd_dir, "raw": app.cmd_raw,
        "g": app.cmd_gpg, "gpg": app.cmd_gpg,
        "q": app.cmd_nogpg, "nogpg": app.cmd_nogpg,
        "x": app.cmd_config, "config": app.cmd_config,
        "_complete": app.cmd_complete,
        "h": lambda _: app.mostrar_ayuda(), "help": lambda _: app.mostrar_ayuda()
    }

    if cmd in dispatch:
        dispatch[cmd](rest)
    elif len(args) == 1:
        app.cmd_listar_evento(cmd, show_dates=False)
    else:
        app.cmd_add(args)

if __name__ == "__main__":
    main()
