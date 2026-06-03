#!/usr/bin/env python3
import os
import sys
import re
import json
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

# ===== CONFIGURACIÓN DE RUTAS =====
BASE = Path.home() / "Documentos/Filen/Obsidian/bits"
EDITOR = os.environ.get("MICRO_EDITOR", os.environ.get("EDITOR", "micro"))

# ===== CLASE DE COLORES (MONOCROMÁTICO LIMPIO) =====
class BitColor:
    def __init__(self):
        self.codes = {
            "rst":    self._ansi("0"),
            "bold":   self._ansi("1;37"),      # Blanco Brillante (Jump Labels de grupos/eventos)
            "event":  self._ansi("0;37"),      # Blanco Normal (Texto general)
            "header": self._ansi("1;37"),      # Blanco Brillante
            "plus":   self._ansi("1;37"),      # Blanco Brillante
            "route":  self._ansi("0;37"),      # Blanco Normal
            "minus":  self._ansi("38;5;243"),  # Gris medio
            "tree":   self._ansi("38;5;239"),  # Gris Oscuro (Líneas del árbol)
            "count":  self._ansi("1;37"),      # Blanco Brillante (Conteos)
            "date":   self._ansi("38;5;243"),  # Gris medio (Fechas apagadas)
        }

    def _ansi(self, code):
        return f"\033[{code}m"

    def get(self, key):
        return self.codes.get(key, "")

# ===== GESTOR DE ALMACENAMIENTO (RESPETA TU FORMATO PLANO) =====
class BitStorage:
    def __init__(self, base_path):
        self.base = Path(base_path)

    def asegurar_base(self):
        self.base.mkdir(parents=True, exist_ok=True)

    def normalize(self, txt):
        txt = txt.lower()
        return "".join(c for c in unicodedata.normalize('NFKD', txt) if unicodedata.category(c) != 'Mn')

    def titulo(self, txt):
        return txt.strip().capitalize()

    def get_grupos(self):
        if not self.base.is_dir(): return []
        return sorted([d.name for d in self.base.iterdir() if d.is_dir() and not d.name.startswith(".")])

    def get_eventos(self, grupo):
        gp = self.base / grupo
        if not gp.is_dir(): return []
        return sorted([f.stem for f in gp.iterdir() if f.is_file() and f.suffix == ".md" and not f.name.startswith(".")])

    def grupo_path(self, grupo):
        return self.base / self.titulo(grupo)

    def evento_path(self, grupo, evento):
        return self.base / self.titulo(grupo) / f"{evento.lower()}.md"

    def trash(self, path):
        if not path.exists(): return
        trash_dir = self.base / ".trash"
        trash_dir.mkdir(exist_ok=True)
        shutil.move(str(path), str(trash_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"))

    def read_evento(self, grupo, evento):
        p = self.evento_path(grupo, evento)
        if not p.is_file(): return []
        lines = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str: continue
                m = re.match(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})\s+(.*)$", line_str)
                if m:
                    lines.append({"fecha": m.group(1), "comentario": m.group(2)})
        return lines

    def write_evento(self, grupo, evento, lines):
        p = self.evento_path(grupo, evento)
        if not lines:
            if p.is_file(): p.unlink()
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            for l in lines:
                f.write(f"{l['fecha']} {l['comentario']}\n")

# ===== INTERFAZ VISUAL =====
class BitInterface:
    def __init__(self, storage, color):
        self.storage = storage
        self.c = color

    def leer(self, prompt):
        try: 
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError): 
            print(f"\n  {self.c.get('tree')}(Interrumpido por el usuario){self.c.get('rst')}\n")
            sys.exit(0)

    def pedir_grupo(self, label="Grupo"):
        grupos = self.storage.get_grupos()
        self.print_arbol_compacto()
        while True:
            res = self.leer(f"{label}: ")
            if not res: return ""
            g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
            for g in grupos:
                if g_abbrevs.get(g) == self.storage.normalize(res) or self.storage.normalize(g) == self.storage.normalize(res):
                    return g
            return self.storage.titulo(res)

    def pedir_evento(self, grupo, label="Evento"):
        evs = self.storage.get_eventos(grupo)
        if evs:
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            print(f"\n  Eventos en {grupo}:")
            for e in evs:
                print(f"    {self._render_nombre_con_label(e, e_abbrevs.get(e), longitud=2)}")
            print()
        while True:
            res = self.leer(f"{label}: ")
            if not res: return ""
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            for e in evs:
                if e_abbrevs.get(e) == res.lower() or self.storage.normalize(e) == res.lower():
                    return e
            return res.lower()

    def calc_abreviaturas(self, lista, longitud):
        abbrevs = {}
        usados = set()
        for item in lista:
            plano = self.storage.normalize(item)
            found = False
            for i in range(len(plano) - (longitud - 1)):
                sub = plano[i:i+longitud]
                if sub not in usados and " " not in sub:
                    abbrevs[item] = sub
                    usados.add(sub)
                    found = True
                    break
            if not found:
                for i in range(len(plano) - (longitud - 1)):
                    sub = plano[i:i+longitud]
                    if " " not in sub:
                        abbrevs[item] = sub
                        break
        return abbrevs

    def _render_nombre_con_label(self, nombre, abbrev, longitud):
        color_vivo = self.c.get("event")
        if not abbrev:
            return color_vivo + nombre + self.c.get("rst")
            
        nombre_plano = "".join(
            c for c in unicodedata.normalize("NFKD", nombre.lower())
            if unicodedata.category(c) != "Mn"
        )
        idx = nombre_plano.find(abbrev)
        
        if idx != -1:
            start = nombre[:idx]
            lbl = nombre[idx:idx+longitud]
            end = nombre[idx+longitud:]
            strong = self.c.get("bold")
            return f"{color_vivo}{start}{self.c.get('rst')}{strong}{lbl}{self.c.get('rst')}{color_vivo}{end}{self.c.get('rst')}"
        else:
            return f"{color_vivo}{nombre} {self.c.get('bold')}{abbrev}{self.c.get('rst')}"

    def render_ruta_completa(self, grupo, evento):
        """Formatea de manera elegante Grupo(3 letras)/Evento(2 letras) para los mensajes del sistema."""
        grupos = self.storage.get_grupos()
        g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
        g_render = self._render_nombre_con_label(grupo, g_abbrevs.get(grupo), longitud=3)
        
        evs = self.storage.get_eventos(grupo)
        # Si el evento es nuevo y no está listado aún, forzamos un cálculo rápido sobre la marcha
        if evento not in evs: evs.append(evento)
        e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
        e_render = self._render_nombre_con_label(evento, e_abbrevs.get(evento), longitud=2)
        
        return f"{g_render}{self.c.get('tree')}/{self.c.get('rst')}{e_render}"

    def _linea_grupo(self, prefijo, grupo, abbrev):
        return f"{self.c.get('tree')}{prefijo}{self._render_nombre_con_label(grupo + '/', abbrev, longitud=3)}"

    def _linea_evento(self, pref_g, pref_e, ev, extra="", abbrev=None):
        return f"{self.c.get('tree')}{pref_g}{pref_e}{self._render_nombre_con_label(ev, abbrev, longitud=2)}{extra}{self.c.get('rst')}"

    def print_arbol_compacto(self, grupos_filter=None):
        grupos = grupos_filter if grupos_filter is not None else self.storage.get_grupos()
        if not grupos:
            print("  (vacío)"); return
        g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
        for gi, grupo in enumerate(grupos):
            is_last_g = gi == len(grupos) - 1
            print(self._linea_grupo("└── " if is_last_g else "├── ", grupo, g_abbrevs.get(grupo)))
            evs = self.storage.get_eventos(grupo)
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            for ei, ev in enumerate(evs):
                ev_lines = self.storage.read_evento(grupo, ev)
                extra = f"  {self.c.get('tree')}({self.c.get('count')}{len(ev_lines)}{self.c.get('tree')}){self.c.get('rst')}"
                print(self._linea_evento("    " if is_last_g else "│   ", "└── " if ei == len(evs) - 1 else "├── ", ev, extra, e_abbrevs.get(ev)))

    def print_resumen(self):
        grupos = self.storage.get_grupos()
        if not grupos:
            print("  (vacío)"); return
        g_abbrevs = self.calc_abreviaturas(grupos, longitud=3)
        for gi, grupo in enumerate(grupos):
            is_last_g = gi == len(grupos) - 1
            print(self._linea_grupo("└── " if is_last_g else "├── ", grupo, g_abbrevs.get(grupo)))
            evs = self.storage.get_eventos(grupo)
            e_abbrevs = self.calc_abreviaturas(evs, longitud=2)
            for ei, ev in enumerate(evs):
                ev_lines = self.storage.read_evento(grupo, ev)
                ultima = f"  {self.c.get('date')}{ev_lines[-1]['fecha']}" if ev_lines else ""
                extra = f"  {self.c.get('tree')}({self.c.get('count')}{len(ev_lines)}{self.c.get('tree')}){self.c.get('rst')}{ultima}"
                print(self._linea_evento("    " if is_last_g else "│   ", "└── " if ei == len(evs) - 1 else "├── ", ev, extra, e_abbrevs.get(ev)))

    def print_evento_tabla(self, grupo, evento, show_dates=False):
        lines = self.storage.read_evento(grupo, evento)
        if not lines:
            print("  (sin registros)"); return
        print(f"\n  {self.render_ruta_completa(grupo, evento)}")
        for i, line in enumerate(lines, 1):
            date_str = f"{self.c.get('date')}{line['fecha']}{self.c.get('tree')} │ " if show_dates else ""
            print(f"  {self.c.get('count')}{i:2d}{self.c.get('tree')} │ {date_str}{self.c.get('event')}{line['comentario']}{self.c.get('rst')}")
        print()

# ===== NÚCLEO DE LA APLICACIÓN =====
class BitApp:
    def __init__(self, base_path, editor_name):
        self.storage = BitStorage(base_path)
        self.c = BitColor()
        self.ui = BitInterface(self.storage, self.c)
        self.editor = editor_name

    def find_grupo_o_evento(self, token):
        token_p = self.storage.normalize(token)
        grupos = self.storage.get_grupos()
        g_abbrevs = self.ui.calc_abreviaturas(grupos, longitud=3)
        for g in grupos:
            if g_abbrevs.get(g) == token_p or self.storage.normalize(g) == token_p:
                return g
        return None

    def parse_arg(self, arg):
        if not arg: return None, None
        m = re.match(r"^([^/]+)/(.+)$", arg)
        if m:
            g, ev = m.group(1), m.group(2)
            grupo_resuelto = self.find_grupo_o_evento(g) or self.storage.titulo(g)
            if grupo_resuelto:
                evs = self.storage.get_eventos(grupo_resuelto)
                e_abbrevs = self.ui.calc_abreviaturas(evs, longitud=2)
                for e in evs:
                    if e_abbrevs.get(e) == ev.lower() or self.storage.normalize(e) == ev.lower():
                        return grupo_resuelto, e
            return grupo_resuelto, ev
            
        m = re.match(r"^([^/]+)/$", arg)
        if m:
            return self.find_grupo_o_evento(m.group(1)) or self.storage.titulo(m.group(1)), None
        return None, arg

    def resolver_arg(self, arg):
        g, e = self.parse_arg(arg)
        if g and e: return g, e
        token = e or g
        if not token: return None, None
        
        grupos = self.storage.get_grupos()
        g_abbrevs = self.ui.calc_abreviaturas(grupos, longitud=3)
        
        for g_item in grupos:
            evs = self.storage.get_eventos(g_item)
            e_abbrevs = self.ui.calc_abreviaturas(evs, longitud=2)
            for e_item in evs:
                if e_abbrevs.get(e_item) == token.lower() or self.storage.normalize(e_item) == token.lower():
                    return g_item, e_item
                    
        for g_item in grupos:
            if g_abbrevs.get(g_item) == token.lower() or self.storage.normalize(g_item) == token.lower():
                return g_item, None
                
        return None, token

    def insert_sorted(self, lines, new_line):
        lines.append(new_line)
        try:
            lines.sort(key=lambda x: datetime.strptime(x["fecha"], "%Y-%m-%d %H:%M"))
        except Exception:
            pass
        return lines

    def _limpiar_vacios(self):
        for g in self.storage.get_grupos():
            gp = self.storage.grupo_path(g)
            if gp.is_dir() and not any(gp.iterdir()):
                gp.rmdir()

    def cmd_add(self, args):
        if not args:
            grupo = self.ui.pedir_grupo()
            if not grupo: return
            evento = self.ui.pedir_evento(grupo)
            if not evento: return
            comentario = self.ui.leer("Comentario: ")
            if not comentario: return
        else:
            grupo, evento = self.resolver_arg(args[0])
            if not grupo:
                grupo = self.ui.pedir_grupo(f"Crear nuevo grupo para '{evento}'")
                if not grupo: return
            if not evento:
                evento = self.ui.pedir_evento(grupo, "Evento")
                if not evento: return
            comentario = " ".join(args[1:]) if len(args) > 1 else self.ui.leer("Comentario: ")
            if not comentario: return

        fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = self.storage.read_evento(grupo, evento)
        lines = self.insert_sorted(lines, {"fecha": fecha_str, "comentario": comentario})
        self.storage.write_evento(grupo, evento, lines)
        
        # Resaltado en la salida +
        ruta_fmt = self.ui.render_ruta_completa(grupo, evento)
        print(f"{self.c.get('plus')}+ {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} {comentario}")

    def cmd_listar_evento(self, arg, show_dates=False):
        g, e = self.resolver_arg(arg)
        if g and e:
            self.ui.print_evento_tabla(g, e, show_dates=show_dates)
        elif g:
            self.ui.print_arbol_compacto([g])
        else:
            print(f"No encontrado: {arg}")

    def cmd_edit(self, args):
        entrada = args[0] if args else self.ui.pedir_grupo()
        g, e = self.resolver_arg(entrada)
        if not g or not e: return print("Evento no encontrado.")
        p = self.storage.evento_path(g, e)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.system(f"{self.editor} {p}")
        lines = self.storage.read_evento(g, e)
        self.storage.write_evento(g, e, lines)
        self._limpiar_vacios()

    def cmd_pop(self, args):
        entrada = args[0] if args else self.ui.pedir_grupo()
        g, e = self.resolver_arg(entrada)
        if not g or not e: return print("No encontrado.")
        lines = self.storage.read_evento(g, e)
        if not lines: return print("Vacío.")
        popped = lines.pop()
        self.storage.write_evento(g, e, lines)
        
        # Resaltado en la salida -
        ruta_fmt = self.ui.render_ruta_completa(g, e)
        print(f"{self.c.get('minus')}- {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} Quitada última línea: {popped['comentario']}")
        self._limpiar_vacios()

    def cmd_del(self, args):
        entrada = args[0] if args else self.ui.leer("Borrar Grupo/ o evento: ")
        if not entrada: return print("Cancelado")
        
        grupo, evento = self.resolver_arg(entrada)

        if grupo and not evento:
            gp = self.storage.grupo_path(grupo)
            if not gp.is_dir(): return print(f"No existe el grupo '{grupo}'")
            self.ui.print_arbol_compacto([grupo])
            if self.ui.leer(f"Enviar al trash el grupo '{grupo}' y todo su contenido? (s/n): ") == "s":
                self.storage.trash(gp); print(f"Enviado al trash: {grupo}/")
        else:
            if not grupo or not evento:
                return print(f"No se pudo encontrar el grupo o evento para: '{entrada}'")
                
            path = self.storage.evento_path(grupo, evento)
            if not path.is_file(): return print(f"No existe el archivo {grupo}/{evento}")
            if self.ui.leer(f"Enviar al trash '{grupo}/{evento}'? (s/n): ") == "s":
                self.storage.trash(path)
                ruta_fmt = self.ui.render_ruta_completa(grupo, evento)
                print(f"Enviado al trash: {ruta_fmt}")
        self._limpiar_vacios()

    def cmd_mv(self, args):
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
                print("Error: Origen no válido."); return

            lines_orig = self.storage.read_evento(g_orig, e_orig)
            if not lines_orig or n > len(lines_orig) or n <= 0:
                print("Línea origen inválida."); return
                
            moved = lines_orig.pop(n - 1)
            self.storage.write_evento(g_orig, e_orig, lines_orig)

            self.storage.grupo_path(g_dest).mkdir(parents=True, exist_ok=True)
            lines_dest = self.storage.read_evento(g_dest, e_dest)
            lines_dest = self.insert_sorted(lines_dest, moved)
            self.storage.write_evento(g_dest, e_dest, lines_dest)
            
            # Resaltado en movimiento de línea
            r_orig = self.ui.render_ruta_completa(g_orig, e_orig)
            r_dest = self.ui.render_ruta_completa(g_dest, e_dest)
            print(f"~ Línea #{n} movida: {r_orig} → {r_dest} {self.c.get('tree')}│{self.c.get('rst')} {moved['comentario']}")
            self._limpiar_vacios()

        elif len(args) == 2:
            g_orig, e_orig = self.resolver_arg(args[0])
            g_dest, e_dest = self.resolver_arg(args[1])
            
            if g_dest and not e_dest:
                e_dest = e_orig
            elif not g_dest and not e_dest:
                g_dest, e_dest = self.parse_arg(args[1])
                if g_dest and not e_dest: e_dest = e_orig

            if not g_orig or not e_orig or not g_dest:
                print("Error: Rutas de origen o destino inválidas."); return

            src = self.storage.evento_path(g_orig, e_orig)
            dest = self.storage.evento_path(g_dest, e_dest)

            if not src.is_file():
                print(f"Error: No existe el evento {g_orig}/{e_orig}"); return

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
                # Resaltado en fusión completa
                print(f"✓ Evento fusionado: {r_orig} ➔ {r_dest}")
            else:
                shutil.move(str(src), str(dest))
                # Resaltado en movimiento completo
                print(f"✓ Evento movido: {r_orig} ➔ {r_dest}")
            
            self._limpiar_vacios()
        else:
            print("Uso de mv:\n  bit mv [Origen] [N] [Destino]  -> Mover línea N\n  bit mv [Origen] [GrupoDestino/]-> Mover evento completo")

    def cmd_rm(self, args):
        if not args: return print("Indica evento.")
        g, e = self.resolver_arg(args[0])
        if not g or not e: return print("No encontrado.")
        self.ui.print_evento_tabla(g, e)
        n_str = args[1] if len(args) > 1 else self.ui.leer("Número de línea a quitar: ")
        if not n_str.isdigit(): return print("Cancelado.")
        n = int(n_str)
        lines = self.storage.read_evento(g, e)
        if n > len(lines) or n <= 0: return print("Línea inválida.")
        removed = lines.pop(n - 1)
        self.storage.write_evento(g, e, lines)
        
        # Resaltado en rm de línea
        ruta_fmt = self.ui.render_ruta_completa(g, e)
        print(f"{self.c.get('minus')}- {ruta_fmt} {self.c.get('tree')}│{self.c.get('rst')} Removida línea #{n}: {removed['comentario']}")
        self._limpiar_vacios()

    def cmd_dir(self, args):
        print(f"Base path: {self.storage.base}")

    def cmd_raw(self, args):
        if not args: 
            return print("Indica el evento o grupo/evento.")
            
        # Resolvemos usando tu motor prioritario (2 letras para eventos, 3 para grupos)
        g, e = self.resolver_arg(args[0])
        if not g or not e: 
            return print(f"No encontrado: {args[0]}")
            
        lines = self.storage.read_evento(g, e)
        if not lines:
            return print("  (sin registros)")
            
        # Generamos la salida estructurada en una tabla Markdown real con tiempos
        print(f"| Fecha y Hora | {g}/{e} |")
        print("| --- | --- |")
        for line in lines:
            print(f"| {line['fecha']} | {line['comentario']} |")

    def cmd_complete(self, args):
        tokens = []
        for g in self.storage.get_grupos():
            tokens.append(f"{g}/")
            for e in self.storage.get_eventos(g):
                tokens.append(f"{g}/{e}")
        print(" ".join(tokens))

    def mostrar_ayuda(self):
        print(f"""{self.c.get('header')}SISTEMA BITÁCORAS BIT{self.c.get('rst')}
  bit                       Muestra árbol limpio (Filtros: Grupos = 3 letras, Eventos = 2 letras)
  bit -t | -v               Muestra árbol de carpetas con fechas finales
  bit [evento]              Muestra registros de un evento sin marcas de tiempo
  bit -t [evento]           Muestra registros de un evento CON marcas de tiempo nativas
  bit [evento] [texto...]   Añade una nota rápida a un evento
  bit mv [ev1] [ev2]        Mueve o fusiona un evento completo a otro grupo
  bit mv [ev1] [N] [ev2]    Mueve la línea número N a otro evento
  bit del [ruta]            Envía un grupo o un evento a .trash/""")

# ===== ENTRADA DE LA APLICACIÓN =====
def main():
    app = BitApp(base_path=BASE, editor_name=EDITOR)
    app.storage.asegurar_base()
    
    args = sys.argv[1:]
    
    if not args:
        app.ui.print_arbol_compacto()
        return

    cmd = args[0]
    rest = args[1:]
    
    if cmd in ["-t", "--total", "-v", "--verbose"]:
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
