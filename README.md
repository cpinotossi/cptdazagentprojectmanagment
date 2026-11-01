---
title: Projektplanungs-Agent (Demo)
description: Demo eines Microsoft-Foundry-Agents, der über eine Fabric Ontology (Fabric IQ) Projektdaten, Dokumente und E-Mails auswertet
---

## Idee

Alle Daten liegen als Tabellen in einem Fabric Lakehouse. Eine Fabric Ontology gibt ihnen eine fachliche Bedeutung. Der Foundry-Agent greift ausschließlich über das Fabric-IQ-Tool darauf zu.

```text
Project ──Managed By──▶ ProjectManager
ProjectDocument ──Of Project──▶ Project   (Lastenheft, Angebot, Vertrag; Content = PDF-Text)
Email ──About Project──▶ Project          (Kritikalität bewertet der Agent)
```

## Inhalt

| Pfad | Zweck |
|------|-------|
| `infra/main.bicep` | Foundry-Account, Projekt, Modell, Fabric-Kapazität F2, Rollen |
| `scripts/setup.py` | Fabric-Workspace, Lakehouse, Tabellen, Ontology, Foundry-Verbindung, Agent |
| `data/` | Vier Mini-PDFs und drei CSV-Dateien als Testdaten |
| `deploy.sh` | Führt beide Schritte nacheinander aus |

Bicep deckt nur die Azure-Ressourcen ab. Fabric-Inhalte und der Agent liegen auf der Datenebene und werden deshalb per REST und SDK in `scripts/setup.py` angelegt.

## Ausführen

Voraussetzungen: Azure CLI (`az login`), Python 3, Berechtigung zum Anlegen von Ressourcen und Rollen. Im Tenant müssen Fabric aktiv, der Resource Provider `Microsoft.Fabric` registriert und die Fabric-Tenant-Einstellung "Users can create Ontology (preview) items" eingeschaltet sein.

```bash
./deploy.sh
```

Das Skript endet mit einer Testfrage an den Agent. Die F2-Kapazität verursacht Kosten und lässt sich im Azure-Portal pausieren oder mit `az group delete -n rg-projplan-demo` entfernen.

## Hinweise

* Fabric IQ und die Ontology-API sind Preview. Das Setup wurde erfolgreich gegen einen echten Tenant ausgeführt.
* Das Skript verwendet die Anmeldung der Azure CLI. Eine andere Identität (zum Beispiel über Umgebungsvariablen) sieht die Kapazität und den Workspace nicht.
* Die Fabric-Kapazität wird nach dem Bicep-Deployment erst nach einigen Minuten in der Fabric-API sichtbar. Bei einem Fehler "Kapazität nicht gefunden" hilft ein erneuter Aufruf von `./deploy.sh`.
* Alle Spalten sind als Text angelegt, damit das Laden der CSV-Dateien einfach bleibt.
* Die Verbindung nutzt das Entra-Token des angemeldeten Benutzers. Der Benutzer braucht Zugriff auf den Fabric-Workspace.
* Das alte Tool "Fabric data agent" wird am 6. November 2026 abgekündigt und deshalb nicht verwendet.
