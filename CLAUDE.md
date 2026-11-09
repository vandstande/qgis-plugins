# Instruktioner til Claude

Monorepo med QGIS-plugins (QGIS 3.34, Windows-brugere, dansk UI). Publiceres via
GitHub Pages som et custom plugin-repository (`plugins.xml`).

- Fælles metadata (author, email) og homepage/repository/tracker styres
  i `plugins.toml` og skrives ind af `tools/build.py`. Ret dem aldrig i én metadata.txt.
- Alt pluginspecifikt (name, description, about, changelog, icon, tags, qgisMin/MaxVersion, supportsQt6) står i
  `plugins/<mappe>/metadata.txt`.
- Hver ændring i et plugin SKAL have ny version: `python tools/build.py bump <mappe> -m "..."`.
  CI fejler ellers.
- Ved ændringer på tværs af plugins: ret, bump hvert berørt plugin, og kør
  `python tools/build.py sync && python tools/build.py check` før commit.
- Plugins der hører til TimFlow-menuen tilføjer sig til menuen "TimFlow" hvis den findes,
  ellers opretter de den.
- Kun Python-standardbibliotek i tools/. Ingen binære filer i plugins.
- QGIS 4/Qt6: alle plugins understøtter QGIS 3 og 4 (qgisMaximumVersion=4.99, supportsQt6=True).
  Brug pluginets compat.py til QAction, felttyper (felttype("Double")) og QGIS-enums (NIVEAU_*, WKB_*,
  LAG_*, VFW_* ...). Qt-enums skrives fuldt scopet (QMessageBox.StandardButton.Yes). Brug .exec(), ikke .exec_().
- Grupper: timflow (vandstande.dk/timflow), anlaeg (/anlaeg), hecras (/hecras), naturvurdering (/naturvurdering).
