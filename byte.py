#!/usr/bin/env python3
import os
import sys
import re
import json
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    import tomllib          # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib   # pip install tomli
    except ImportError:
        tomllib = None

# ===== CONFIGURACIÓN =====
CONFIG_DIR  = Path.home() / ".config" / "byte"
CONFIG_PATH = CONFIG_DIR / "byte.toml"

def _load_config():
    """Lee ~/.config/byte/byte.toml si existe."""
    if not CONFIG_PATH.is_file():
        return {}
    if tomllib is None:
        print("\033[38;5;243mAviso: byte.toml encontrado pero no hay parser TOML disponible "
              "(Python < 3.11 y tomli no instalado). Usando valores por defecto.\033[0m",
              file=sys.stderr)
        return {}
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)

_CFG = _load_config()

def _cfg_path(key, default):
    raw = _CFG.get(key)
    if raw is None:
        return Path(default).expanduser()
    return Path(raw).expanduser()

BASE   = _cfg_path("base", Path.home() / "Documentos/Filen/Obsidian/bytes")
LINKS  = _cfg_path("links", CONFIG_DIR / "links.json")
INFO   = _cfg_path("info",  CONFIG_DIR / "info.json")
EDITOR = _CFG.get("editor") or os.environ.get("MICRO_EDITOR") or os.environ.get("EDITOR", "micro")

# Extensiones que byte reconoce como texto plano (eventos con extensión propia)
TEXTO_PLANO = {
    ".md", ".txt", ".csv", ".tsv", ".log", ".org", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".css", ".js", ".py", ".sh", ".lua", ".rb", ".go", ".rs",
}

# Extensiones soportadas por Obsidian para byte insert
OBSIDIAN = TEXTO_PLANO | {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp",
    ".pdf",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".3gp",
    ".mp4", ".webm", ".ogv", ".mov", ".mkv",
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
        }

    def _ansi(self, code): return f"\033[{code}m"
    def get(self, key):    return self.codes.get(key, "")


# ===== REGISTRO DE HARDLINKS =====
class LinkRegistry:
    """Persiste inode→ruta_original en BASE/.links (JSON)."""

    def __init__(self, base, links_path=None):
        self.path  = Path(links_path) if links_path else Path(base) / ".links"
        self._data = None   # {str(inode): {"path": str, "copy": bool}}

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
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def register(self, inode: int, ruta: Path, es_copia: bool = False):
        self._load()
        self._data[str(inode)] = {"path": str(ruta), "copy": es_copia}
        self._save()

    def get(self, inode: int):
        self._load()
        entry = self._data.get(str(inode))
        if entry is None: return None
        # compatibilidad con registros viejos (string plano)
        return entry["path"] if isinstance(entry, dict) else entry

    def is_copy(self, path: Path) -> bool:
        """True si fue registrado como copia (no hardlink real)."""
        if not path.is_file(): return False
        self._load()
        entry = self._data.get(str(path.stat().st_ino))
        if entry is None: return False
        return entry.get("copy", False) if isinstance(entry, dict) else False

    def remove(self, inode: int):
        self._load()
        self._data.pop(str(inode), None)
        self._save()

    def is_hardlink(self, path: Path) -> bool:
        """True si el archivo está registrado (hardlink o copia)."""
        if not path.is_file(): return False
        self._load()
        return str(path.stat().st_ino) in self._data

    def origin(self, path: Path):
        """Ruta original registrada, o None."""
        if not path.is_file(): return None
        self._load()
        return self.get(path.stat().st_ino)



# ===== REGISTRO DE INFO =====
class InfoRegistry:
    """Persiste grupo/evento → descripción corta en ~/.config/byte/info.json."""

    def __init__(self, info_path):
        self.path  = Path(info_path)
        self._data = None   # {"Grupo/stem": "texto"}

    def _key(self, grupo, stem):
        return f"{grupo}/{stem}"

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
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                             encoding="utf-8")

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


# ===== ALMACENAMIENTO =====
class ByteStorage:
    def __init__(self, base_path, links_path=None, info_path=None):
        self.base    = Path(base_path)
        self.links   = LinkRegistry(self.base, links_path=links_path)
        self.info    = InfoRegistry(info_path or (Path.home() / ".config/byte/info.json"))

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
        """Devuelve stems de todos los archivos en el grupo (cualquier extensión de TEXTO_PLANO + .md)."""
        gp = self.base / grupo
        if not gp.is_dir(): return []
        resultado = []
        for f in sorted(gp.iterdir()):
            if f.name.startswith("."): continue
            if f.suffix.lower() in TEXTO_PLANO:
                resultado.append(f.stem)
        return resultado

    def get_evento_path(self, grupo, stem):
        """Busca el archivo real de un evento por su stem (sin extensión).
        Devuelve el Path si existe, None si no."""
        gp = self.base / grupo
        if not gp.is_dir(): return None
        for f in gp.iterdir():
            if f.stem == stem and f.suffix.lower() in TEXTO_PLANO:
                return f
        return None

    def grupo_path(self, grupo):
        return self.base / self.titulo(grupo)

    def evento_path(self, grupo, stem, ext=".md"):
        """Ruta canónica para un evento nuevo (siempre .md por defecto)."""
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
                # fallback: primer substring sin espacio aunque esté duplicado
                for i in range(len(plano) - longitud + 1):
                    sub = plano[i:i+longitud]
                    if " " not in sub:
                        abbrevs[item] = sub
                        break
        return abbrevs

    def _render_label(self, nombre, abbrev, longitud):
        c = self.c
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

                ev_path   = self.storage.get_evento_path(grupo, stem)
                origin    = self.storage.links.origin(ev_path) if ev_path else None

                # extensión visible si no es .md
                ext_str = ""
                if ev_path and ev_path.suffix.lower() != ".md":
                    ext_str = f"{self.c.get('date')}{ev_path.suffix}{self.c.get('rst')}"

                extra = ""
                if self.storage.info.has(grupo, stem):
                    extra += f" {self.c.get('date')}i{self.c.get('rst')}"
                if origin:
                    try:
                        origen_fmt = "~/" + str(Path(origin).relative_to(Path.home()))
                    except ValueError:
                        origen_fmt = origin
                    es_copia = self.storage.links.is_copy(ev_path)
                    if es_copia:
                        extra += f" {self.c.get('date')}c{self.c.get('rst')} {self.c.get('link')}→ {origen_fmt}{self.c.get('rst')}"
                    else:
                        extra += f" {self.c.get('link')}→ {origen_fmt}{self.c.get('rst')}"
                if show_dates:
                    mt = self.storage.mtime(ev_path)
                    if mt:
                        extra += f"  {self.c.get('date')}{mt.strftime('%Y-%m-%d %H:%M')}{self.c.get('rst')}"

                linea = (f"{self.c.get('tree')}{pad}{e_pref}"
                         f"{self._render_label(stem, e_abbrevs.get(stem), longitud=2)}"
                         f"{ext_str}{extra}{self.c.get('rst')}")
                print(linea)

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

    # ----- resolución de argumentos -----

    def find_grupo(self, token):
        token_p   = self.storage.normalize(token)
        grupos    = self.storage.get_grupos()
        g_abbrevs = self.ui.calc_abreviaturas(grupos, longitud=3)
        for g in grupos:
            if g_abbrevs.get(g) == token_p or self.storage.normalize(g) == token_p:
                return g
        return None

    def parse_arg(self, arg):
        """Parsea 'Grupo/stem' | 'Grupo/' | 'stem'  →  (grupo|None, stem|None)."""
        if not arg: return None, None

        m = re.match(r"^([^/]+)/(.+)$", arg)
        if m:
            g_raw, ev_raw = m.group(1), m.group(2)
            grupo = self.find_grupo(g_raw) or self.storage.titulo(g_raw)
            evs   = self.storage.get_eventos(grupo)
            e_abb = self.ui.calc_abreviaturas(evs, longitud=2)
            # quitar extensión del token si la trae
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
        """Resuelve token → (grupo, stem). Prioridad: ruta explícita > abbrev evento > abbrev grupo."""
        g, e = self.parse_arg(arg)
        if g and e: return g, e
        token = e or g
        if not token: return None, None

        # quitar extensión del token si la trae
        token_path = Path(token)
        if token_path.suffix.lower() in TEXTO_PLANO:
            token_stem = token_path.stem
        else:
            token_stem = token

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

        # buscar el archivo real (puede tener extensión distinta a .md)
        ev_path = self.storage.get_evento_path(grupo, stem)
        if ev_path is None:
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")

        ruta_fmt = self.ui.render_ruta(grupo, stem)

        if texto is not None:
            es_nuevo = not ev_path.is_file()
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(texto + "\n")
            accion = "+" if es_nuevo else "~"
            print(f"{self.c.get('plus')}{accion} {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} {texto}")
            return

        # modo editor
        es_nuevo = not ev_path.is_file()
        ev_path.parent.mkdir(parents=True, exist_ok=True)
        ev_path.touch()
        os.system(f'{self.editor} "{ev_path}"')

        if es_nuevo and ev_path.stat().st_size == 0:
            ev_path.unlink()
            print(f"{self.c.get('minus')}(Archivo vacío descartado){self.c.get('rst')}")
        else:
            accion = f"{self.c.get('plus')}+" if es_nuevo else f"{self.c.get('count')}~"
            print(f"{accion} {ruta_fmt}{self.c.get('rst')}")
            # Si es una copia, ofrecer sincronización automática
            if not es_nuevo and self.storage.links.is_copy(ev_path):
                self.cmd_updatelink([], auto=True, ev_path_auto=ev_path)

    def cmd_link(self, args):
        """Crea un hardlink del archivo externo como evento.
        El stem del evento es el nombre del archivo fuente.
        byte link ../README.md  →  pregunta grupo, crea Grupo/README.md como hardlink.
        """
        if not args:
            archivo = self.ui.leer("Archivo a enlazar: ")
            if not archivo: return
        else:
            archivo = args[0]

        src = Path(archivo).expanduser().resolve()
        if not src.is_file():
            return print(f"No existe: {src}")

        ext  = src.suffix.lower()
        stem = src.stem

        # Si la extensión no es texto plano avisamos
        if ext not in TEXTO_PLANO:
            print(f"{self.c.get('minus')}Advertencia: '{ext}' no es texto plano. "
                  f"Se enlazará igualmente.{self.c.get('rst')}")

        grupo = self.ui.pedir_grupo(f"Grupo para '{stem}{ext}'")
        if not grupo: return

        dest = self.storage.base / self.storage.titulo(grupo) / f"{stem}{ext}"

        if dest.exists():
            # verificar si ya es el mismo inodo
            if dest.stat().st_ino == src.stat().st_ino:
                print(f"{self.c.get('date')}(Hardlink ya existe){self.c.get('rst')}")
            else:
                print(f"Ya existe {grupo}/{stem}{ext} y no es el mismo archivo. Abortando.")
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
            metodo = "hardlink"
            es_copia = False
        except OSError as e:
            print(f"{self.c.get('date')}  hardlink no posible ({e.strerror}), copiando...{self.c.get('rst')}")
            shutil.copy2(src, dest)
            metodo = "copia"
            es_copia = True

        self.storage.links.register(dest.stat().st_ino, src, es_copia=es_copia)

        try:
            origen_fmt = "~/" + str(src.relative_to(Path.home()))
        except ValueError:
            origen_fmt = str(src)

        ruta_fmt = self.ui.render_ruta(grupo, stem)
        print(f"{self.c.get('plus')}+ {ruta_fmt}{self.c.get('rst')}"
              f"  {self.c.get('link')}→ {origen_fmt}  ({metodo}){self.c.get('rst')}")

    def cmd_insert(self, args):
        """Añade [[ruta/relativa]] al .md del evento."""
        if len(args) < 2:
            self.ui.print_arbol()
            entrada = self.ui.leer("Evento: ")
            if not entrada: return
            archivo = self.ui.leer("Archivo a insertar: ")
            if not archivo: return
        else:
            entrada = args[0]
            archivo = " ".join(args[1:])

        src = Path(archivo).expanduser().resolve()
        if not src.is_file():
            return print(f"No existe: {src}")

        ext = src.suffix.lower()
        if ext not in OBSIDIAN:
            return print(f"'{ext}' no está soportado por Obsidian.")

        grupo, stem = self.resolver_arg(entrada)
        if not grupo:
            stem  = entrada.lower()
            grupo = self.ui.pedir_grupo(f"Grupo para '{stem}'")
            if not grupo: return
        if not stem:
            stem = self.ui.pedir_evento(grupo, "Evento")
            if not stem: return

        ev_path = self.storage.get_evento_path(grupo, stem)
        if ev_path is None:
            ev_path = self.storage.evento_path(grupo, stem, ext=".md")

        es_nuevo = not ev_path.is_file()
        ev_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            rel = src.relative_to(self.storage.base)
        except ValueError:
            rel = src

        link = f"[[{rel}]]"
        with open(ev_path, "a", encoding="utf-8") as f:
            f.write(link + "\n")

        ruta_fmt = self.ui.render_ruta(grupo, stem)
        accion   = "+" if es_nuevo else "~"
        print(f"{self.c.get('plus')}{accion} {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} {link}")

    def cmd_del(self, args):
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o evento: ")
        if not entrada: return print("Cancelado.")

        grupo, stem = self.resolver_arg(entrada)

        if grupo and not stem:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir(): return print(f"No existe el grupo '{grupo}'")
            self.ui.print_arbol([grupo])
            if self.ui.leer(f"Enviar al trash '{grupo}/' y todo su contenido? (s/n): ") == "s":
                # limpiar registros de hardlinks del grupo
                for ev in self.storage.get_eventos(grupo):
                    p = self.storage.get_evento_path(grupo, ev)
                    if p: self.storage.links.remove(p.stat().st_ino)
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
                self.storage.links.remove(ev_path.stat().st_ino)
                self.storage.trash(ev_path)
                print(f"{self.c.get('minus')}- {ruta_fmt}{self.c.get('rst')}")

        self.storage.limpiar_vacios()

    def cmd_mv(self, args):
        """
        byte mv [destino] [fuente]        → fusiona (append fuente → destino, borra fuente)
        byte mv [origen] [Grupo/]         → mueve evento a otro grupo
        byte mv [origen] [Grupo/nombre]   → mueve y renombra
        Sin args → interactivo
        """
        if not args:
            self.ui.print_arbol()
            opcion = self.ui.leer("¿[m]over o [f]usionar? (m/f): ").lower()
            if opcion == "f":
                g_dest = self.ui.pedir_grupo("Grupo destino (recibirá el contenido)")
                e_dest = self.ui.pedir_evento(g_dest, "Evento destino")
                g_src  = self.ui.pedir_grupo("Grupo fuente")
                e_src  = self.ui.pedir_evento(g_src, "Evento fuente (será eliminado)")
                self._fusionar(g_dest, e_dest, g_src, e_src)
            else:
                g_src  = self.ui.pedir_grupo("Grupo origen")
                e_src  = self.ui.pedir_evento(g_src, "Evento origen")
                g_dest = self.ui.pedir_grupo("Grupo destino")
                nuevo  = self.ui.leer(f"Nuevo nombre (Enter = '{e_src}'): ") or e_src
                self._mover(g_src, e_src, g_dest, nuevo.lower())
            return

        if len(args) == 1:
            return print("Uso: byte mv [destino] [fuente]  |  byte mv [origen] [Grupo/]")

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

        # dos tokens simples → fusión (destino primero)
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
        self.storage.links.remove(p_src.stat().st_ino)
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
            print(f"Ya existe {g_dest}/{e_dest}{p_src.suffix} → fusionando.")
            self._fusionar(g_dest, e_dest, g_src, e_src)
            return
        p_dest.parent.mkdir(parents=True, exist_ok=True)
        # mantener registro hardlink si aplica
        inode = p_src.stat().st_ino
        origin = self.storage.links.get(inode)
        shutil.move(str(p_src), str(p_dest))
        if origin:
            self.storage.links.register(p_dest.stat().st_ino, Path(origin))
            self.storage.links.remove(inode)
        self.storage.limpiar_vacios()
        r_src  = self.ui.render_ruta(g_src, e_src)
        r_dest = self.ui.render_ruta(g_dest, e_dest)
        print(f"{self.c.get('plus')}✓ Movido: {r_src} ➔ {r_dest}{self.c.get('rst')}")

    def cmd_updatelink(self, args, auto=False, ev_path_auto=None):
        """Sincroniza copias con su archivo origen.
        auto=True: llamado automáticamente tras editar una copia (pide confirmación igual).
        """
        # Detectar herramienta de diff disponible
        def diff_tool():
            import shutil as sh
            for t in ["delta", "bat"]:
                if sh.which(t): return t
            return None

        def mostrar_diff(a, b):
            tool = diff_tool()
            if tool == "delta":
                os.system(f'diff -u "{a}" "{b}" | delta')
            elif tool == "bat":
                import tempfile, subprocess
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

        # Recopilar todas las copias o usar la pasada automáticamente
        if auto and ev_path_auto:
            candidatos = [ev_path_auto]
        else:
            candidatos = []
            for g in self.storage.get_grupos():
                for stem in self.storage.get_eventos(g):
                    p = self.storage.get_evento_path(g, stem)
                    if p and self.storage.links.is_copy(p):
                        candidatos.append(p)

        if not candidatos:
            if not auto:
                print(f"{self.c.get('date')}No hay copias registradas.{self.c.get('rst')}")
            return

        for ev_path in candidatos:
            origin = self.storage.links.origin(ev_path)
            if not origin:
                continue
            src = Path(origin)
            try:
                origen_fmt = "~/" + str(src.relative_to(Path.home()))
            except ValueError:
                origen_fmt = str(src)

            if not src.is_file():
                print(f"{self.c.get('minus')}  Origen ya no existe: {origen_fmt}{self.c.get('rst')}")
                continue

            # Comparar contenidos
            contenido_ev  = ev_path.read_text(encoding="utf-8", errors="replace")
            contenido_src = src.read_text(encoding="utf-8", errors="replace")

            if contenido_ev == contenido_src:
                if not auto:
                    print(f"{self.c.get('date')}  {ev_path.name} — sin cambios{self.c.get('rst')}")
                continue

            print(f"\n{self.c.get('bold')}  {ev_path.name}{self.c.get('rst')}"
                  f"  {self.c.get('link')}⇢ {origen_fmt}{self.c.get('rst')}")

            while True:
                res = self.ui.leer("  ¿[s]incronizar, [d]iff, o [n]o? (s/d/n): ").lower()
                if res == "d":
                    mostrar_diff(src, ev_path)  # src=origen, ev_path=tu versión
                elif res == "s":
                    shutil.copy2(ev_path, src)
                    print(f"{self.c.get('plus')}  ✓ Origen actualizado: {origen_fmt}{self.c.get('rst')}")
                    break
                else:
                    print(f"{self.c.get('date')}  Omitido{self.c.get('rst')}")
                    break

    def cmd_check(self, args):
        """Detecta copias cuyo origen cambió externamente y ofrece sincronizar."""

        def diff_tool():
            import shutil as sh
            for t in ["delta", "bat"]:
                if sh.which(t): return t
            return None

        def mostrar_diff(a, b):
            tool = diff_tool()
            if tool == "delta":
                os.system(f'diff -u "{a}" "{b}" | delta')
            elif tool == "bat":
                import tempfile, subprocess
                r = subprocess.run(["diff", "-u", str(a), str(b)],
                                   capture_output=True, text=True)
                if r.stdout:
                    tmp = tempfile.NamedTemporaryFile(suffix=".diff", delete=False, mode="w")
                    tmp.write(r.stdout); tmp.close()
                    os.system(f'bat --language=diff "{tmp.name}"')
                    Path(tmp.name).unlink()
                else:
                    print("  (sin diferencias)")
            else:
                os.system(f'diff -u "{a}" "{b}"')

        candidatos = []
        for g in self.storage.get_grupos():
            for stem in self.storage.get_eventos(g):
                p = self.storage.get_evento_path(g, stem)
                if p and self.storage.links.is_copy(p):
                    candidatos.append((g, stem, p))

        if not candidatos:
            print(f"{self.c.get('date')}  No hay copias registradas.{self.c.get('rst')}")
            return

        cambios = []
        for g, stem, ev_path in candidatos:
            origin = self.storage.links.origin(ev_path)
            if not origin: continue
            src = Path(origin)
            try:
                origen_fmt = "~/" + str(src.relative_to(Path.home()))
            except ValueError:
                origen_fmt = str(src)

            if not src.is_file():
                print(f"{self.c.get('minus')}  {g}/{stem} — origen ya no existe: {origen_fmt}{self.c.get('rst')}")
                continue

            contenido_ev  = ev_path.read_text(encoding="utf-8", errors="replace")
            contenido_src = src.read_text(encoding="utf-8", errors="replace")

            if contenido_ev != contenido_src:
                cambios.append((g, stem, ev_path, src, origen_fmt))

        if not cambios:
            print(f"{self.c.get('date')}  Todo al día, sin cambios externos.{self.c.get('rst')}")
            return

        for g, stem, ev_path, src, origen_fmt in cambios:
            ruta_fmt = self.ui.render_ruta(g, stem)
            print(f"\n{self.c.get('bold')}  {ruta_fmt}{self.c.get('rst')}"
                  f"  {self.c.get('link')}c → {origen_fmt}{self.c.get('rst')}")

            while True:
                res = self.ui.leer("  ¿[s]incronizar origen→evento, [d]iff, o [n]o? (s/d/n): ").lower()
                if res == "d":
                    mostrar_diff(ev_path, src)
                elif res == "s":
                    shutil.copy2(src, ev_path)
                    print(f"{self.c.get('plus')}  ✓ Evento actualizado desde {origen_fmt}{self.c.get('rst')}")
                    break
                else:
                    print(f"{self.c.get('date')}  Omitido{self.c.get('rst')}")
                    break

    def cmd_info(self, args):
        """byte info evento         → muestra la info del evento
           byte info evento texto   → establece (sobreescribe) la info
        """
        if not args:
            # Listar todos los eventos con info
            grupos = self.storage.get_grupos()
            encontrado = False
            for g in grupos:
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

        if len(args) >= 2:
            # Establecer info
            texto = " ".join(args[1:])
            self.storage.info.set(grupo, stem, texto)
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            print(f"  {ruta_fmt}  {self.c.get('date')}{texto}{self.c.get('rst')}")
        else:
            # Mostrar info
            txt = self.storage.info.get(grupo, stem)
            ruta_fmt = self.ui.render_ruta(grupo, stem)
            if txt:
                print(f"  {ruta_fmt}  {self.c.get('date')}{txt}{self.c.get('rst')}")
            else:
                print(f"  {ruta_fmt}  {self.c.get('date')}(sin info){self.c.get('rst')}")

    def cmd_dir(self, args):
        print(f"Base: {self.storage.base}")

    def cmd_complete(self, args):
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self):
        h, t, r = self.c.get("header"), self.c.get("tree"), self.c.get("rst")
        print(f"""{h}BYTE — Notas en Markdown{r}
  {t}byte{r}                               Árbol (grupos=3 letras, eventos=2 letras)
  {t}byte -t{r}                            Árbol con fecha de última modificación
  {t}byte [evento]{r}                      Abre en editor (crea .md si no existe)
  {t}byte [evento] texto...{r}             Añade línea al final del archivo
  {t}byte [Grupo/evento]{r}                Abre evento explícito
  {t}byte check{r}                         Detecta copias cuyo origen cambió externamente
  {t}byte info [evento]{r}                Muestra la info corta del evento
  {t}byte info [evento] texto{r}          Establece (sobreescribe) la info corta
  {t}byte link [archivo]{r}               Hardlink/copia: el archivo externo se convierte en evento (pregunta grupo)
  {t}byte updatelink  (ul){r}             Sincroniza copias con su origen (también auto tras editar)
  {t}byte insert [evento] [archivo]{r}     Añade [[ruta/relativa]] al .md (pdf, img, audio, texto...)
  {t}byte del [ruta]{r}                    Envía grupo o evento al .trash/
  {t}byte mv [destino] [fuente]{r}         Fusiona fuente al final de destino
  {t}byte mv [origen] [Grupo/]{r}          Mueve evento a otro grupo
  {t}byte mv [origen] [Grupo/nombre]{r}    Mueve y renombra""")


# ===== MAIN =====
def main():
    app = ByteApp(base_path=BASE, editor_name=EDITOR, links_path=LINKS, info_path=INFO)
    app.storage.asegurar_base()

    args = sys.argv[1:]

    if not args:
        app.ui.print_arbol()
        return

    cmd  = args[0]
    rest = args[1:]

    if cmd in ["-t", "--total", "-v"]:
        app.ui.print_arbol(show_dates=True)
        return

    dispatch = {
        "del":        app.cmd_del,
        "mv":         app.cmd_mv,
        "link":       app.cmd_link,
        "insert":     app.cmd_insert,
        "check":      app.cmd_check,
        "info":       app.cmd_info,
        "updatelink": app.cmd_updatelink,
        "ul":         app.cmd_updatelink,
        "dir":        app.cmd_dir,
        "_complete":  app.cmd_complete,
        "h":          lambda _: app.mostrar_ayuda(),
        "help":       lambda _: app.mostrar_ayuda(),
    }

    if cmd in dispatch:
        dispatch[cmd](rest)
    else:
        app.cmd_open([cmd] + rest)


if __name__ == "__main__":
    main()
