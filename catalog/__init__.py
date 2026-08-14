"""Catalogo e integrita' dei dati raccolti dal collector.

Il catalogo e' SOLA LETTURA sulla directory dati: non scrive, non sposta e non
riorganizza niente sotto `data_dir`. Marca le ore sospette, non le esclude:
la decisione su cosa buttare via appartiene a chi legge il report.
"""

from __future__ import annotations

__all__ = ["dataset", "gapwindows", "metrics", "sanity", "spread", "funding", "report"]
