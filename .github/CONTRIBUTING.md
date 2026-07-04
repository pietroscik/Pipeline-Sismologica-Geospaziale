# Linee Guida per la Contribuzione

Siamo felici che tu voglia contribuire al progetto! Per favore, prenditi un momento per leggere queste linee guida.

## Come Contribuire

### Segnalare Bug

- Utilizza il template [Bug Report](../../issues/new?template=bug_report.md) per segnalare un bug.
- Assicurati che il bug non sia già stato segnalato cercando tra le [Issues](../../issues).
- Includi più dettagli possibili: i passaggi per riprodurlo, la versione del software, il tuo ambiente, e log/screenshot.

### Suggerire Nuove Funzionalità

- Utilizza il template [Feature Request](../../issues/new?template=feature_request.md) per proporre una nuova funzionalità.

### La Tua Prima Pull Request

1.  **Fork & Clone**: Esegui il fork del repository e clonalo in locale.
2.  **Branch**: Crea un nuovo branch per le tue modifiche (`git checkout -b nome-feature-o-fix`).
3.  **Sviluppo**:
    - Installa le dipendenze di sviluppo (`pip install -e .[dev,ml]`).
    - Apporta le tue modifiche al codice.
    - Aggiungi test per le nuove funzionalità o per i bug risolti.
4.  **Verifica**:
    - Formatta il codice con `black .`.
    - Controlla lo stile con `flake8 .`.
    - Esegui tutti i test con `pytest tests/`.
5.  **Commit**: Scrivi messaggi di commit chiari e concisi.
6.  **Push & Pull Request**:
    - Fai il push del tuo branch (`git push origin nome-feature-o-fix`).
    - Apri una Pull Request verso il branch `main` (o `develop`).
    - Compila il template della PR, collegando l'issue che risolvi (es. `Closes #123`).

## Standard di Codice

- Segui lo stile PEP 8.
- Usa `snake_case` per variabili e funzioni.
- Aggiungi docstring a moduli, classi e funzioni.
- Commenta le parti complesse del codice.