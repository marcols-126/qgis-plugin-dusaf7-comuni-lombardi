# Test suite

Test puri (nessuna dipendenza QGIS/Qt) per i validatori e gli helper di
parsing. Si eseguono con:

```bash
pip install pytest
python -m pytest tests/ -v
```

I file testati sono moduli che usano solo la standard library
(`urllib`, `dataclasses`, ecc.) e che non importano `qgis.*`:

- `data_sources/lombardia_comuni_client.py` — validatori + display
  name normalization
- `data_sources/lombardia_dusaf_client.py` — validatori paginazione +
  validazione feature
- `workflow/data_resolver._parse_dusaf_descr` — parsing del DESCR REST

Per i moduli che importano `qgis.core` (layer factory, pipeline, qc,
output, dialog) servono test funzionali dentro QGIS (Python Console o
pytest-qgis), che restano fuori scope di questa suite di partenza.
