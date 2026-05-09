# QGIS Plugin DUSAF 7 - Comuni Lombardi

Plugin QGIS per l'analisi automatizzata dell'uso del suolo nei Comuni lombardi tramite dataset DUSAF 7.0 e confini amministrativi ISTAT 2026.

Il plugin aggiunge:
- un provider Processing dedicato;
- un algoritmo di analisi DUSAF 7 per Comune lombardo;
- un pulsante nella barra strumenti di QGIS;
- controllo geometrico, gestione slivers, calcolo superfici e audit QC-4.

## Installazione

Scaricare lo ZIP dalla sezione Release e installarlo in QGIS tramite:

Plugin → Gestisci e installa plugin → Installa da ZIP

## Dati necessari

Prima dell'esecuzione è necessario caricare nel progetto QGIS:

- DUSAF7
- Com01012026_WGS84

I dati non sono inclusi nel repository e devono essere scaricati dalle fonti ufficiali.

## Licenza

Codice Python distribuito con licenza AGPL-3.0.

Gli stili QML DUSAF riprendono/adattano la simbologia del dataset DUSAF 7.0 di Regione Lombardia, con attribuzione alla fonte.
