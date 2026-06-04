#!/usr/bin/env python3
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

try:
    import tomllib          # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# ===== CONFIGURACIÓN =====
CONFIG_DIR  = Path.home() / ".config" / "byte"
CONFIG_PATH = CONFIG_DIR / "byte.toml"

def _load_config():
    if not CONFIG_PATH.is_file():
        return {}
    if tomllib is None:
        print("\033[38;5;243mAviso: byte.toml encontrado pero sin parser TOML "
              "(Python < 3.11 y tomli no instalado). Usando valores por defecto.\033[0m",
              file=sys.stderr)
        return {}
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)

_CFG = _load_config()

def _cfg_path(key, default):
    raw = _CFG.get(key)
    if raw is None:
        return Path(str(default)).expanduser()
    return Path(raw).expanduser()

BASE   = _cfg_path("base",  Path.home() / "Documentos/Filen/Obsidian/bytes")
LINKS  = _cfg_path("links", CONFIG_DIR / "links.json")
INFO   = _cfg_path("info",  CONFIG_DIR / "info.json")
EDITOR = _CFG.get("editor") or os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")
GPG_KEY= _CFG.get("gpg_key", "")

TEXTO_PLANO = {
    ".md", ".txt", ".csv", ".tsv", ".log", ".org", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".css", ".js", ".py", ".sh", ".lua", ".rb", ".go", ".rs",
}


# ===== COLORES =====
class ByteColor:
    def __init__(self):
        self.codes = {
            "rst":    self._ansi("0"),
            "bold":   self._ansi("1;37"),
            "event":  self._ansi("0;37"),
            "header": self._ansi("1;37"),
            "plus":   self._ansi("1;37"),
            "minus":  self._ansi("38;5;243"),
            "tree":   self._ansi("38;5;239"),
            "count":  self._ansi("1;37"),
            "date":   self._ansi("38;5;243"),
            "link":   self._ansi("38;5;245"),
            "warn":   self._ansi("33"),
        }

    def _ansi(self, code): return f"\033[{code}m"
    def get(self, key):    return self.codes.get(key, "")


# ===== DIFF HELPER (compartido) =====
def _diff_tool():
    for t in ["delta", "bat"]:
        if shutil.which(t): return t
    return None

def mostrar_diff(a, b):
    tool = _diff_tool()
    if tool == "delta":
        os.system(f'diff -u "{a}" "{b}" | delta')
    elif tool == "bat":
        r = subprocess.run(["diff", "-u", str(a), str(b)], capture_output=True, text=True)
        if r.stdout:
            tmp = tempfile.NamedTemporaryFile(suffix=".diff", delete=False, mode="w")
            tmp.write(r.stdout); tmp.close()
            os.system(f'bat --language=diff "{tmp.name}"')
            Path(tmp.name).unlink()
        else:
            print("  (sin diferencias)")
    else:
        os.system(f'diff -u "{a}" "{b}"')


# ===== REGISTRO DE HARDLINKS =====
class LinkRegistry:
    """Clave: "Grupo/stem"  →  {"path": str, "copy": bool}
    No usa inodos: sobrevive a cifrado GPG, mv y cualquier reescritura."""

    def __init__(self, base, links_path=None):
        self.path  = Path(links_path) if links_path else Path(base) / ".links"
        self._data = None

    def _key(self, grupo, stem):
        return f"{grupo}/{stem}"

    def _load(self):
        if self._data is None:
            if self.path.is_file():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    # migración: si las claves son numéricas (inodos viejos) descartamos
                    self._data = {k: v for k, v in raw.items() if not k.isdigit()}
                except Exception:
                    self._data = {}
            else:
                self._data = {}

    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def register(self, grupo: str, stem: str, ruta: Path, es_copia: bool = False):
        self._load()
        self._data[self._key(grupo, stem)] = {"path": str(ruta), "copy": es_copia}
        self._save()

    def get(self, grupo: str, stem: str):
        self._load()
        entry = self._data.get(self._key(grupo, stem))
        if entry is None: return None
        return entry["path"] if isinstance(entry, dict) else entry

    def is_copy(self, grupo: str, stem: str) -> bool:
        self._load()
        entry = self._data.get(self._key(grupo, stem))
        if entry is None: return False
        return entry.get("copy", False) if isinstance(entry, dict) else False

    def remove(self, grupo: str, stem: str):
        self._load()
        self._data.pop(self._key(grupo, stem), None)
        self._save()

    def is_registered(self, grupo: str, stem: str) -> bool:
        self._load()
        return self._key(grupo, stem) in self._data

    def origin(self, grupo: str, stem: str):
        return self.get(grupo, stem)

    def rename(self, g_src, s_src, g_dest, s_dest):
        """Mueve el registro al renombrar/mover un evento."""
        self._load()
        entry = self._data.pop(self._key(g_src, s_src), None)
        if entry:
            self._data[self._key(g_dest, s_dest)] = entry
            self._save()


# ===== REGISTRO DE INFO =====
class InfoRegistry:
    def __init__(self, info_path):
        self.path  = Path(info_path)
        self._data = None

    def _key(self, grupo, stem): return f"{grupo}/{stem}"

    def _load(self):
        if self._data is None:
            if self.path.is_file():
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    self._data = {}
            else:
                self._data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def set(self, grupo, stem, texto):
        self._load()
        self._data[self._key(grupo, stem)] = texto.strip()
        self._save()

    def get(self, grupo, stem):
        self._load()
        return self._data.get(self._key(grupo, stem))

    def remove(self, grupo, stem):
        self._load()
        self._data.pop(self._key(grupo, stem), None)
        self._save()

    def has(self, grupo, stem):
        self._load()
        return self._key(grupo, stem) in self._data


# ===== REGISTRO GPG =====
class GpgRegistry:
    """Persiste qué eventos están marcados como protegidos con GPG."""
    def __init__(self, config_dir):
        self.path  = Path(config_dir) / "gpg.json"
        self._data = None

    def _load(self):
        if self._data is None:
            if self.path.is_file():
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    self._data = {}
            else:
                self._data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _key(self, grupo, stem): return f"{grupo}/{stem}"

    def mark(self, grupo, stem, key_id: str):
        self._load()
        self._data[self._key(grupo, stem)] = key_id
        self._save()

    def unmark(self, grupo, stem):
        self._load()
        self._data.pop(self._key(grupo, stem), None)
        self._save()

    def is_protected(self, grupo, stem) -> bool:
        self._load()
        return self._key(grupo, stem) in self._data

    def key_id(self, grupo, stem):
        self._load()
        return self._data.get(self._key(grupo, stem))


# ===== ALMACENAMIENTO =====
class ByteStorage:
    def __init__(self, base_path, links_path=None, info_path=None, config_dir=None):
        self.base    = Path(base_path)
        self.links   = LinkRegistry(self.base, links_path=links_path)
        self.info    = InfoRegistry(info_path or (CONFIG_DIR / "info.json"))
        self.gpg     = GpgRegistry(config_dir or CONFIG_DIR)

    def asegurar_base(self):
        self.base.mkdir(parents=True, exist_ok=True)
        self.links.path.parent.mkdir(parents=True, exist_ok=True)

    def normalize(self, txt):
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", txt)
                       if unicodedata.category(c) != "Mn")

    def titulo(self, txt):
        return txt.strip().capitalize()

    def get_grupos(self):
        if not self.base.is_dir(): return []
        return sorted([d.name for d in self.base.iterdir()
                       if d.is_dir() and not d.name.startswith(".")])

    def get_eventos(self, grupo):
        gp = self.base / grupo
        if not gp.is_dir(): return []
        resultado = []
        for f in sorted(gp.iterdir()):
            if f.name.startswith("."): continue
            ext = f.suffix.lower()
            if ext in TEXTO_PLANO or ext == ".gpg":
                resultado.append(f.stem if ext != ".gpg" else Path(f.stem).stem)
        return list(dict.fromkeys(resultado))  # dedup preservando orden

    def get_evento_path(self, grupo, stem):
        gp = self.base / grupo
        if not gp.is_dir(): return None
        # buscar primero .gpg cifrado, luego texto plano
        for f in gp.iterdir():
            if f.name.startswith("."): continue
            # archivo cifrado: stem.md.gpg → stem
            if f.suffix.lower() == ".gpg" and Path(f.stem).stem == stem:
                return f
            if f.stem == stem and f.suffix.lower() in TEXTO_PLANO:
                return f
        return None

    def grupo_path(self, grupo):
        return self.base / self.titulo(grupo)

    def evento_path(self, grupo, stem, ext=".md"):
        return self.base / self.titulo(grupo) / f"{stem}{ext}"

    def trash(self, path):
        if not path.exists(): return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        shutil.move(str(path), str(dest))

    def mtime(self, path):
        if not path or not path.is_file(): return None
        return datetime.fromtimestamp(path.stat().st_mtime)

    def limpiar_vacios(self):
        for g in self.get_grupos():
            gp = self.grupo_path(g)
            if gp.is_dir() and not any(f for f in gp.iterdir() if not f.name.startswith(".")):
                gp.rmdir()


# ===== INTERFAZ VISUAL =====
class ByteInterface:
    def __init__(self, storage, color):
        self.storage = storage
        self.c       = color

    def leer(self, prompt):
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {self.c.get('tree')}(Interrumpido){self.c.get('rst')}\n")
            sys.exit(0)

    def calc_abreviaturas(self, lista, longitud):
        abbrevs = {}
        usados  = set()
        for item in lista:
            plano = self.storage.normalize(item)
            asignado = False
            for i in range(len(plano) - longitud + 1):
                sub = plano[i:i+longitud]
                if " " not in sub and sub not in usados:
                    abbrevs[item] = sub
                    usados.add(sub)
                    asignado = True
                    break
            if not asignado:
                for i in range(len(plano) - longitud + 1):
                    sub = plano[i:i+longitud]
                    if " " not in sub:
                        abbrevs[item] = sub
                        break
        return abbrevs

    def _render_label(self, nombre, abbrev, longitud):
        c     = self.c
        color = c.get("event")
        if not abbrev:
            return color + nombre + c.get("rst")
        plano = "".join(ch for ch in unicodedata.normalize("NFKD", nombre.lower())
                        if unicodedata.category(ch) != "Mn")
        idx = plano.find(abbrev)
        if idx != -1:
            pre  = nombre[:idx]
            lbl  = nombre[idx:idx+longitud]
            post = nombre[idx+longitud:]
            return (f"{color}{pre}{c.get('rst')}"
                    f"{c.get('bold')}{lbl}{c.get('rst')}"
                    f"{color}{post}{c.get('rst')}")
        return f"{color}{nombre} {c.get('bold')}{abbrev}{c.get('rst')}"

    def render_ruta(self, grupo, stem):
        grupos    = self.storage.get_grupos()
        g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
        g_render  = self._render_label(grupo, g_abbrevs.get(grupo), longitud=3)
        evs       = self.storage.get_eventos(grupo)
        if stem not in evs: evs = evs + [stem]
        e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
        e_render  = self._render_label(stem, e_abbrevs.get(stem), longitud=2)
        return f"{g_render}{self.c.get('tree')}/{self.c.get('rst')}{e_render}"

    def _fmt_origin(self, path_str):
        try:
            return "~/" + str(Path(path_str).relative_to(Path.home()))
        except ValueError:
            return path_str

    def print_arbol(self, grupos_filter=None, show_dates=False):
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)"); return

        g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)

        for gi, grupo in enumerate(grupos):
            is_last_g = gi == len(grupos) - 1
            g_pref    = "└── " if is_last_g else "├── "
            print(f"{self.c.get('tree')}{g_pref}"
                  f"{self._render_label(grupo + '/', g_abbrevs.get(grupo), longitud=3)}")

            evs       = self.storage.get_eventos(grupo)
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)

            for ei, stem in enumerate(evs):
                is_last_e = ei == len(evs) - 1
                pad       = "    " if is_last_g else "│   "
                e_pref    = "└── " if is_last_e else "├── "

                ev_path = self.storage.get_evento_path(grupo, stem)
                origin  = self.storage.links.origin(grupo, stem)

                # extensión si no es .md (o si es .gpg)
                ext_str = ""
                if ev_path:
                    real_ext = ev_path.suffix.lower()
                    if real_ext == ".gpg":
                        ext_str = f"{self.c.get('date')}.gpg{self.c.get('rst')}"
                    elif real_ext != ".md":
                        ext_str = f"{self.c.get('date')}{real_ext}{self.c.get('rst')}"

                # indicadores
                flags = ""
                if self.storage.gpg.is_protected(grupo, stem):
                    flags += f" {self.c.get('warn')}g{self.c.get('rst')}"
                if self.storage.info.has(grupo, stem):
                    flags += f" {self.c.get('date')}i{self.c.get('rst')}"
                if origin:
                    es_copia    = self.storage.links.is_copy(grupo, stem)
                    origen_fmt  = self._fmt_origin(origin)
                    disponible  = Path(origin).is_file()
                    if not disponible:
                        # enlace roto: ruta en rojo tenue + ✗
                        flags += f" {self.c.get('minus')}✗ → {origen_fmt}{self.c.get('rst')}"
                    elif es_copia:
                        flags += f" {self.c.get('date')}c{self.c.get('rst')} {self.c.get('link')}→ {origen_fmt}{self.c.get('rst')}"
                    else:
                        flags += f" {self.c.get('link')}→ {origen_fmt}{self.c.get('rst')}"
                if show_dates:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        flags += f"  {self.c.get('date')}{mt.strftime('%Y-%m-%d %H:%M')}{self.c.get('rst')}"

                print(f"{self.c.get('tree')}{pad}{e_pref}"
                      f"{self._render_label(stem, e_abbrevs.get(stem), longitud=2)}"
                      f"{ext_str}{flags}{self.c.get('rst')}")

    def pedir_grupo(self, label="Grupo"):
        grupos = self.storage.get_grupos()
        self.print_arbol()
        while True:
            res = self.leer(f"{label}: ")
            if not res: return ""
            g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
            for g in grupos:
                if (g_abbrevs.get(g) == self.storage.normalize(res) or
                        self.storage.normalize(g) == self.storage.normalize(res)):
                    return g
            return self.storage.titulo(res)

    def pedir_evento(self, grupo, label="Evento"):
        evs = self.storage.get_eventos(grupo)
        if evs:
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            print(f"\n  Eventos en {grupo}:")
            for e in evs:
                print(f"    {self._render_label(e, e_abbrevs.get(e), longitud=2)}")
            print()
        while True:
            res = self.leer(f"{label}: ")
            if not res: return ""
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            for e in evs:
                if (e_abbrevs.get(e) == res.lower() or
                        self.storage.normalize(e) == res.lower()):
                    return e
            return res.lower()


# ===== NÚCLEO =====
class ByteApp:
    def __init__(self, base_path, editor_name, links_path=None, info_path=None):
        self.storage = ByteStorage(base_path, links_path=links_path, info_path=info_path)
        self.c       = ByteColor()
        self.ui      = ByteInterface(self.storage, self.c)
        self.editor  = editor_name
        self.gpg_key = GPG_KEY

    # ----- resolución -----

    def find_grupo(self, token):
        token_p   = self.storage.normalize(token)
        grupos    = self.storage.get_grupos()
        g_abbrevs = self.ui.calc_abreviaturas(grupos, longitud=3)
        for g in grupos:
            if g_abbrevs.get(g) == token_p or self.storage.normalize(g) == token_p:
                return g
        return None

    def parse_arg(self, arg):
        if not arg: return None, None
        m = re.match(r"^([^/]+)/(.+)$", arg)
        if m:
            g_raw, ev_raw = m.group(1), m.group(2)
            grupo = self.find_grupo(g_raw) or self.storage.titulo(g_raw)
            evs   = self.storage.get_eventos(grupo)
            e_abb = self.ui.calc_abreviaturas(evs, longitud=2)
            ev_stem = Path(ev_raw).stem if Path(ev_raw).suffix.lower() in TEXTO_PLANO else ev_raw
            for e in evs:
                if e_abb.get(e) == ev_stem.lower() or self.storage.normalize(e) == ev_stem.lower():
                    return grupo, e
            return grupo, ev_stem.lower()
        m = re.match(r"^([^/]+)/$", arg)
        if m:
            return self.find_grupo(m.group(1)) or self.storage.titulo(m.group(1)), None
        return None, arg

    def resolver_arg(self, arg):
        g, e = self.parse_arg(arg)
        if g and e: return g, e
        token = e or g
        if not token: return None, None
        token_path = Path(token)
        token_stem = token_path.stem if token_path.suffix.lower() in TEXTO_PLANO else token
        grupos    = self.storage.get_grupos()
        g_abbrevs = self.ui.calc_abreviaturas(grupos, longitud=3)
        for g_item in grupos:
            evs   = self.storage.get_eventos(g_item)
            e_abb = self.ui.calc_abreviaturas(evs, longitud=2)
            for e_item in evs:
                if (e_abb.get(e_item) == token_stem.lower() or
                        self.storage.normalize(e_item) == self.storage.normalize(token_stem)):
                    return g_item, e_item
        for g_item in grupos:
            if (g_abbrevs.get(g_item) == token_stem.lower() or
                    self.storage.normalize(g_item) == self.storage.normalize(token_stem)):
                return g_item, None
        return None, token_stem

    # ----- GPG helpers -----

    def _gpg_encrypt(self, path: Path, key_id: str) -> Path:
        """Cifra path → path.gpg, borra el original. Devuelve path.gpg."""
        out = Path(str(path) + ".gpg")
        r = subprocess.run(
            ["gpg", "--yes", "--batch", "-r", key_id, "-o", str(out), "-e", str(path)],
            capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode())
        path.unlink()
        return out

    def _gpg_decrypt_to_tmp(self, path: Path) -> Path:
        """Descifra path.gpg a un archivo temporal. Devuelve el Path temporal."""
        # Recuperar extensión original: stem.md.gpg → stem.md
        inner_ext = Path(path.stem).suffix or ".md"
        tmp = tempfile.NamedTemporaryFile(suffix=inner_ext, delete=False)
        tmp.close()
        tmp_path = Path(tmp.name)
        r = subprocess.run(
            ["gpg", "--yes", "--batch", "-o", str(tmp_path), "-d", str(path)],
            capture_output=True)
        if r.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(r.stderr.decode())
        return tmp_path

    # ----- comandos -----

    def cmd_open(self, args):
        """Abre/crea evento. Con texto extra → append al final."""
        if not args:
            grupo = self.ui.pedir_grupo()
            if not grupo: return
            stem  = self.ui.pedir_evento(grupo)
            if not stem: return
            texto = None
        else:
            token = args[0]
            texto = " ".join(args[1:]) if len(args) > 1 else None
            grupo, stem = self.resolver_arg(token)
            if not grupo:
                stem  = Path(token).stem if Path(token).suffix.lower() in TEXTO_PLANO else token.lower()
                grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
                if not grupo: return
            if not stem:
                stem = self.ui.pedir_evento(grupo, "Evento")
                if not stem: return

        ev_path = self.storage.get_evento_path(grupo, stem)
        if ev_path is None:
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")

        ruta_fmt = self.ui.render_ruta(grupo, stem)

        # modo append
        if texto is not None:
            es_nuevo = not ev_path.is_file()
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            target = ev_path
            # si está cifrado, descifrar primero
            if ev_path.suffix.lower() == ".gpg":
                tmp = self._gpg_decrypt_to_tmp(ev_path)
                with open(tmp, "a", encoding="utf-8") as f:
                    f.write(texto + "\n")
                key_id = self.storage.gpg.key_id(grupo, stem)
                self._gpg_encrypt(tmp, key_id)
                ev_path.unlink(missing_ok=True)
                # re-cifrar
                inner = Path(str(tmp).rstrip(".gpg"))
                shutil.copy2(tmp, inner); tmp.unlink(missing_ok=True)
                self._gpg_encrypt(inner, key_id)
            else:
                with open(target, "a", encoding="utf-8") as f:
                    f.write(texto + "\n")
            accion = "+" if es_nuevo else "~"
            print(f"{self.c.get('plus')}{accion} {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} {texto}")
            return

        # modo editor
        es_nuevo = not ev_path.is_file()

        # si está cifrado
        if ev_path.suffix.lower() == ".gpg":
            try:
                tmp = self._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                return print(f"GPG error: {e}")
            mtime_antes = tmp.stat().st_mtime
            os.system(f'{self.editor} "{tmp}"')
            if tmp.stat().st_mtime != mtime_antes:
                key_id = self.storage.gpg.key_id(grupo, stem)
                ev_path.unlink()
                inner_ext = Path(ev_path.stem).suffix or ".md"
                inner = ev_path.parent / (stem + inner_ext)
                shutil.move(str(tmp), str(inner))
                self._gpg_encrypt(inner, key_id)
                print(f"{self.c.get('count')}~ {ruta_fmt}{self.c.get('rst')} (cifrado)")
            else:
                tmp.unlink(missing_ok=True)
                print(f"{self.c.get('date')}  (sin cambios){self.c.get('rst')}")
            return

        ev_path.parent.mkdir(parents=True, exist_ok=True)
        ev_path.touch()
        os.system(f'{self.editor} "{ev_path}"')

        if es_nuevo and ev_path.stat().st_size == 0:
            ev_path.unlink()
            print(f"{self.c.get('minus')}(Archivo vacío descartado){self.c.get('rst')}")
        else:
            accion = f"{self.c.get('plus')}+" if es_nuevo else f"{self.c.get('count')}~"
            print(f"{accion} {ruta_fmt}{self.c.get('rst')}")

    def cmd_link(self, args):
        """byte --link archivo [nombre]
        Un argumento: usa el mismo nombre del archivo.
        Dos argumentos: usa el segundo como nombre del evento.
        """
        if not args:
            archivo = self.ui.leer("Archivo a enlazar: ")
            if not archivo: return
            grupo_hint, stem_override = None, ""
        elif len(args) == 1:
            archivo = args[0]
            grupo_hint, stem_override = None, ""
        else:
            archivo = args[0]
            segundo = args[1]
            # Segundo arg puede ser Grupo/stem, Grupo/ o solo stem
            if "/" in segundo:
                partes = segundo.split("/", 1)
                grupo_hint    = self.find_grupo(partes[0]) or self.storage.titulo(partes[0])
                stem_override = partes[1] if partes[1] else ""
            else:
                grupo_hint    = None
                stem_override = segundo

        src = Path(archivo).expanduser().resolve()
        if not src.is_file():
            return print(f"No existe: {src}")

        # Determinar stem y ext finales
        if stem_override:
            p_override = Path(stem_override)
            if p_override.suffix.lower() in TEXTO_PLANO:
                # "zshrc.md" → stem="zshrc", ext=".md"
                stem = p_override.stem
                ext  = p_override.suffix.lower()
            else:
                # override sin extensión reconocida → usarlo como stem, ext del origen
                stem = stem_override
                ext  = src.suffix.lower()
        else:
            stem = src.stem
            ext  = src.suffix.lower()

        if ext and ext not in TEXTO_PLANO:
            print(f"{self.c.get('warn')}Advertencia: '{ext}' no es texto plano. "
                  f"Se enlazará igualmente.{self.c.get('rst')}")

        if grupo_hint:
            grupo = grupo_hint
            self.ui.print_arbol()
            print(f"  → Grupo: {grupo}")
        else:
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}{ext}'")
        if not grupo: return

        dest = self.storage.base / self.storage.titulo(grupo) / f"{stem}{ext}"

        if dest.exists():
            if dest.stat().st_ino == src.stat().st_ino:
                return print(f"{self.c.get('date')}(Hardlink ya existe){self.c.get('rst')}")
            return print(f"Ya existe {grupo}/{stem}{ext} con distinto contenido. Abortando.")

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
            metodo, es_copia = "hardlink", False
        except OSError as e:
            print(f"{self.c.get('date')}  hardlink no posible ({e.strerror}), copiando...{self.c.get('rst')}")
            shutil.copy2(src, dest)
            metodo, es_copia = "copia", True

        self.storage.links.register(grupo, stem, src, es_copia=es_copia)
        origen_fmt = self.ui._fmt_origin(str(src))
        ruta_fmt   = self.ui.render_ruta(grupo, stem)
        print(f"{self.c.get('plus')}+ {ruta_fmt}{self.c.get('rst')}"
              f"  {self.c.get('link')}→ {origen_fmt}  ({metodo}){self.c.get('rst')}")

    def cmd_unlink(self, args):
        """Elimina el registro del enlace/copia. Ambos archivos quedan intactos."""
        if not args:
            # mostrar solo eventos que tienen enlace registrado
            enlazados = []
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    if self.storage.links.is_registered(g, stem):
                        enlazados.append((g, stem))
            if not enlazados:
                print(f"{self.c.get('date')}  No hay enlaces registrados.{self.c.get('rst')}")
                return
            for g, stem in enlazados:
                ruta_fmt   = self.ui.render_ruta(g, stem)
                origen_fmt = self.ui._fmt_origin(self.storage.links.origin(g, stem) or "")
                es_copia   = self.storage.links.is_copy(g, stem)
                marca      = "c →" if es_copia else "→"
                print(f"  {ruta_fmt}  {self.c.get('link')}{marca} {origen_fmt}{self.c.get('rst')}")
            entrada = self.ui.leer("Evento: ")
            if not entrada: return
        else:
            entrada = args[0]

        grupo, stem = self.resolver_arg(entrada)
        if not grupo or not stem:
            return print(f"No encontrado: '{entrada}'")

        if not self.storage.links.is_registered(grupo, stem):
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            return print(f"  {ruta_fmt}  {self.c.get('date')}(sin enlace registrado){self.c.get('rst')}")

        origen_fmt = self.ui._fmt_origin(self.storage.links.origin(grupo, stem) or "")
        ruta_fmt   = self.ui.render_ruta(grupo, stem)
        self.storage.links.remove(grupo, stem)
        print(f"{self.c.get('minus')}  {ruta_fmt}  {self.c.get('date')}desenlazado "
              f"(archivo intacto){self.c.get('rst')}  {self.c.get('link')}{origen_fmt}{self.c.get('rst')}")

    def cmd_del(self, args):
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o evento: ")
        if not entrada: return print("Cancelado.")

        grupo, stem = self.resolver_arg(entrada)

        if grupo and not stem:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir(): return print(f"No existe el grupo '{grupo}'")
            self.ui.print_arbol([grupo])
            if self.ui.leer(f"Enviar al trash '{grupo}/'? (s/n): ") == "s":
                for ev in self.storage.get_eventos(grupo):
                    p = self.storage.get_evento_path(grupo, ev)
                    if p:
                        self.storage.links.remove(grupo, ev)
                        self.storage.info.remove(grupo, ev)
                        self.storage.gpg.unmark(grupo, ev)
                self.storage.trash(gp)
                print(f"Enviado al trash: {grupo}/")
        else:
            if not grupo or not stem:
                return print(f"No encontrado: '{entrada}'")
            ev_path = self.storage.get_evento_path(grupo, stem)
            if not ev_path or not ev_path.is_file():
                return print(f"No existe {grupo}/{stem}")
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            if self.ui.leer(f"Enviar al trash {grupo}/{ev_path.name}? (s/n): ") == "s":
                self.storage.links.remove(grupo, stem)
                self.storage.info.remove(grupo, stem)
                self.storage.gpg.unmark(grupo, stem)
                self.storage.trash(ev_path)
                print(f"{self.c.get('minus')}- {ruta_fmt}{self.c.get('rst')}")

        self.storage.limpiar_vacios()

    def cmd_mv(self, args):
        if not args:
            self.ui.print_arbol()
            opcion = self.ui.leer("¿[m]over o [f]usionar? (m/f): ").lower()
            if opcion == "f":
                g_dest = self.ui.pedir_grupo("Grupo destino")
                e_dest = self.ui.pedir_evento(g_dest, "Evento destino")
                g_src  = self.ui.pedir_grupo("Grupo fuente")
                e_src  = self.ui.pedir_evento(g_src, "Evento fuente")
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                g_src  = self.ui.pedir_grupo("Grupo origen")
                e_src  = self.ui.pedir_evento(g_src, "Evento origen")
                g_dest = self.ui.pedir_grupo("Grupo destino")
                nuevo  = self.ui.leer(f"Nuevo nombre (Enter = '{e_src}'): ") or e_src
                self._mover(g_src, e_src, g_dest, nuevo.lower())
            return

        if len(args) == 1:
            return print("Uso: byte --mv [destino] [fuente]  |  byte --mv [origen] [Grupo/]")

        a1, a2 = args[0], args[1]
        if a2.endswith("/"):
            g_src, e_src = self.resolver_arg(a1)
            g_dest = self.find_grupo(a2.rstrip("/")) or self.storage.titulo(a2.rstrip("/"))
            if not g_src or not e_src: return print(f"Origen no encontrado: '{a1}'")
            self._mover(g_src, e_src, g_dest, e_src)
            return
        if "/" in a2:
            g_src, e_src   = self.resolver_arg(a1)
            g_dest, e_dest = self.resolver_arg(a2)
            if not g_src or not e_src: return print(f"Origen no encontrado: '{a1}'")
            if not g_dest:
                partes = a2.split("/")
                g_dest, e_dest = self.storage.titulo(partes[0]), partes[1].lower()
            if not e_dest: e_dest = e_src
            p_dest = self.storage.get_evento_path(g_dest, e_dest)
            if p_dest and p_dest.is_file():
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                self._mover(g_src, e_src, g_dest, e_dest)
            return
        g_dest, e_dest = self.resolver_arg(a1)
        g_src,  e_src  = self.resolver_arg(a2)
        if not g_dest or not e_dest: return print(f"Destino no encontrado: '{a1}'")
        if not g_src  or not e_src:  return print(f"Fuente no encontrado: '{a2}'")
        self._fusionar(g_dest, e_dest, g_src, e_src)

    def _fusionar(self, g_dest, e_dest, g_src, e_src):
        p_src  = self.storage.get_evento_path(g_src, e_src)
        p_dest = self.storage.get_evento_path(g_dest, e_dest)
        if not p_src or not p_src.is_file():
            return print(f"No existe: {g_src}/{e_src}")
        p_dest_real = p_dest if p_dest else self.storage.evento_path(g_dest, e_dest)
        p_dest_real.parent.mkdir(parents=True, exist_ok=True)
        contenido_src = p_src.read_text(encoding="utf-8")
        if p_dest_real.is_file():
            contenido_dest = p_dest_real.read_text(encoding="utf-8")
            sep = "\n\n---\n\n" if contenido_dest.strip() else ""
            p_dest_real.write_text(contenido_dest + sep + contenido_src, encoding="utf-8")
        else:
            p_dest_real.write_text(contenido_src, encoding="utf-8")
        self.storage.links.remove(g_src, e_src)
        self.storage.info.remove(g_src, e_src)
        self.storage.gpg.unmark(g_src, e_src)
        p_src.unlink()
        self.storage.limpiar_vacios()
        r_src  = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{self.c.get('plus')}✓ Fusionado: {r_src} ➔ {r_dest}{self.c.get('rst')}")

    def _mover(self, g_src, e_src, g_dest, e_dest):
        p_src = self.storage.get_evento_path(g_src, e_src)
        if not p_src or not p_src.is_file():
            return print(f"No existe: {g_src}/{e_src}")
        p_dest = self.storage.evento_path(g_dest, e_dest, ext=p_src.suffix)
        if p_dest.is_file():
            print(f"Ya existe {g_dest}/{e_dest} → fusionando.")
            self._fusionar(g_dest, e_dest, g_src, e_src)
            return
        p_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p_src), str(p_dest))
        self.storage.links.rename(g_src, e_src, g_dest, e_dest)
        # mover info y gpg
        info_txt = self.storage.info.get(g_src, e_src)
        if info_txt:
            self.storage.info.set(g_dest, e_dest, info_txt)
            self.storage.info.remove(g_src, e_src)
        if self.storage.gpg.is_protected(g_src, e_src):
            key_id = self.storage.gpg.key_id(g_src, e_src)
            self.storage.gpg.mark(g_dest, e_dest, key_id)
            self.storage.gpg.unmark(g_src, e_src)
        self.storage.limpiar_vacios()
        r_src  = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{self.c.get('plus')}✓ Movido: {r_src} ➔ {r_dest}{self.c.get('rst')}")

    def cmd_gpg(self, args):
        """byte --gpg evento [key_id]
        Marca el evento como protegido y lo cifra (o descifra si ya lo está).
        Sin key_id usa el configurado en byte.toml.
        """
        if not shutil.which("gpg"):
            return print("gpg no está disponible en el sistema.")

        if not args:
            self.ui.print_arbol()
            entrada = self.ui.leer("Evento: ")
            if not entrada: return
            key_arg = ""
        else:
            entrada = args[0]
            key_arg = args[1] if len(args) > 1 else ""

        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem  = entrada.lower()
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo: return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem: return

        ev_path = self.storage.get_evento_path(grupo, stem)

        # Determinar llave
        key_id = key_arg or self.gpg_key
        if not key_id:
            key_id = self.ui.leer("ID de llave GPG (email o fingerprint): ")
            if not key_id: return

        ruta_fmt = self.ui.render_ruta(grupo, stem)

        # ¿Ya cifrado? → descifrar
        if ev_path and ev_path.suffix.lower() == ".gpg":
            if self.ui.leer(f"Descifrar {grupo}/{stem}? (s/n): ") != "s":
                return
            try:
                tmp = self._gpg_decrypt_to_tmp(ev_path)
            except RuntimeError as e:
                return print(f"GPG error: {e}")
            inner_ext = Path(ev_path.stem).suffix or ".md"
            clear_path = ev_path.parent / f"{stem}{inner_ext}"
            shutil.move(str(tmp), str(clear_path))
            ev_path.unlink()
            self.storage.gpg.unmark(grupo, stem)
            print(f"{self.c.get('plus')}~ {ruta_fmt}{self.c.get('rst')} (descifrado)")
            return

        # No existe aún → crear vacío primero
        if not ev_path or not ev_path.is_file():
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            ev_path.touch()

        # Cifrar — preservar registro de link si existe
        link_origin  = self.storage.links.get(grupo, stem)
        link_is_copy = self.storage.links.is_copy(grupo, stem)
        try:
            self._gpg_encrypt(ev_path, key_id)
        except RuntimeError as e:
            return print(f"GPG error: {e}")

        self.storage.gpg.mark(grupo, stem, key_id)
        # GPG destruye el archivo original y crea uno nuevo → el hardlink se rompe.
        # Si tenía link (hardlink o copia), forzar copy=True para que check
        # lo trate como copia sincronizable.
        if link_origin:
            self.storage.links.register(grupo, stem, Path(link_origin), es_copia=True)
        print(f"{self.c.get('plus')}~ {ruta_fmt}{self.c.get('rst')}"
              f"  {self.c.get('warn')}g{self.c.get('rst')} cifrado con {key_id}")

    def cmd_nogpg(self, args):
        """Desprotege y descifra un evento GPG, deja el archivo en claro."""
        if not shutil.which("gpg"):
            return print("gpg no está disponible en el sistema.")

        if not args:
            self.ui.print_arbol()
            entrada = self.ui.leer("Evento a desproteger: ")
            if not entrada: return
        else:
            entrada = args[0]

        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem  = entrada.lower()
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo: return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem: return

        ev_path = self.storage.get_evento_path(grupo, stem)
        ruta_fmt = self.ui.render_ruta(grupo, stem)

        if not ev_path or ev_path.suffix.lower() != ".gpg":
            return print(f"  {ruta_fmt} no está cifrado.")

        if self.ui.leer(f"  Descifrar y desproteger {grupo}/{stem}? (s/n): ") != "s":
            print(f"{self.c.get('date')}  Cancelado{self.c.get('rst')}")
            return

        try:
            tmp = self._gpg_decrypt_to_tmp(ev_path)
        except RuntimeError as e:
            return print(f"GPG error: {e}")

        inner_ext  = Path(ev_path.stem).suffix or ".md"
        clear_path = ev_path.parent / f"{stem}{inner_ext}"
        shutil.move(str(tmp), str(clear_path))
        ev_path.unlink()
        self.storage.gpg.unmark(grupo, stem)
        print(f"{self.c.get('plus')}~ {ruta_fmt}{self.c.get('rst')} (descifrado, sin protección GPG)")

    def _leer_contenido(self, path: Path) -> str:
        """Lee el contenido de un archivo, descifrando si es .gpg."""
        if path.suffix.lower() == ".gpg":
            try:
                tmp = self._gpg_decrypt_to_tmp(path)
                contenido = tmp.read_text(encoding="utf-8", errors="replace")
                tmp.unlink(missing_ok=True)
                return contenido
            except RuntimeError:
                return None  # GPG falló (llave no disponible, etc.)
        return path.read_text(encoding="utf-8", errors="replace")

    def cmd_check(self, args):
        """Detecta y sincroniza cambios en ambas direcciones para copias y enlaces GPG."""
        candidatos = []
        for g in self.storage.get_grupos():
            for stem in self.storage.get_eventos(g):
                p = self.storage.get_evento_path(g, stem)
                if not p: continue
                is_copy = self.storage.links.is_copy(g, stem)
                is_gpg_link = (p.suffix.lower() == ".gpg"
                               and self.storage.links.is_registered(g, stem))
                if is_copy or is_gpg_link:
                    candidatos.append((g, stem, p))

        if not candidatos:
            print(f"{self.c.get('date')}  No hay copias ni enlaces cifrados registrados.{self.c.get('rst')}")
            return

        # clasificar: entrantes (origen→evento) y salientes (evento→origen)
        cambios_entrantes = []
        salientes         = []

        for g, stem, ev_path in candidatos:
            origin = self.storage.links.origin(g, stem)
            if not origin: continue
            src = Path(origin)
            if not src.is_file():
                print(f"{self.c.get('minus')}  {g}/{stem} — origen no disponible: "
                      f"{self.ui._fmt_origin(str(src))}{self.c.get('rst')}")
                continue

            es_gpg = ev_path.suffix.lower() == ".gpg"
            contenido_ev = self._leer_contenido(ev_path)
            if contenido_ev is None:
                print(f"{self.c.get('warn')}  {g}/{stem} — no se pudo descifrar (GPG), omitido.{self.c.get('rst')}")
                continue
            contenido_src = src.read_text(encoding="utf-8", errors="replace")

            if contenido_ev != contenido_src:
                cambios_entrantes.append((g, stem, ev_path, src, es_gpg))
            else:
                salientes.append((g, stem, ev_path, src))

        # --- dirección origen→evento ---
        for g, stem, ev_path, src, es_gpg in cambios_entrantes:
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt   = self.ui.render_ruta(g, stem)
            gpg_tag    = f" {self.c.get('warn')}g{self.c.get('rst')}" if es_gpg else ""
            print(f"\n{self.c.get('bold')}  {ruta_fmt}{self.c.get('rst')}{gpg_tag}"
                  f"  {self.c.get('link')}c → {origen_fmt}{self.c.get('rst')}"
                  f"  {self.c.get('date')}(origen modificado){self.c.get('rst')}")
            while True:
                res = self.ui.leer("  ¿[s]incronizar origen→evento, [d]iff, [n]o? (s/d/n): ").lower()
                if res == "d":
                    if es_gpg:
                        tmp = self._gpg_decrypt_to_tmp(ev_path)
                        mostrar_diff(tmp, src)
                        tmp.unlink(missing_ok=True)
                    else:
                        mostrar_diff(ev_path, src)
                elif res == "s":
                    if es_gpg:
                        key_id = self.storage.gpg.key_id(g, stem)
                        inner_ext = Path(ev_path.stem).suffix or ".md"
                        inner = ev_path.parent / f"{stem}{inner_ext}"
                        shutil.copy2(src, inner)
                        ev_path.unlink()
                        self._gpg_encrypt(inner, key_id)
                        print(f"{self.c.get('plus')}  ✓ Actualizado y re-cifrado desde {origen_fmt}{self.c.get('rst')}")
                    else:
                        shutil.copy2(src, ev_path)
                        print(f"{self.c.get('plus')}  ✓ Actualizado desde {origen_fmt}{self.c.get('rst')}")
                    break
                else:
                    print(f"{self.c.get('date')}  Omitido{self.c.get('rst')}")
                    break

        # --- dirección evento→origen ---
        for g, stem, ev_path, src in salientes:
            contenido_ev  = self._leer_contenido(ev_path)
            contenido_src = src.read_text(encoding="utf-8", errors="replace")
            if contenido_ev == contenido_src:
                continue
            origen_fmt = self.ui._fmt_origin(str(src))
            ruta_fmt   = self.ui.render_ruta(g, stem)
            print(f"\n{self.c.get('bold')}  {ruta_fmt}{self.c.get('rst')}"
                  f"  {self.c.get('link')}→ {origen_fmt}{self.c.get('rst')}"
                  f"  {self.c.get('date')}(evento modificado){self.c.get('rst')}")
            while True:
                res = self.ui.leer("  ¿[s]incronizar evento→origen, [d]iff, [n]o? (s/d/n): ").lower()
                if res == "d":
                    mostrar_diff(src, ev_path)
                elif res == "s":
                    shutil.copy2(ev_path, src)
                    print(f"{self.c.get('plus')}  ✓ Origen actualizado: {origen_fmt}{self.c.get('rst')}")
                    break
                else:
                    print(f"{self.c.get('date')}  Omitido{self.c.get('rst')}")
                    break

        # mensaje final si no hubo nada que reportar
        if not cambios_entrantes and not any(
            self._leer_contenido(ev) != src.read_text(encoding="utf-8", errors="replace")
            for _, _, ev, src in salientes
        ):
            print(f"{self.c.get('date')}  Todo al día.{self.c.get('rst')}")

    def cmd_info(self, args):
        if not args:
            encontrado = False
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    txt = self.storage.info.get(g, stem)
                    if txt:
                        ruta_fmt = self.ui.render_ruta(g, stem)
                        print(f"  {ruta_fmt}  {self.c.get('date')}{txt}{self.c.get('rst')}")
                        encontrado = True
            if not encontrado:
                print(f"{self.c.get('date')}  (ningún evento tiene info){self.c.get('rst')}")
            return

        grupo, stem = self.resolver_arg(args[0])
        if not grupo:
            stem  = args[0].lower()
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo: return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem: return

        ruta_fmt = self.ui.render_ruta(grupo, stem)
        if len(args) >= 2:
            texto = " ".join(args[1:])
            self.storage.info.set(grupo, stem, texto)
            print(f"  {ruta_fmt}  {self.c.get('date')}{texto}{self.c.get('rst')}")
        else:
            txt = self.storage.info.get(grupo, stem)
            if txt:
                print(f"  {ruta_fmt}  {self.c.get('date')}{txt}{self.c.get('rst')}")
            else:
                print(f"  {ruta_fmt}  {self.c.get('date')}(sin info){self.c.get('rst')}")

    def cmd_config(self, args):
        """Asistente de configuración inicial. Genera ~/.config/byte/byte.toml."""
        c, r, d = self.c.get("bold"), self.c.get("rst"), self.c.get("date")
        print(f"\n{c}BYTE — Configuración inicial{r}\n"
              f"  Archivo: {CONFIG_PATH}\n"
              f"  {d}(Enter = mantener valor actual){r}\n")

        # base
        base_actual = str(BASE)
        resp = self.ui.leer(f"Directorio base [{base_actual}]: ")
        nueva_base = resp if resp else base_actual

        # editor
        editor_actual = EDITOR
        resp = self.ui.leer(f"Editor [{editor_actual}]: ")
        nuevo_editor = resp if resp else editor_actual

        # links
        links_actual = str(LINKS)
        resp = self.ui.leer(f"Archivo links [{links_actual}]: ")
        nuevo_links = resp if resp else links_actual

        # info
        info_actual = str(INFO)
        resp = self.ui.leer(f"Archivo info [{info_actual}]: ")
        nuevo_info = resp if resp else info_actual

        # gpg key
        gpg_actual = self.gpg_key or ""
        resp = self.ui.leer(f"Llave GPG (email/fingerprint) [{gpg_actual or 'ninguna'}]: ")
        nuevo_gpg = resp if resp else gpg_actual

        # previsualización
        lineas = [
            f'base   = "{nueva_base}"',
            f'editor = "{nuevo_editor}"',
            f'links  = "{nuevo_links}"',
            f'info   = "{nuevo_info}"',
        ]
        if nuevo_gpg:
            lineas.append(f'gpg_key = "{nuevo_gpg}"')

        contenido = "\n".join(lineas) + "\n"
        print(f"\n{d}--- byte.toml ---{r}")
        print(contenido)

        if self.ui.leer("¿Guardar? (s/n): ") == "s":
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(contenido, encoding="utf-8")
            print(f"{self.c.get('plus')}✓ Guardado en {CONFIG_PATH}{r}")
        else:
            print(f"{d}  Cancelado{r}")

    def cmd_dir(self, args):
        d = self.c.get("date"); r = self.c.get("rst")
        print(f"  base:   {self.storage.base}")
        print(f"  links:  {self.storage.links.path}")
        print(f"  info:   {self.storage.info.path}")
        print(f"  config: {CONFIG_PATH}")
        print(f"  gpg:    {self.storage.gpg.path}")

    def cmd_complete(self, args):
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self):
        h = self.c.get("header"); t = self.c.get("tree")
        d = self.c.get("date");   r = self.c.get("rst")
        w = self.c.get("warn")
        print(f"""{h}BYTE — Notas en Markdown{r}
  {t}byte{r}                                   Árbol (grupos=3 letras, eventos=2 letras)
  {t}byte -t{r}                                Árbol con fechas de modificación
  {t}byte -h | --help{r}                       Esta ayuda

  {h}Abrir / Añadir texto{r}
  {t}byte [evento]{r}                          Abre en editor (crea .md si no existe)
  {t}byte [evento] texto...{r}                 Añade línea al final
  {t}byte [Grupo/evento]{r}                    Abre evento explícito

  {h}Comandos{r}  {d}(--comando  o  letra sin guion){r}
  {t}--link      l{r}  archivo [nombre]        Hardlink/copia → evento (mismo nombre o el que indiques)
{t}--del       d{r}  [ruta]                  Envía al .trash/
  {t}--mv        m{r}  [origen] [destino]      Mueve o fusiona
  {t}--info      i{r}  [evento] [texto]        Muestra o establece info corta
  {t}--gpg       g{r}  evento [key]            Cifra con GPG y protege el evento
  {t}--nogpg     q{r}  evento                  Descifra y elimina la protección GPG
  {t}--check     c{r}                          Detecta y sincroniza cambios (ambas direcciones)
  {t}--unlink    u{r}  [evento]                Elimina el registro del enlace (archivos intactos)
  {t}--config    x{r}                          Asistente de configuración inicial
  {t}--dir        {r}                          Muestra rutas activas

  {h}Árbol — indicadores{r}
  {w}g{r}  protegido con GPG   {d}i{r}  tiene info   {d}c →{r}  copia   {d}→{r}  hardlink""")


# ===== DISPATCH TABLE =====
def build_dispatch(app):
    return {
        "link":      app.cmd_link,
"del":       app.cmd_del,
        "mv":        app.cmd_mv,
        "info":      app.cmd_info,
        "gpg":       app.cmd_gpg,
        "nogpg":     app.cmd_nogpg,
        "check":     app.cmd_check,
        "unlink":    app.cmd_unlink,
        "config":    app.cmd_config,
        "dir":       app.cmd_dir,
        "_complete": app.cmd_complete,
        # letras cortas (sin guion)
        "l": app.cmd_link,
        "d": app.cmd_del,
        "m": app.cmd_mv,
        "i": app.cmd_info,
        "g": app.cmd_gpg,
        "q": app.cmd_nogpg,
        "c": app.cmd_check,
        "u": app.cmd_unlink,
        "x": app.cmd_config,
    }


# ===== MAIN =====
def main():
    app      = ByteApp(base_path=BASE, editor_name=EDITOR, links_path=LINKS, info_path=INFO)
    dispatch = build_dispatch(app)
    app.storage.asegurar_base()

    args = sys.argv[1:]

    if not args:
        app.ui.print_arbol()
        return

    cmd  = args[0]
    rest = args[1:]

    # Flags globales
    if cmd in ["-t", "--total", "-v"]:
        app.ui.print_arbol(show_dates=True)
        return
    # Ayuda: todas las variantes
    if cmd in ["-h", "--help", "help", "h"]:
        app.mostrar_ayuda()
        return

    # Normalizar: --comando → comando  |  letra sola sin guion
    if cmd.startswith("--"):
        cmd_clean = cmd[2:]
    else:
        cmd_clean = cmd  # letras cortas sin guion, o token libre

    if cmd_clean in dispatch:
        dispatch[cmd_clean](rest)
    else:
        # token libre → abrir/crear evento (nunca colisiona con comandos)
        app.cmd_open([cmd] + rest)


if __name__ == "__main__":
    main()
