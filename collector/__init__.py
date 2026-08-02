"""Package del collector Hyperliquid (Fase 0: solo raccolta dati).

Volutamente vuoto: importare `collector` non deve tirarsi dietro websockets e
pyarrow. Chi vuole il processo importa `collector.collector`, chi vuole solo le
funzioni pure di parsing importa `collector.parsing` e non installa niente.
"""
