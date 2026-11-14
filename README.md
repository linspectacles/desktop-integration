# Desktop Integration

**Expose. Explore. Explain.**

Desktop Integration is a read-only freedesktop desktop-entry and MIME association/default-resolution inspector using XDG application directories and `mimeapps.list` precedence.

Current release: **0.0.3**

Repository: <https://github.com/linspectacles/desktop-integration>

<p align="center">
  <a href="https://github.com/brunonlinespace/brunonlinespace/blob/main/linspectacles/desktop-integration.png">
    <img
      src="https://raw.githubusercontent.com/brunonlinespace/brunonlinespace/main/linspectacles/desktop-integration.png"
      alt="Desktop Integration screenshot"
      width="900"
    >
  </a>
</p>

## What Desktop Integration is

Desktop Integration answers a focused question: **how are applications registered with the desktop, and how do the current XDG configuration layers associate MIME types with those desktop entries?**

It is not an installed-package inventory and not an application launcher. Desktop-entry commands are displayed as registration evidence but never executed.

## Highlights

- Four views: **Desktop Entries**, **Actions**, **MIME Associations**, and **MIME Configuration**.
- Desktop-entry discovery from XDG application directories.
- Desktop-file ID precedence and shadowing.
- `Hidden`, `NoDisplay`, `OnlyShowIn`, and `NotShowIn` reported separately.
- Desktop actions kept distinct from the main application entry.
- MIME defaults, added associations, removed associations, and desktop-entry `MimeType` declarations treated as separate inputs.
- `mimeapps.list` resolution across user/system and desktop-specific lookup precedence.
- Shared MIME-info aliases/subclasses used for installed inheritance context.
- Missing referenced desktop IDs reported instead of silently removed.
- `TryExec` may be checked for file/PATH availability but is never executed.

## Data sources and acquisition

### Desktop entries

- `$XDG_DATA_HOME/applications`
- `$XDG_DATA_DIRS/applications`
- standard XDG defaults when unset.

### MIME configuration

- `$XDG_CONFIG_HOME`
- `$XDG_CONFIG_DIRS`
- `$XDG_DATA_HOME`
- `$XDG_DATA_DIRS`
- `XDG_CURRENT_DESKTOP`
- desktop-specific `*-mimeapps.list`
- `mimeapps.list`
- compatibility/deprecated application-directory locations defined by the MIME Applications specification.

### MIME inheritance context

- `mime/aliases`
- `mime/subclasses`

under the XDG data roots.

## Interface

The View selector provides **Desktop Entries**, **Actions**, **MIME Associations**, and **MIME Configuration**.

Metadata Details preserves source paths, precedence/shadowing, desktop-entry fields and association-resolution evidence.

## Read-only and scope boundaries

Desktop Integration never executes `Exec`, action commands, or `TryExec`; it does not launch applications, edit desktop entries, change MIME defaults, modify `mimeapps.list`, install/uninstall desktop files, or rebuild MIME caches.

**Installed Software** remains the package/application-installation authority. **Autostart Inspector** remains the automatic-launch authority.

## Useful keyboard shortcuts

| Command | Shortcut |
| --- | --- |
| Toggle Metadata Details | `F3` |
| Toggle Metadata Details layout | `Ctrl+L` |
| Refresh inventory | `Shift+F5` |
| Options | `Ctrl+/` |

## Running from source

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install PyQt6
python3 -m linspectacles_desktop_integration.standalone
```

No application is launched during inspection.

No RPM installation instructions are included in this README.

## LinSpectacles Hub

This applet can run independently, but it can also be installed and used inside **[LinSpectacles Hub](https://github.com/linspectacles/linspectacles-hub)**.

LinSpectacles Hub is the optional workbench for the LinSpectacles applet family. It provides shared applet discovery, navigation and management while each inspector remains independently runnable.

To install a portable or user applet through the Hub, open **Configuration... → Applets** and add the applet ZIP. Once discovered, the applet can be enabled and opened from the Hub navigation.

System-installed applets are discovered by the Hub from its system applet store and remain managed by the system package manager.

## Source layout and LinSpectacles integration

```text
linspectacles_desktop_integration/
├── __init__.py
├── standalone.py
├── LICENSE
├── applet.json
├── README.md
└── desktop_integration/
    ├── __init__.py
    ├── applet.py
    ├── _support.py
    └── icon.png
```

## Configuration

Desktop Integration persists implemented presentation state only; it does not persist/write desktop defaults or associations.

## Scope

Desktop Integration owns **desktop-entry registration and MIME handler/default resolution**. **Installed Software** owns installed software/package inventory, **XDG Portals** owns portal frontend/backend configuration, and **D-Bus Inspector** owns generic D-Bus state.

## License

GNU General Public License v3.0 or later. See [`LICENSE`](LICENSE).

## Links

- LinSpectacles organisation: <https://github.com/linspectacles/>
- LinSpectacles Hub: <https://github.com/linspectacles/linspectacles-hub>
- Repository: <https://github.com/linspectacles/desktop-integration>
- Issues: <https://github.com/linspectacles/desktop-integration/issues>

<p align="center">
  <a href="https://github.com/linspectacles/desktop-integration">
    <img
      src="desktop_integration/icon.png"
      alt="Desktop Integration"
      width="256"
    >
  </a>
</p>
