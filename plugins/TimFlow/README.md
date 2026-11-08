# TimFlow - QGIS plugin

## Installation
1. Find din QGIS profil-plugin-mappe:
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Kopier hele mappen `TimFlow` (denne mappe) ind i plugin-mappen ovenfor, så stien bliver
   `.../python/plugins/TimFlow/__init__.py` osv.
   (Alternativt: I QGIS -> Plugins -> Manage and Install Plugins -> Install from ZIP,
   og vælg TimFlow.zip.)
3. Genstart QGIS, gå til `Plugins -> Manage and Install Plugins -> Installed`,
   og sæt flueben ved "TimFlow".
4. Der kommer nu et TimFlow-ikon i værktøjslinjen samt et selvstændigt "TimFlow"-punkt
   i menubjælken.

## Brug
- **Nyt**: Spørger om et modelnavn og placering, og opretter en geopackage med
  lagene `<modelnavn>` (polygon, felter `grid_x` (heltal), `grid_y` (heltal), `jordtype`
  (decimal)) og `Reference` (punkt, felt `refkote` (decimal)). Alle lag samles i en
  gruppe med modelnavnet. Koordinatsystem = projektets CRS, ellers EPSG:25832.
- **Rediger**: Vælg et lag fra en tidligere TimFlow-geopackage i lagpanelet, og klik
  "Rediger" for at koble pluginet til den pågældende geopackage uden at oprette nye lag.
- **Tilføj vandstande**: Opretter et nyt punktlag i den aktive geopackage med et felt
  `kote` (decimal). Du angiver selv lagets navn.
- **Reference punkt**: Beregner og indsætter (evt. genindsætter) et enkelt punkt i
  laget `Reference` ud fra første polygon i polygonlaget (det eneste polygonlag i modellen) samt gennemsnittet af `kote` i alle
  øvrige punktlag.
- **Beregn**: Genererer et TimML/TTim-baseret pythonscript (`timflow.steady`) ud fra
  indholdet af geopackagen, beder dig gemme det, og spørger efterfølgende om scriptet
  skal køres med det samme.

## Bemærk
- `icon.png` er pluginets ikon (vises i værktøjslinjen og i plugin-oversigten).
- "Beregn" kalder `python <script>.py` via systemets kommandolinje. Sørg for at
  `python` findes i PATH, og at pakken `timflow` samt øvrige afhængigheder
  (numpy, matplotlib, tkinter) er installeret i det python-miljø, der bruges.
