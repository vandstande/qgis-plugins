# Vandstande.dk – QGIS plugins

Alle vores QGIS-plugins samlet ét sted. Hver gang der pushes til `main`, bygger GitHub
automatisk zip-filer og et `plugins.xml`, og publicerer dem på GitHub Pages. QGIS henter
derfra og giver brugerne besked om opdateringer.

**Repository-URL til QGIS:** `https://<bruger>.github.io/qgis-plugins/plugins.xml`

## Struktur

```
plugins.toml            Fælles felter (forfatter, e-mail), grupper og rækkefølgen
                        på oversigtssiden + hvilken
                        hjemmeside hvert plugin hører til
plugins/<mappe>/        Ét plugin pr. mappe – præcis som det ligger i QGIS' plugin-mappe
tools/build.py          Byg, valider, hæv version, importer
tools/web/embed.js      Indlejring på vandstande.dk/timflow og /anlaeg
.github/workflows/      Automatisk build og publicering
```

## Ret i ét plugin

1. Ret filerne i `plugins/<mappe>/`
2. Hæv versionen (ellers stopper build'et – så brugerne altid får opdateringen):
   ```
   python tools/build.py bump <mappe> -m "Kort beskrivelse af ændringen"
   ```
   `bump` hæver sidste ciffer (1.2.3 → 1.2.4). Brug `minor` eller `major` for større ændringer:
   `python tools/build.py bump timflow minor -m "Ny funktion: ..."`
3. Commit og push. Efter ca. 1 minut kan brugerne opdatere i QGIS.

Du kan også bare rette `version=` og `changelog=` direkte i `metadata.txt`.

Kan det gøres i browseren? Ja: rediger filen på github.com, ret `version=` i
`metadata.txt` og commit. GitHub bygger selv.

## Fælles ændringer

Forfatter, e-mail, QGIS-versioner, links til hjemmesider og GitHub står i `plugins.toml`
og skrives automatisk ind i alle plugins. Ret dem dér – ikke i den enkelte `metadata.txt`.
Kør evt. `python tools/build.py sync` for også at opdatere filerne i repo'et.

## Nyt plugin

```
python tools/build.py import Nyt_plugin.zip timflow     # eller anlaeg
python tools/build.py check
```

Mappenavnet i zip-filen bliver plugin-mappen og skal være et gyldigt Python-navn
(små bogstaver, `_`, ingen æøå).

## Test lokalt før push

```
python tools/build.py check          # validerer alle plugins
python tools/build.py build          # bygger til _site/ – åbn _site/index.html
```

## Hjemmesiderne

På vandstande.dk/timflow indsættes:

```html
<div data-vandstande-plugins="timflow"></div>
<script src="https://<bruger>.github.io/qgis-plugins/embed.js" defer></script>
```

og på vandstande.dk/anlaeg det samme med `"anlaeg"`. Boksen viser altid nyeste version,
downloadlink og repository-URL'en med en kopiér-knap. Tilføj `data-install="nej"` for kun
at vise pluginsene.

Faste downloadlinks (altid nyeste version): `https://<bruger>.github.io/qgis-plugins/zips/<mappe>.zip`
