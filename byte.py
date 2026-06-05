#!/usr/bin/env python3
# byte.py — gestor de notas Markdown en grupos/eventos (Linux/macOS)

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
# CONFIGURACIÓN
# ============================================================================

class Config:
    DEFAULT_BASE = Path.home() / "Documentos/Filen/Obsidian/bytes"
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

    def save(self, base: Path, editor: str, gpg_key: str, gpg_keys_secondary: List[str]) -> None:
        system_path = Path.home() / ".config" / "byte" / "byte.toml"
        target = system_path if system_path.is_file() else self.base / ".byte" / "byte.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'base   = "{base}"', f'editor = "{editor}"']
        if gpg_key:
            lines.append(f'gpg_key = "{gpg_key}"')
        lines.append(f'gpg_keys_secondary = [{", ".join(f"\"{k}\"" for k in gpg_keys_secondary)}]' if gpg_keys_secondary else 'gpg_keys_secondary = []')
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.base = base
        self.editor = editor
        self.gpg_key = gpg_key
        self.gpg_keys_secondary = gpg_keys_secondary
        self.used_config_path = target

# ============================================================================
# UTILIDADES
# ============================================================================

def _diff_tool() -> Optional[str]:
    for tool in ("delta", "bat"):
        if shutil.which(tool):
            return tool
    return None

def mostrar_diff(a: Path, b: Path) -> None:
    tool = _diff_tool()
    a_str, b_str = str(a), str(b)
    if tool == "delta":
        os.system(f'diff -u "{a_str}" "{b_str}" | delta')
    elif tool == "bat":
        r = subprocess.run(["diff", "-u", a_str, b_str], capture_output=True, text=True)
        if r.stdout:
            tmp = tempfile.NamedTemporaryFile(suffix=".diff", delete=False, mode="w")
            tmp.write(r.stdout)
            tmp.close()
            os.system(f'bat --language=diff "{tmp.name}"')
            Path(tmp.name).unlink()
        else:
            print("  (sin diferencias)")
    else:
        os.system(f'diff -u "{a_str}" "{b_str}"')

def _resaltar(texto: str, abrev: Optional[str], long: int, color_norm: str, color_res: str) -> str:
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
# REGISTROS JSON (clase base)
# ============================================================================

class JsonRegistry:
    def __init__(self, path: Path):
        self.path = path
        self._data: Optional[Any] = None

    def _load(self) -> None:
        if self._data is not None:
            return
        if self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = self._default_data()
        else:
            self._data = self._default_data()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _default_data(self):
        return {}

    def _key(self, grupo: str, stem: str) -> str:
        return f"{grupo}/{stem}"


class LinkRegistry(JsonRegistry):
    def add_origin(self, grupo: str, stem: str, ruta: Path, es_copia: bool = False) -> None:
        self._load()
        key = self._key(grupo, stem)
        entry = {"path": str(ruta), "copy": es_copia}
        if key not in self._data:
            self._data[key] = []
        if entry not in self._data[key]:
            self._data[key].append(entry)
            self._save()

    def remove_origin(self, grupo: str, stem: str, ruta: Path) -> None:
        self._load()
        key = self._key(grupo, stem)
        if key in self._data:
            self._data[key] = [e for e in self._data[key] if e["path"] != str(ruta)]
            if not self._data[key]:
                del self._data[key]
            self._save()

    def remove_all_origins(self, grupo: str, stem: str) -> None:
        self._load()
        key = self._key(grupo, stem)
        if key in self._data:
            del self._data[key]
            self._save()

    def get_origins(self, grupo: str, stem: str) -> List[Dict[str, Any]]:
        self._load()
        return self._data.get(self._key(grupo, stem), [])

    def rename(self, g_src: str, s_src: str, g_dst: str, s_dst: str) -> None:
        self._load()
        key_src = self._key(g_src, s_src)
        key_dst = self._key(g_dst, s_dst)
        if key_src in self._data:
            self._data[key_dst] = self._data.pop(key_src)
            self._save()


class InfoRegistry(JsonRegistry):
    def set(self, grupo: str, stem: str, texto: str) -> None:
        self._load()
        self._data[self._key(grupo, stem)] = texto.strip()
        self._save()

    def get(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        return self._data.get(self._key(grupo, stem))

    def remove(self, grupo: str, stem: str) -> None:
        self._load()
        self._data.pop(self._key(grupo, stem), None)
        self._save()

    def has(self, grupo: str, stem: str) -> bool:
        return self.get(grupo, stem) is not None


class GpgRegistry(JsonRegistry):
    def mark(self, grupo: str, stem: str, key_id: str) -> None:
        self._load()
        self._data[self._key(grupo, stem)] = key_id
        self._save()

    def unmark(self, grupo: str, stem: str) -> None:
        self._load()
        self._data.pop(self._key(grupo, stem), None)
        self._save()

    def is_protected(self, grupo: str, stem: str) -> bool:
        self._load()
        return self._key(grupo, stem) in self._data

    def key_id(self, grupo: str, stem: str) -> Optional[str]:
        self._load()
        return self._data.get(self._key(grupo, stem))

    def can_decrypt(self, grupo: str, stem: str) -> bool:
        """Verifica si la clave secreta correspondiente al evento está disponible."""
        key_id = self.key_id(grupo, stem)
        if not key_id:
            return False
        # Tomar la primera clave de la lista (por simplicidad)
        first_key = key_id.split(",")[0].strip()
        result = subprocess.run(
            ["gpg", "--list-secret-keys", "--with-colons", first_key],
            capture_output=True, text=True
        )
        return result.returncode == 0 and "sec:" in result.stdout

# ============================================================================
# ALMACENAMIENTO (case‑sensitive)
# ============================================================================

class ByteStorage:
    def __init__(self, base: Path):
        self.base = base
        byte_dir = base / ".byte"
        self.links = LinkRegistry(byte_dir / "links.json")
        self.info = InfoRegistry(byte_dir / "info.json")
        self.gpg = GpgRegistry(byte_dir / "gpg.json")

    def asegurar_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / ".byte").mkdir(parents=True, exist_ok=True)

    def normalize(self, txt: str) -> str:
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", txt) if unicodedata.category(c) != "Mn")

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
        for f in sorted(gp.iterdir()):
            if f.name.startswith("."):
                continue
            ext = f.suffix.lower()
            if ext in (".md", ".txt", ".py", ".sh", ".gpg"):
                stem = f.stem if ext != ".gpg" else Path(f.stem).stem
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
        gp = self.base / grupo
        if not gp.is_dir():
            return None
        for f in gp.iterdir():
            if f.name.startswith("."):
                continue
            ext = f.suffix.lower()
            if ext == ".gpg":
                if Path(f.stem).stem == stem:
                    return f
            elif ext in (".md", ".txt", ".py", ".sh"):
                if f.stem == stem:
                    return f
        return None

    def grupo_path(self, grupo: str) -> Path:
        return self.base / self.titulo(grupo)

    def evento_path(self, grupo: str, stem: str, ext: str = ".md") -> Path:
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

    # --- GPG helpers ---
    def leer_evento(self, grupo: str, stem: str) -> Optional[str]:
        path = self.get_evento_path(grupo, stem)
        if not path or not path.is_file():
            return None
        if path.suffix.lower() == ".gpg":
            try:
                tmp = self._gpg_decrypt_to_tmp(path)
                contenido = tmp.read_text(encoding="utf-8")
                tmp.unlink()
                return contenido
            except RuntimeError:
                return None
        return path.read_text(encoding="utf-8")

    def escribir_evento(self, grupo: str, stem: str, contenido: str,
                        key_id: Optional[str] = None, cifrar: bool = True) -> None:
        ev_path = self.get_evento_path(grupo, stem)
        if cifrar:
            if key_id is None:
                key_id = self.gpg.key_id(grupo, stem)
            debe_cifrar = key_id is not None
        else:
            debe_cifrar = False

        if not ev_path:
            ext = ".gpg" if debe_cifrar else ".md"
            ev_path = self.evento_path(grupo, stem, ext=ext)
        ev_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tf:
            tf.write(contenido)
            tmp_path = Path(tf.name)

        try:
            if debe_cifrar:
                self._gpg_encrypt(tmp_path, key_id, ev_path)
            else:
                shutil.copy2(tmp_path, ev_path)
        finally:
            tmp_path.unlink()

    def _gpg_encrypt(self, plain_path: Path, key_id: str, output_path: Path) -> None:
        # Si la clave contiene comas, son múltiples destinatarios
        if "," in key_id:
            keys = [k.strip() for k in key_id.split(",") if k.strip()]
            return self._gpg_encrypt_multiple(plain_path, keys, output_path)

        out = output_path if output_path.suffix == ".gpg" else output_path.with_suffix(output_path.suffix + ".gpg")
        res = subprocess.run(
            ["gpg", "--yes", "--batch", "--trust-model", "always",
             "-r", key_id, "-o", str(out), "-e", str(plain_path)],
            capture_output=True
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())
        if out != output_path:
            out.rename(output_path)

    def _gpg_encrypt_multiple(self, plain_path: Path, keys: List[str], output_path: Path) -> None:
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
        inner_ext = Path(path.stem).suffix or ".md"
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
# INTERFAZ DE USUARIO
# ============================================================================

class ByteInterface:
    def __init__(self, storage: ByteStorage):
        self.storage = storage

    def leer(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {C('tree')}(Interrumpido){C('rst')}")
            sys.exit(0)

    def calc_abreviaturas(self, lista: List[str], long: int) -> Dict[str, str]:
        abbr = {}
        usados: Set[str] = set()
        for item in lista:
            for i in range(len(item) - long + 1):
                sub = item[i:i+long]
                if sub not in usados:
                    abbr[item] = sub
                    usados.add(sub)
                    break
            else:
                for i in range(len(item) - long + 1):
                    sub = item[i:i+long]
                    abbr[item] = sub
                    break
        return abbr

    def render_ruta(self, grupo: str, stem: str) -> str:
        grupos = self.storage.get_grupos()
        g_abbr = self.calc_abreviaturas(grupos, 3)
        g_render = _resaltar(grupo, g_abbr.get(grupo), 3, C("group"), C("bold"))
        evs = self.storage.get_eventos(grupo)
        if stem not in evs:
            evs.append(stem)
        e_abbr = self.calc_abreviaturas(evs, 2)
        e_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))
        return f"{g_render}{C('tree')}/{C('rst')}{e_render}"

    def _fmt_origin(self, path_str: str) -> str:
        p = Path(path_str)
        parts = p.parts
        if len(parts) >= 2:
            return f"…/{parts[-2]}/{parts[-1]}"
        return path_str

    def print_arbol(self, grupos_filter: Optional[List[str]] = None, show_dates: bool = False) -> None:
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)")
            return

        tr = C("tree")
        r = C("rst")
        d = C("date")
        w = C("warn")
        max_name_len = 28

        g_abbr = self.calc_abreviaturas(grupos, 3)

        for gi, grupo in enumerate(grupos):
            evs = self.storage.get_eventos(grupo)
            ev_count = len(evs)
            if gi > 0:
                print()
            grupo_render = _resaltar(grupo, g_abbr.get(grupo), 3, C("group"), C("bold"))
            print(f"{tr}{grupo_render} {d}({ev_count}){r}")

            if not evs:
                continue

            e_abbr = self.calc_abreviaturas(evs, 2)
            for stem in evs:
                ev_path = self.storage.get_evento_path(grupo, stem)

                ext_str = ""
                display_stem = stem
                if ev_path:
                    real_ext = ev_path.suffix.lower()
                    if real_ext == ".gpg":
                        ext_str = ""
                    elif real_ext != ".md":
                        ext_str = f"{d}{real_ext}{r}"
                        display_stem = stem + real_ext

                event_render = _resaltar(stem, e_abbr.get(stem), 2, C("event"), C("bold"))

                # Badges: GPG (con ? si no se puede descifrar) e info
                badges = ""
                if self.storage.gpg.is_protected(grupo, stem):
                    if self.storage.gpg.can_decrypt(grupo, stem):
                        badges += f" {w}g{r}"
                    else:
                        badges += f" {w}g?{r}"
                if self.storage.info.has(grupo, stem):
                    badges += f" {w}i{r}"

                origins = self.storage.links.get_origins(grupo, stem)
                origins_str = ""
                if origins:
                    parts = []
                    for o in origins:
                        path = o["path"]
                        es_copia = o["copy"]
                        origen_fmt = self._fmt_origin(path)
                        disponible = Path(path).is_file()
                        if not disponible:
                            parts.append(f"{C('minus')}✗{r} {d}{origen_fmt}{r}")
                        elif es_copia:
                            parts.append(f"{d}c → {origen_fmt}{r}")
                        else:
                            parts.append(f"{d}→ {origen_fmt}{r}")
                    origins_str = f" {d}·{r} " + f"{d}, {r}".join(parts)

                fecha_str = ""
                if show_dates and ev_path:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        fecha_str = f" {d}{mt.strftime('%Y-%m-%d %H:%M')}{r}"

                padding = max(0, max_name_len - len(display_stem))
                line = f"  {event_render}{ext_str}{' ' * padding}{badges}{origins_str}{fecha_str}"
                print(line)

        print()

    def pedir_grupo(self, label: str = "Grupo") -> str:
        grupos = self.storage.get_grupos()
        self.print_arbol()
        while True:
            res = self.leer(f"{label}: ")
            if not res:
                return ""
            if res in grupos:
                return res
            g_abbr = self.calc_abreviaturas(grupos, 3)
            for g, ab in g_abbr.items():
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
            e_abbr = self.calc_abreviaturas(evs, 2)
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
            e_abbr = self.calc_abreviaturas(evs, 2)
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
        self.config = config
        self.storage = ByteStorage(config.base)
        self.ui = ByteInterface(self.storage)

    # --- Resolución de argumentos (case‑sensitive) ---
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
            grupo = self.find_grupo(g_raw)
            if grupo is None:
                grupo = self.storage.titulo(g_raw)
            ev_stem = Path(ev_raw).stem if Path(ev_raw).suffix in {".md", ".txt", ".py", ".sh"} else ev_raw
            return grupo, ev_stem
        m = re.match(r"^([^/]+)/$", arg)
        if m:
            grupo = self.find_grupo(m.group(1))
            if grupo is None:
                grupo = self.storage.titulo(m.group(1))
            return grupo, None
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
            if token in evs:
                return grupo, token
            e_abbr = self.ui.calc_abreviaturas(evs, 2)
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

    # --- Comandos ---
    def cmd_open(self, args: List[str]) -> None:
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
            sys.stdout.write(contenido)
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
                stem = Path(token).stem if Path(token).suffix in {".md", ".txt", ".py", ".sh"} else token
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

        ruta_fmt = self.ui.render_ruta(grupo, stem)

        if texto is not None:
            es_nuevo = not ev_path.is_file()
            contenido_actual = self.storage.leer_evento(grupo, stem) or ""
            nuevo = contenido_actual + texto + "\n"
            key_id = self.storage.gpg.key_id(grupo, stem) if self.storage.gpg.is_protected(grupo, stem) else None
            self.storage.escribir_evento(grupo, stem, nuevo, key_id=key_id, cifrar=bool(key_id))
            accion = "+" if es_nuevo else "~"
            print(f"{C('plus')}{accion} {ruta_fmt} {C('tree')}│{C('rst')} {texto}")
            return

        es_nuevo = not ev_path.is_file()
        if ev_path.suffix.lower() == ".gpg":
            try:
                tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                print(f"GPG error: {e}")
                return
            mtime_antes = tmp.stat().st_mtime
            os.system(f'{self.config.editor} "{tmp}"')
            if tmp.stat().st_mtime != mtime_antes:
                key_id = self.storage.gpg.key_id(grupo, stem)
                contenido = tmp.read_text(encoding="utf-8")
                try:
                    self.storage.escribir_evento(grupo, stem, contenido, key_id=key_id, cifrar=True)
                    print(f"{C('count')}~ {ruta_fmt}{C('rst')} (cifrado)")
                except Exception as e:
                    print(f"Error al guardar: {e}")
            else:
                tmp.unlink()
                print(f"{C('date')}  (sin cambios){C('rst')}")
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

    def cmd_link(self, args: List[str]) -> None:
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
            if "/" in segundo:
                partes = segundo.split("/", 1)
                grupo_hint = self.find_grupo(partes[0])
                if grupo_hint is None:
                    grupo_hint = self.storage.titulo(partes[0])
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

        if stem_override:
            p_override = Path(stem_override)
            if p_override.suffix in {".md", ".txt", ".py", ".sh"}:
                stem = p_override.stem
                ext = p_override.suffix
            else:
                stem = stem_override
                ext = ".md"
        else:
            stem = src.stem
            ext = src.suffix if src.suffix else ".md"

        ev_path = self.storage.get_evento_path(grupo, stem)
        ev_exists = ev_path is not None and ev_path.is_file()
        if not ev_exists:
            ev_path = self.storage.evento_path(grupo, stem, ext=ext)

        # Caso 1: origen no existe, evento sí
        if not src_exists and ev_exists:
            print(f"{C('date')}  El archivo externo no existe, se creará a partir del vault.{C('rst')}")
            if self.ui.leer(f"  Crear {src} desde {grupo}/{stem}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                return
            src_parent.mkdir(parents=True, exist_ok=True)
            contenido = self.storage.leer_evento(grupo, stem)
            if contenido is None:
                print("  Error al leer el evento")
                return
            src.write_text(contenido, encoding="utf-8")
            self.storage.links.add_origin(grupo, stem, src, es_copia=True)
            print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(str(src))} (copia desde vault){C('rst')}")
            return

        # Caso 2: origen existe, evento no
        if src_exists and not ev_exists:
            print(f"{C('date')}  El evento {grupo}/{stem} no existe, se creará desde el archivo externo.{C('rst')}")
            if self.ui.leer(f"  Crear {grupo}/{stem} desde {src}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                return
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, ev_path)
                metodo = "hardlink"
                es_copia = False
            except OSError:
                shutil.copy2(src, ev_path)
                metodo = "copia"
                es_copia = True
            self.storage.links.add_origin(grupo, stem, src, es_copia=es_copia)
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            print(f"{C('plus')}+ {ruta_fmt}{C('rst')}"
                  f"  {C('link')}→ {self.ui._fmt_origin(str(src))}  ({metodo}){C('rst')}")
            return

        # Caso 3: ambos existen
        if src_exists and ev_exists:
            origins = self.storage.links.get_origins(grupo, stem)
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
                    src.write_text(contenido, encoding="utf-8")
                    self.storage.links.add_origin(grupo, stem, src, es_copia=True)
                    print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} → {src} (actualizado){C('rst')}")
                else:
                    print("  Error al leer el vault")
            elif op == 'o':
                if self.ui.leer(f"  ¿Sobrescribir {ev_path} con el contenido de {src}? (s/{C('date')}N{C('rst')}): ").lower() != 's':
                    return
                contenido = src.read_text(encoding="utf-8")
                key_id = self.storage.gpg.key_id(grupo, stem) if self.storage.gpg.is_protected(grupo, stem) else None
                self.storage.escribir_evento(grupo, stem, contenido, key_id=key_id, cifrar=bool(key_id))
                self.storage.links.add_origin(grupo, stem, src, es_copia=False)
                print(f"{C('plus')}✓ {self.ui.render_ruta(grupo, stem)} actualizado desde {src}{C('rst')}")
            elif op == 'a':
                self.storage.links.add_origin(grupo, stem, src, es_copia=True)
                print(f"{C('plus')}+ {self.ui.render_ruta(grupo, stem)}{C('rst')}"
                      f"  {C('link')}→ {self.ui._fmt_origin(str(src))} (nuevo origen){C('rst')}")
            else:
                print(f"{C('date')}  Cancelado.{C('rst')}")
            return

        print(f"{C('minus')}  Ni el archivo externo ni el evento existen. Nada que hacer.{C('rst')}")

    def cmd_unlink(self, args: List[str]) -> None:
        if not args:
            enlazados = []
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    origins = self.storage.links.get_origins(g, stem)
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

        origins = self.storage.links.get_origins(grupo, stem)
        if not origins:
            print(f"  {self.ui.render_ruta(grupo, stem)}  {C('date')}(sin enlaces registrados){C('rst')}")
            return

        if len(origins) == 1:
            origen = origins[0]["path"]
            if self.ui.leer(f"  ¿Desenlazar {self.ui.render_ruta(grupo, stem)} de {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                self.storage.links.remove_origin(grupo, stem, Path(origen))
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
                    self.storage.links.remove_all_origins(grupo, stem)
                    print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}todos los enlaces eliminados{C('rst')}")
                return
            if op.isdigit():
                idx = int(op) - 1
                if 0 <= idx < len(origins):
                    origen = origins[idx]["path"]
                    if self.ui.leer(f"  ¿Desenlazar {origen}? (s/{C('date')}N{C('rst')}): ").lower() == 's':
                        self.storage.links.remove_origin(grupo, stem, Path(origen))
                        print(f"{C('minus')}  {self.ui.render_ruta(grupo, stem)}  {C('date')}desenlazado{C('rst')}")
                else:
                    print("  Número inválido.")

    def cmd_del(self, args: List[str]) -> None:
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
                        self.storage.links.remove_all_origins(grupo, ev)
                        self.storage.info.remove(grupo, ev)
                        self.storage.gpg.unmark(grupo, ev)
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
                self.storage.links.remove_all_origins(grupo, stem)
                self.storage.info.remove(grupo, stem)
                self.storage.gpg.unmark(grupo, stem)
                self.storage.trash(ev_path)
                print(f"{C('minus')}- {ruta_fmt}{C('rst')}")
        self.storage.limpiar_vacios()

    def _renombrar(self, grupo: str, origen: str, destino: str) -> None:
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
        self.storage.links.rename(grupo, origen, grupo, destino)
        info_txt = self.storage.info.get(grupo, origen)
        if info_txt:
            self.storage.info.set(grupo, destino, info_txt)
            self.storage.info.remove(grupo, origen)
        if self.storage.gpg.is_protected(grupo, origen):
            key_id = self.storage.gpg.key_id(grupo, origen)
            self.storage.gpg.mark(grupo, destino, key_id)
            self.storage.gpg.unmark(grupo, origen)
        print(f"{C('plus')}✓ Renombrado: {grupo}/{origen} → {grupo}/{destino}{C('rst')}")

    def _mover(self, g_src: str, e_src: str, g_dest: str, e_dest: str) -> None:
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
        origins = self.storage.links.get_origins(g_src, e_src)
        for o in origins:
            self.storage.links.add_origin(g_dest, e_dest, Path(o["path"]), o["copy"])
        self.storage.links.remove_all_origins(g_src, e_src)
        info_txt = self.storage.info.get(g_src, e_src)
        if info_txt:
            self.storage.info.set(g_dest, e_dest, info_txt)
            self.storage.info.remove(g_src, e_src)
        if self.storage.gpg.is_protected(g_src, e_src):
            key_id = self.storage.gpg.key_id(g_src, e_src)
            self.storage.gpg.mark(g_dest, e_dest, key_id)
            self.storage.gpg.unmark(g_src, e_src)
        self.storage.limpiar_vacios()
        r_src = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{C('plus')}✓ Movido: {r_src} ➔ {r_dest}{C('rst')}")

    def _fusionar(self, g_dest: str, e_dest: str, g_src: str, e_src: str) -> None:
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
        contenido_src = p_src.read_text(encoding="utf-8")
        if p_dest_real.is_file():
            contenido_dest = p_dest_real.read_text(encoding="utf-8")
            sep = "\n\n---\n\n" if contenido_dest.strip() else ""
            p_dest_real.write_text(contenido_dest + sep + contenido_src, encoding="utf-8")
        else:
            p_dest_real.write_text(contenido_src, encoding="utf-8")
        src_origins = self.storage.links.get_origins(g_src, e_src)
        for o in src_origins:
            self.storage.links.add_origin(g_dest, e_dest, Path(o["path"]), o["copy"])
        self.storage.links.remove_all_origins(g_src, e_src)
        self.storage.info.remove(g_src, e_src)
        self.storage.gpg.unmark(g_src, e_src)
        p_src.unlink()
        self.storage.limpiar_vacios()
        r_src = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{C('plus')}✓ Fusionado: {r_src} ➔ {r_dest}{C('rst')}")

    def cmd_mv(self, args: List[str]) -> None:
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
            print("Uso: byte --mv [destino] [fuente]  |  byte --mv [origen] [Grupo/]")
            return

        a1, a2 = args[0], args[1]

        g1, e1 = self.parse_arg(a1)
        g2, e2 = self.parse_arg(a2)
        if g1 and e1 and g2 and e2 and g1 == g2 and self.storage.normalize(e1) == self.storage.normalize(e2) and e1 != e2:
            self._renombrar(g1, e1, e2)
            return

        if a2.endswith("/"):
            g_src, e_src = self.resolver_arg(a1)
            g_dest = self.find_grupo(a2.rstrip("/"))
            if g_dest is None:
                g_dest = self.storage.titulo(a2.rstrip("/"))
            if not g_src or not e_src:
                print(f"Origen no encontrado: '{a1}'")
                return
            self._mover(g_src, e_src, g_dest, e_src)
            return

        if "/" in a2:
            g_src, e_src = self.resolver_arg(a1)
            g_dest, e_dest = self.resolver_arg(a2)
            if not g_src or not e_src:
                print(f"Origen no encontrado: '{a1}'")
                return
            if not g_dest:
                partes = a2.split("/")
                g_dest = self.storage.titulo(partes[0])
                e_dest = partes[1]
            if not e_dest:
                e_dest = e_src
            p_dest = self.storage.get_evento_path(g_dest, e_dest)
            if p_dest and p_dest.is_file():
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                self._mover(g_src, e_src, g_dest, e_dest)
            return

        g_dest, e_dest = self.resolver_arg(a1)
        g_src, e_src = self.resolver_arg(a2)
        if not g_dest or not e_dest:
            print(f"Destino no encontrado: '{a1}'")
            return
        if not g_src or not e_src:
            print(f"Fuente no encontrado: '{a2}'")
            return
        self._fusionar(g_dest, e_dest, g_src, e_src)

    def cmd_gpg(self, args: List[str]) -> None:
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
            key_actual = self.storage.gpg.key_id(grupo, stem) or self.config.gpg_key
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
            self.storage.gpg.mark(grupo, stem, ",".join(todos_keys))
            print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} → {'  '.join(todos_keys)}")
            return

        if not self.config.gpg_key:
            print(f"{w}  Sin llave primaria configurada.{r}")
            print(f"  Configúrala con {d}byte x{r} o añade {d}gpg_key = 'tu@correo'{r} en byte.toml")
            return
        if extra_keys:
            print(f"  {d}Nota: para cifrar por primera vez se usa la configuración de byte.toml.{r}")
            print(f"  {d}Para añadir destinatarios a un archivo ya cifrado, vuelve a ejecutar byte g.{r}")

        all_keys = [self.config.gpg_key] + [k for k in self.config.gpg_keys_secondary if k != self.config.gpg_key]

        if not ev_path or not ev_path.is_file():
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            ev_path.touch()

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

        self.storage.gpg.mark(grupo, stem, ",".join(all_keys))
        origins = self.storage.links.get_origins(grupo, stem)
        if origins:
            for o in origins:
                self.storage.links.add_origin(grupo, stem, Path(o["path"]), es_copia=True)
        prim_fmt = f"{w}{self.config.gpg_key}{r}"
        sec_fmt = ("  " + "  ".join(f"{d}{k}{r}" for k in all_keys[1:])) if all_keys[1:] else ""
        print(f"{C('plus')}~ {ruta_fmt}{r}  {w}g{r} {prim_fmt}{sec_fmt}")

    def cmd_nogpg(self, args: List[str]) -> None:
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
        self.storage.gpg.unmark(grupo, stem)
        print(f"{C('plus')}~ {self.ui.render_ruta(grupo, stem)}{C('rst')} (descifrado, sin protección GPG)")

    def cmd_check(self, args: List[str]) -> None:
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
        print(f"Directorio: {self.storage.base}")
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

        candidatos = []
        for g in self.storage.get_grupos():
            for stem in self.storage.get_eventos(g):
                ev_path = self.storage.get_evento_path(g, stem)
                if not ev_path:
                    continue
                for o in self.storage.links.get_origins(g, stem):
                    candidatos.append((g, stem, ev_path, o["path"], o["copy"]))

        if not candidatos:
            print(f"{d}No hay enlaces registrados.{r}")
            return

        cambios_entrantes = []
        salientes = []

        for g, stem, ev_path, origin_str, es_copia in candidatos:
            src = Path(origin_str)
            if not src.is_file():
                print(f"{d}{g}/{stem} → origen no disponible: {self.ui._fmt_origin(origin_str)} (omitido){r}")
                continue

            es_gpg = ev_path.suffix.lower() == ".gpg"
            contenido_ev = self.storage.leer_evento(g, stem)
            if contenido_ev is None:
                print(f"{w}{g}/{stem} — no se pudo descifrar (GPG), omitido.{r}")
                continue
            contenido_src = src.read_text(encoding="utf-8")

            if contenido_ev != contenido_src:
                cambios_entrantes.append((g, stem, ev_path, src, es_gpg, es_copia))
            else:
                salientes.append((g, stem, ev_path, src, es_copia))

        for g, stem, ev_path, src, es_gpg, es_copia in cambios_entrantes:
            if not es_copia:
                continue
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt = self.ui.render_ruta(g, stem)
            gpg_tag = f" {w}g{r}" if es_gpg else ""
            print(f"\n{C('bold')}{ruta_fmt}{r}{gpg_tag}"
                  f"  {C('link')}c → {origen_fmt}{r}"
                  f"  {d}(origen modificado){r}")
            while True:
                res = self.ui.leer("  ¿[s]incronizar origen→evento, [d]iff, [n]o? (s/d/n): ").lower()
                if res == "d":
                    if es_gpg:
                        tmp = self.storage._gpg_decrypt_to_tmp(ev_path)
                        mostrar_diff(tmp, src)
                        tmp.unlink()
                    else:
                        mostrar_diff(ev_path, src)
                elif res == "s":
                    if es_gpg:
                        key_id = self.storage.gpg.key_id(g, stem)
                        # Dividir en múltiples claves si es necesario
                        keys = [k.strip() for k in key_id.split(",") if k.strip()]
                        # Verificar que todas las claves públicas existan
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
                        self.storage._gpg_encrypt_multiple(inner, keys, ev_path)
                        print(f"{C('plus')}  ✓ Actualizado y re-cifrado desde {origen_fmt}{r}")
                    else:
                        shutil.copy2(src, ev_path)
                        print(f"{C('plus')}  ✓ Actualizado desde {origen_fmt}{r}")
                    break
                else:
                    print(f"{d}  Omitido{r}")
                    break

        for g, stem, ev_path, src, es_copia in salientes:
            if not es_copia:
                continue
            contenido_ev = self.storage.leer_evento(g, stem)
            if contenido_ev is None:
                continue
            contenido_src = src.read_text(encoding="utf-8")
            if contenido_ev == contenido_src:
                continue
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt = self.ui.render_ruta(g, stem)
            print(f"\n{C('bold')}{ruta_fmt}{r}"
                  f"  {C('link')}→ {origen_fmt}{r}"
                  f"  {d}(evento modificado){r}")
            while True:
                res = self.ui.leer("  ¿[s]incronizar evento→origen, [d]iff, [n]o? (s/d/n): ").lower()
                if res == "d":
                    mostrar_diff(src, ev_path)
                elif res == "s":
                    shutil.copy2(ev_path, src)
                    print(f"{C('plus')}  ✓ Origen actualizado: {origen_fmt}{r}")
                    break
                else:
                    print(f"{d}  Omitido{r}")
                    break

        print(f"{d}Revisión completada.{r}")

    def cmd_info(self, args: List[str]) -> None:
        if not args:
            encontrado = False
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    txt = self.storage.info.get(g, stem)
                    if txt:
                        ruta_fmt = self.ui.render_ruta(g, stem)
                        print(f"  {ruta_fmt}  {C('date')}{txt}{C('rst')}")
                        encontrado = True
            if not encontrado:
                print(f"{C('date')}  (ningún evento tiene info){C('rst')}")
            return

        grupo, stem = self.resolver_arg(args[0])
        if not grupo:
            stem = args[0]
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo:
                return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem:
                return

        ruta_fmt = self.ui.render_ruta(grupo, stem)
        d = C("date")
        r = C("rst")
        w = C("warn")

        if len(args) >= 2:
            texto = " ".join(args[1:])
            self.storage.info.set(grupo, stem, texto)
            print(f"{d}Nota guardada para {ruta_fmt}: {texto}{r}")
            return

        txt = self.storage.info.get(grupo, stem)
        if txt:
            print(f"{d}{txt}{r}")

        info_lines = []
        if self.storage.gpg.is_protected(grupo, stem):
            info_lines.append(f"{w}Cifrado: {self.storage.gpg.key_id(grupo, stem)}{r}")
        origins = self.storage.links.get_origins(grupo, stem)
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

    def cmd_config(self, args: List[str]) -> None:
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

        lines = [f'base   = "{nueva_base}"', f'editor = "{nuevo_editor}"']
        if nueva_primaria:
            lines.append(f'gpg_key = "{nueva_primaria}"')
        lines.append(f'gpg_keys_secondary = [{", ".join(f"\"{k}\"" for k in nuevas_sec)}]' if nuevas_sec else 'gpg_keys_secondary = []')
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
            self.config.used_config_path = target
            self.storage = ByteStorage(self.config.base)
            self.ui = ByteInterface(self.storage)
            if nueva_base != self.config.base:
                print(f"{w}Cambio de BASE aplicado (recreado el storage).{r}")
        else:
            print(f"{d}Cancelado{r}")

    def cmd_complete(self, args: List[str]) -> None:
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self) -> None:
        h = C("header")
        t = C("tree")
        d = C("date")
        r = C("rst")
        w = C("warn")
        print(f"{h}BYTE — Notas en Markdown{r}\n")
        print(f"  {t}byte{r}              {d}árbol{r}")
        print(f"  {t}byte -t{r}           {d}árbol con fechas{r}")
        print(f"  {t}byte -h{r}           {d}esta ayuda{r}")
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
        print(f"  {t}--info    {d}i{r}  {d}[evento] [texto]{r}    nota corta asociada al evento")
        print(f"  {t}--gpg     {d}g{r}  evento {d}[llave]{r}      cifra con primaria+secundarias; sobre cifrado: añade secundaria")
        print(f"  {t}--nogpg   {d}q{r}  evento              descifra y elimina protección GPG")
        print(f"  {t}--check   {d}c{r}                      muestra configuración y sincroniza copias/enlaces")
        print(f"  {t}--config  {d}x{r}                      configuración inicial")
        print()
        print(f"  {h}Indicadores en el árbol{r}")
        print(f"  {w}g{r} gpg (o {w}g?{r} sin clave)   {w}i{r} info   {d}→{r} hardlink   {d}c →{r} copia   {d}✗{r} enlace roto")

# ============================================================================
# PUNTO DE ENTRADA
# ============================================================================

def main() -> None:
    config = Config()
    app = ByteApp(config)
    app.storage.asegurar_base()

    args = sys.argv[1:]

    if not args:
        app.ui.print_arbol()
        return

    cmd = args[0]
    rest = args[1:]

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
