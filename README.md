# bit.py

Gestor de eventos tipo log, byte es basado en bit.py.

Pendiente:
- [ ] Actualizar README.md
- [ ] A futuro integrar en byte.py

# byte.py

Gestor de notas Markdown organizado en grupos/eventos. Guarda archivos en
`Grupo/evento.md`, con soporte opcional de cifrado GPG y sincronización de
archivos externos via hardlinks.

```
~/bytes/
├── Dev/
│   ├── ideas.md
│   └── setup.md
└── Personal/
    └── diario.md.gpg
```


## Uso

```
byte                       árbol de notas
byte -t                    árbol con fechas de modificación
byte entrada               abre en editor (crea si no existe)
byte entrada texto...      añade texto al final sin abrir editor
byte Grupo/entrada         abre evento explícito
```

## Comandos

| Comando | Atajo | Descripción |
|---------|-------|-------------|
| `--link`   | `l` | Registra un archivo externo como origen (hardlink o copia) |
| `--unlink` | `u` | Quita el registro del enlace (archivos intactos) |
| `--del`    | `d` | Envía entrada o grupo al `.trash/` |
| `--mv`     | `m` | Mueve o fusiona eventos |
| `--info`   | `i` | Nota corta asociada a un evento |
| `--gpg`    | `g` | Cifra con clave GPG; sobre cifrado, añade destinatario |
| `--nogpg`  | `q` | Descifra y elimina protección GPG |
| `--check`  | `c` | Muestra configuración y sincroniza copias/enlaces |
| `--config` | `x` | Configuración inicial |

Los grupos y entradas se pueden referenciar por abreviatura: `byte Dia/id` en
lugar de `byte Diario/ideas`. Tres letras para grupos, 2 para entrada.

## Configuración

El archivo `byte.toml` se crea automáticamente en `~/.config/byte/byte.toml`
o dentro del vault en `.byte/byte.toml`.

```toml
base   = "~/Documentos/Obsidian/bytes"
editor = "micro"
gpg_key = "tu@correo.com"
gpg_keys_secondary = []
```

## Indicadores en el árbol

`g` cifrado GPG · `i` tiene nota · `→` hardlink · `c →` copia · `✗` enlace roto
