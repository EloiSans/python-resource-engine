# Resource Engine

## Proposta d'eina per construir backends de negoci declaratius

**Estat:** proposta inicial (v3: afegida l'assistència a agents d'IA)\
**Llenguatge objectiu:** Python\
**Framework d'entrada previst:** FastAPI

------------------------------------------------------------------------

## 1. Resum executiu

Volem crear una eina reutilitzable per desenvolupar backends
d'aplicacions de negoci amb menys codi repetitiu i una estructura
coherent.

L'eina proporcionarà un **motor de recursos (Resource Engine)** que
permetrà definir les entitats d'una aplicació de manera declarativa i
exposar-ne automàticament les operacions CRUD habituals. El comportament
de cada recurs es podrà ampliar mitjançant polítiques d'autorització
(*Gates*), hooks del cicle de vida i màquines d'estats (*State
Machines*).

L'eina no pretén substituir l'arquitectura de l'aplicació ni convertir
tota la lògica de negoci en callbacks. Es construirà seguint els
principis de l'**arquitectura hexagonal**, mantenint el domini
independent de FastAPI, SQLAlchemy i altres detalls d'infraestructura.

El resultat esperat és una base comuna per a aplicacions Python en què
les operacions habituals siguin automàtiques, però les regles de negoci
continuïn sent explícites, testejables i sota el control de cada mòdul.

## 2. Problema que volem resoldre

En molts backends, cada entitat requereix implementar repetidament:

-   Endpoints per consultar, crear, modificar i eliminar registres.
-   Validació de dades d'entrada i sortida.
-   Comprovacions d'autorització.
-   Accés a dades i gestió de transaccions.
-   Lògica abans i després de les operacions.
-   Gestió dels estats i de les transicions possibles.
-   Publicació d'esdeveniments i execució d'efectes secundaris.

Aquesta implementació repetitiva provoca duplicació, diferències de
comportament entre recursos i més esforç de manteniment.

Alhora, les aplicacions de negoci necessiten operacions que van més
enllà del CRUD: aprovar, rebutjar, publicar, cancel·lar, validar o
completar processos. Aquestes operacions sovint tenen regles
d'autorització, precondicions, transicions d'estat i efectes secundaris.

Volem centralitzar els mecanismes comuns sense imposar una implementació
rígida de la lògica específica de cada negoci.

## 3. Objectius

### 3.1. Objectius principals

1.  **Definició declarativa de recursos:** descriure les entitats i les
    operacions disponibles sense implementar manualment cada endpoint.
2.  **CRUD automàtic:** generar les operacions estàndard de consulta i
    modificació a partir de la definició del recurs.
3.  **Autorització centralitzada:** permetre definir polítiques per
    operació, usuari, context i registre.
4.  **Lifecycle extensible:** oferir hooks tipats en els punts
    rellevants del cicle de vida d'una operació.
5.  **Gestió explícita d'estats:** integrar màquines d'estats per
    definir i executar transicions vàlides.
6.  **Arquitectura hexagonal:** separar domini, aplicació i
    infraestructura mitjançant ports i adaptadors.
7.  **Operacions personalitzades:** permetre casos d'ús específics que
    comparteixin els mecanismes comuns del motor.
8.  **Testabilitat:** facilitar tests unitaris del domini i de les
    polítiques, així com tests d'integració dels recursos.
9.  **Documentació funcional generada:** obtenir documentació funcional
    coherent a partir de les mateixes definicions que governen l'execució.
10. **Assistència a agents d'IA:** el framework s'ha d'autoexplicar als
    assistents d'IA que l'utilitzin per programar, a partir de les
    mateixes definicions, i oferir-los eines per verificar el codi que
    generen.

### 3.2. Objectius complementaris

-   Generació automàtica d'OpenAPI a través de FastAPI.
-   Auditoria de les operacions i de les transicions d'estat.
-   Publicació d'esdeveniments després de confirmar les transaccions.
-   Possibilitat d'utilitzar diferents implementacions de persistència.
-   Reutilització del motor en diferents aplicacions.
-   Generació de documentació funcional en Markdown i diagrames Mermaid.
-   Validació automàtica de la cobertura documental durant CI/CD.
-   Instal·lació de skills i context per a agents d'IA (Claude, Cursor,
    Copilot i altres compatibles amb `AGENTS.md`).
-   Eines de línia de comandes amb sortida estructurada que permetin als
    agents inspeccionar i validar el sistema.

## 4. Principis de disseny

### 4.1. Declaratiu per defecte, explícit quan calgui

Les operacions habituals s'han de poder generar a partir de metadades
del recurs. Les regles específiques del negoci, en canvi, han de
continuar sent explícites.

### 4.2. El domini no depèn del framework

Les entitats, les regles de negoci i les màquines d'estats no han de
dependre de FastAPI ni de SQLAlchemy.

### 4.3. Un recurs no és una taula de base de dades

Un `Resource` representa una capacitat de l'aplicació sobre una entitat
o conjunt d'entitats. No ha d'estar obligat a correspondre directament
amb un model ORM o una taula.

### 4.4. CRUD i processos de negoci són conceptes diferents

El CRUD cobreix les operacions genèriques sobre recursos. Els processos
de negoci s'expressen com a casos d'ús o accions específiques, encara
que utilitzin el mateix motor d'execució.

### 4.5. La documentació forma part de les definicions

Les definicions declaratives han de ser també una font d'informació per
generar documentació funcional. Els docstrings de Python seran el
mecanisme principal per descriure la intenció i el comportament esperat
dels elements del sistema.

La documentació generada no substituirà les definicions executables:
les polítiques continuaran imposant permisos, les màquines d'estats
validant transicions i els casos d'ús executant processos.

### 4.6. Els efectes externs han de ser consistents

Les notificacions, els emails i la publicació de missatges no s'han
d'executar com si formessin part d'una transacció SQL. S'han de
gestionar després del commit o mitjançant un patró Outbox quan calgui
garantir-ne l'entrega.

### 4.7. El framework s'explica a les persones i a les màquines

Les mateixes definicions que generen documentació funcional per a
persones han de generar context per als agents d'IA. No hi haurà una
documentació paral·lela per a IA mantinguda a mà: el coneixement del
projecte s'obté per introspecció i el coneixement del framework es
distribueix versionat amb el paquet.

Les regles importants no s'han de limitar a estar descrites: sempre que
sigui possible, s'han de poder comprovar automàticament. Un agent
treballa millor quan pot verificar el que ha escrit que quan només ha
llegit com s'hauria de fer.

------------------------------------------------------------------------

## 5. Conceptes principals

### 5.1. Resource

És la definició declarativa d'un recurs de l'aplicació. Agrupa:

-   Entitat o model de domini associat.
-   Schemas d'entrada i sortida.
-   Operacions CRUD habilitades.
-   Gates associats a les operacions.
-   Hooks del cicle de vida.
-   State Machine, si el recurs té un procés governat per estats.
-   Accions o operacions específiques.

Exemple conceptual:

``` python
class InvoiceResource(Resource):
    model = Invoice

    schemas = {
        "create": InvoiceCreate,
        "update": InvoiceUpdate,
        "read": InvoiceRead,
    }

    operations = ["list", "get", "create", "update", "delete"]

    gates = {
        "list": InvoiceGates.list,
        "get": InvoiceGates.view,
        "create": InvoiceGates.create,
        "update": InvoiceGates.update,
        "delete": InvoiceGates.delete,
    }

    hooks = {
        "before_create": [validate_customer],
        "after_create": [record_creation],
        "before_update": [validate_status],
    }

    state_machine = InvoiceStateMachine
```

És un exemple il·lustratiu: la sintaxi definitiva s'haurà de validar
durant el disseny de l'API del motor.

### 5.2. Resource Manager

És el component d'aplicació que executa les operacions dels recursos.

Les seves responsabilitats inclouen:

-   Rebre una operació i el seu context.
-   Resoldre el recurs i l'operació corresponents.
-   Executar els gates.
-   Executar els hooks en l'ordre establert.
-   Validar les dades.
-   Coordinar repositoris i transaccions.
-   Executar una transició d'estat quan correspongui.
-   Gestionar els esdeveniments resultants.

El `ResourceManager` no ha de conèixer els detalls de FastAPI ni de
SQLAlchemy.

### 5.3. Gates / Policies

Els gates determinen si un actor pot executar una operació sobre un
recurs concret.

Han de poder considerar:

-   L'usuari o actor que fa la petició.
-   L'operació sol·licitada.
-   El recurs i el registre afectat.
-   El context de l'aplicació, com ara l'organització o el tenant.
-   Altres atributs necessaris per prendre la decisió.

Cal distingir entre:

-   **Autorització:** si l'actor pot executar l'acció.
-   **Regles de domini:** si l'acció és vàlida segons l'estat i les
    invariants del negoci.

Per exemple, un usuari pot tenir permís per aprovar factures, però una
factura pagada no es pot aprovar de nou. La primera comprovació és una
policy; la segona és una regla del domini o de la màquina d'estats.

### 5.4. Hooks

Els hooks són punts d'extensió del cicle de vida de les operacions.

Es preveu suport per a funcions síncrones i asíncrones, amb context
tipat i accés controlat a les dades necessàries.

Possibles punts del cicle de vida:

-   `before_validate`
-   `after_validate`
-   `before_create` / `after_create`
-   `before_update` / `after_update`
-   `before_delete` / `after_delete`
-   `before_transition` / `after_transition`
-   `after_commit`

Els hooks han de tenir responsabilitats ben delimitades. No han de
convertir-se en una via per amagar processos de negoci complexos ni
substituir els casos d'ús.

Els hooks posteriors a la persistència però anteriors al commit han de
ser transaccionals i no haurien de produir efectes externs
irreversibles. Els efectes externs s'han de publicar després del commit
o mitjançant Outbox.

### 5.5. State Machines

Les màquines d'estats defineixen els estats possibles d'una entitat i
les transicions permeses entre aquests estats.

La integració amb `python-statemachine` permetrà:

-   Declarar els estats i les transicions.
-   Validar si una transició és possible.
-   Executar accions i comprovacions associades.
-   Integrar les transicions amb gates i hooks.
-   Exposar les transicions com a operacions de l'API.
-   Registrar l'historial de canvis d'estat.

Exemple conceptual:

``` python
class InvoiceStateMachine(StateMachine):
    draft = State(initial=True)
    pending = State()
    approved = State()
    rejected = State()
    paid = State(final=True)

    submit = draft.to(pending)
    approve = pending.to(approved)
    reject = pending.to(rejected)
    pay = approved.to(paid)
```

El motor ha de coordinar l'execució de la màquina, la persistència del
nou estat i la transacció. La màquina d'estats no ha de gestionar
directament sessions SQLAlchemy.

Inicialment es preveu una màquina d'estats per recurs, però el disseny
hauria de permetre evolucionar cap a múltiples màquines independents per
entitat si apareix aquesta necessitat.

### 5.6. Actions / Use Cases

No totes les operacions són CRUD ni transicions d'estat.

El motor ha de permetre registrar accions específiques, com ara:

-   `approve_invoice`
-   `generate_invoice`
-   `import_readings`
-   `recalculate_totals`
-   `cancel_subscription`

Aquestes accions s'implementaran com a casos d'ús explícits. Podran
reutilitzar els mecanismes de context, autorització, transaccions, hooks
i events del motor.

------------------------------------------------------------------------

## 5.7. Functional Documentation

El framework incorporarà un **Documentation Engine** que generarà
documentació funcional a partir de les definicions del sistema. Aquesta
capa serà independent del Runtime Engine i no participarà en l'execució
de les operacions.

L'objectiu és mantenir una única font de veritat: el mateix codi que
defineix els recursos, les operacions, els permisos i els processos
permetrà generar documentació funcional navegable.

### Docstrings com a font principal

Els docstrings natius de Python seran el mecanisme principal per
documentar la intenció funcional. Són idiomàtics, visibles al costat
del codi i compatibles amb les eines habituals de l'ecosistema Python.

S'utilitzaran per descriure, com a mínim:

-   Resources i entitats funcionals.
-   Operacions CRUD i operacions específiques.
-   Gates i policies.
-   Hooks i validacions.
-   State Machines, estats i transicions.
-   Actions / Use Cases.
-   Events i excepcions funcionals rellevants.

Exemple:

```python
class InvoiceResource(Resource):
    """
    Gestió de factures de l'organització.

    Permet crear, consultar i modificar factures, així com
    gestionar-ne el procés d'aprovació.

    Business area:
        Facturació

    Actors:
        - Administrador
        - Comptable
        - Responsable
    """

    model = Invoice
    operations = ["list", "get", "create", "update", "delete"]
```

Una policy també ha de poder explicar la regla funcional que aplica:

```python
async def can_update_invoice(ctx: PermissionContext) -> bool:
    """
    Permet modificar una factura quan:

    - L'actor té el rol de comptable.
    - La factura pertany a la seva organització.
    - La factura està en estat esborrany.
    """
    ...
```

### Convencions per als docstrings

Es definirà una convenció comuna, basada en l'estil Google Python
Docstrings o NumPy Docstrings, amb seccions funcionals consistents.
Les seccions aplicables dependran del tipus d'element:

| Secció | Finalitat |
|---|---|
| Description | Què representa o què fa l'element |
| Actors | Perfils o actors que hi intervenen |
| Preconditions | Condicions que han de complir-se abans |
| Postconditions | Resultat garantit després de l'execució |
| Business Rules | Regles funcionals que s'apliquen |
| Effects | Efectes produïts pel procés |
| Exceptions | Errors funcionals possibles |
| Examples | Exemples d'ús o escenaris |

No serà obligatori incloure totes les seccions a tots els docstrings.
El framework definirà quines són obligatòries per a cada tipus
d'element.

Per interpretar les seccions estructurades es podrà utilitzar
`docstring-parser`, evitant desenvolupar un parser propi en la primera
versió.

### Separació entre documentació i comportament executable

Els docstrings descriuen la intenció funcional, però no són executats
pel Runtime Engine ni substitueixen la lògica real.

| Informació | Font de veritat |
|---|---|
| Descripció funcional | Docstring |
| Operacions habilitades | Definició del Resource |
| Permisos efectius | Policies / Gates |
| Estats i transicions vàlides | State Machine |
| Validacions i comportaments | Validators / Hooks / Use Cases |
| Resultats i efectes reals | Implementació executable |

Per exemple, el docstring d'una policy pot explicar que un comptable
només pot modificar factures de la seva organització en estat
d'esborrany. La policy és qui ha d'imposar efectivament aquesta
restricció.

No s'intentarà inferir automàticament regles de negoci a partir de
l'anàlisi del codi Python o de frases en llenguatge natural. Si la
lògica executable no es pot descriure amb precisió, la descripció
funcional s'haurà d'especificar explícitament.

### Components del Documentation Engine

El Documentation Engine tindrà quatre components principals:

1.  **Documentation Introspector:** inspecciona el registre de recursos
    i els elements associats, obtenint metadades i docstrings.
2.  **Documentation Model:** construeix una representació funcional
    normalitzada, independent del format de sortida.
3.  **Documentation Generators:** transformen el model en diferents
    formats.
4.  **Documentation Validator:** comprova que els elements rellevants
    tinguin documentació i que les seccions obligatòries estiguin
    presents.

Generadors previstos:

-   Markdown: documentació funcional compatible amb MkDocs.
-   Mermaid: diagrames de processos, relacions i màquines d'estats.
-   JSON: exportació estructurada per a altres eines.
-   HTML: possible format de publicació directa en una fase posterior.

El model intermedi evita que la introspecció depengui d'un generador
concret i permet afegir formats sense modificar les definicions dels
mòduls.

### Documentació generada

A partir de les definicions i els docstrings, el sistema podrà generar:

-   Catàleg de recursos i entitats funcionals.
-   Catàleg d'operacions i casos d'ús.
-   Documentació dels camps i schemas.
-   Matrius de permisos per actor, recurs i operació.
-   Diagrames de màquines d'estats i transicions.
-   Diagrames dels processos de negoci.
-   Catàleg d'esdeveniments i efectes.
-   Relació d'errors i excepcions funcionals.

Les matrius i els diagrames han de combinar les descripcions dels
docstrings amb les definicions executables registrades. Quan una regla
no es pugui representar de manera fiable, s'haurà d'indicar com a
descripció textual i no presentar-la com una garantia inferida.

### Validació documental

El Documentation Validator es podrà executar durant CI/CD per detectar
elements sense documentar o amb informació incompleta. Per exemple:

```text
Documentation validation

OK  InvoiceResource has a description
OK  CRUD operations are documented
OK  State machine states and transitions are documented
WARN InvoiceGates.delete has no functional description
WARN ApproveInvoice has no documented postconditions
```

La política de validació podrà començar en mode informatiu i evolucionar
cap a errors de CI per als elements obligatoris.

------------------------------------------------------------------------

## 5.8. AI Assistance

El framework ha de poder autoexplicar-se als assistents d'IA que
l'utilitzin per programar, de manera que generin codi coherent amb el
motor i amb l'arquitectura sense haver de llegir-ne tot el codi font.

Aquesta capa, l'**AI Context Engine**, reutilitza el Documentation
Introspector i el Documentation Model. És un consumidor més de la mateixa
font de veritat, igual que els generadors Markdown o Mermaid, i no
participa en l'execució de les operacions.

### Tres capes de coneixement

Un agent necessita tres tipus d'informació diferents, que canvien a
ritmes diferents:

| Capa | Què explica | Com es lliura | Quan canvia |
|---|---|---|---|
| Coneixement del framework | Com s'escriuen recursos, gates, hooks i màquines d'estats; ordre del cicle de vida; antipatrons | Skill i `llms.txt` inclosos al paquet | Amb cada versió del paquet |
| Coneixement del projecte | Quins recursos, operacions, gates, estats i accions té l'aplicació | Context generat per l'Introspector | Amb cada canvi de codi |
| Eines de verificació | Si el codi escrit és correcte i respecta les regles | CLI amb sortida JSON i, en el futur, servidor MCP | En cada execució |

### Skill inclosa al paquet

El paquet distribuirà una skill en el format estàndard d'Agent Skills
(`SKILL.md`, referències, exemples i scripts), versionada juntament amb
el motor:

```text
resource_engine/
└── ai/
    ├── skill/
    │   ├── SKILL.md              # quan s'aplica i flux de treball
    │   ├── references/
    │   │   ├── resource.md       # API declarativa
    │   │   ├── lifecycle.md      # ordre contractual de gates, hooks i commit
    │   │   ├── state-machines.md
    │   │   ├── gates.md
    │   │   ├── docstrings.md     # convenció de seccions
    │   │   └── anti-patterns.md  # errors habituals i la seva correcció
    │   └── examples/
    │       └── invoices/         # mòdul de referència complet i testejat
    ├── llms.txt
    └── templates/                # plantilles per a AGENTS.md i altres eines
```

El `SKILL.md` ha de ser breu i orientat al procés: quins passos cal
seguir per afegir un recurs, una acció o una màquina d'estats, i quines
comandes s'han d'executar per verificar el resultat.

Ha d'explicitar les regles que no es poden trencar, entre altres:

-   No produir efectes externs en hooks anteriors a `after_commit`.
-   No modificar l'estat mitjançant `PATCH`; fer-ho amb transicions.
-   No importar FastAPI, SQLAlchemy ni Pydantic des de `domain/`.
-   Implementar els processos de negoci complexos com a casos d'ús, no
    com a hooks.
-   Documentar amb docstrings els elements funcionals segons la
    convenció.

El fitxer `anti-patterns.md`, amb exemples de codi incorrecte i la
versió corregida, és especialment important per evitar els errors més
habituals dels agents.

El mòdul d'exemple `invoices` s'executarà als tests del framework per
garantir que l'exemple que llegeixen els agents sempre és correcte.

### Instal·lació explícita

Un paquet Python no pot executar codi en instal·lar-se des d'un wheel, i
tampoc seria desitjable que modifiqués el repositori sense permís. Per
això la integració amb els agents s'instal·larà amb una comanda
explícita:

```bash
resource-engine ai install --target claude,agents,cursor,copilot
```

La comanda:

-   Copia la skill a `.claude/skills/resource-engine/`.
-   Crea o actualitza un **bloc gestionat** a `AGENTS.md`, delimitat per
    marcadors (`<!-- resource-engine:begin -->` /
    `<!-- resource-engine:end -->`), sense modificar el contingut escrit
    per l'equip.
-   Genera, si es demana, els fitxers equivalents per a altres eines
    (`.cursor/rules/`, `.github/copilot-instructions.md`) a partir de les
    mateixes plantilles.
-   Desa la versió instal·lada en un fitxer de bloqueig per detectar
    desajustos entre la skill i la versió del paquet.

`AGENTS.md` serà el format comú de referència. Els formats específics de
cada eina es tractaran com a adaptadors, seguint el mateix criteri que la
resta de l'arquitectura.

La comanda `resource-engine ai sync` actualitzarà la skill i el context
generat. En CI es podrà comprovar que el context està al dia, de la
mateixa manera que es valida la documentació funcional.

### Context del projecte generat

Un generador `ai_context` del Documentation Engine produirà un catàleg
compacte de l'aplicació, pensat per ser llegit per models:

```markdown
## Resources
- invoices (modules/invoices) — ops: list, get, create, update, delete
  state_machine: draft→pending→{approved, rejected}; approved→paid
  gates: update=can_update_invoice ("comptable, pròpia organització, esborrany")
  actions: approve_invoice, recalculate_totals
```

Aquest catàleg dona a l'agent el mapa de l'aplicació sense haver de
llegir tots els mòduls, i reutilitza els docstrings definits a la secció
5.7. Les regles que no es puguin representar de manera fiable es
presentaran com a descripció textual, amb el mateix criteri que la
documentació funcional.

### Eines de verificació

La CLI del framework oferirà comandes amb sortida `--json` perquè els
agents en puguin interpretar el resultat:

| Comanda | Finalitat |
|---|---|
| `resource-engine inspect [recurs]` | Mostra la definició efectiva resolta: hooks en l'ordre real, gates, schemas i transicions |
| `resource-engine check` | Lints d'arquitectura i de convencions del motor |
| `resource-engine scaffold resource\|action\|state-machine` | Genera de manera determinista el codi repetitiu d'un element nou |
| `resource-engine docs validate` | Validació de la cobertura documental (secció 5.7) |

`resource-engine check` ha de detectar, com a mínim:

-   Imports d'infraestructura dins de `domain/` (es pot basar en
    `import-linter`).
-   Operacions habilitades sense gate associat.
-   Camps d'estat exposats en schemas de creació o modificació.
-   Hooks registrats en punts incompatibles amb el seu tipus d'efecte,
    quan es puguin identificar.

El *scaffolding* redueix errors: el codi estructural es genera de manera
determinista i l'agent només ha d'escriure la lògica de negoci.

En una fase posterior, `resource-engine mcp` podrà exposar aquestes
mateixes funcions com a servidor MCP perquè els agents les cridin
directament com a eines.

### Errors orientats a la correcció

Les excepcions del motor tindran codis estables i indicaran com
resoldre el problema i on trobar-ne l'explicació:

```text
RE-E104: El hook 'send_email' registrat a 'before_create' produeix efectes externs.
Mou-lo a 'after_commit' o publica un esdeveniment.
Vegeu references/lifecycle.md#efectes-externs
```

Un missatge accionable permet a l'agent corregir l'error sense
intervenció; un traceback genèric sovint no.

### Avaluació

L'eficàcia de la skill es podrà mesurar amb tasques de referència (per
exemple, «afegeix un recurs amb gate i màquina d'estats») executades per
un agent amb la skill instal·lada i sense, comparant si el resultat
supera `check`, `docs validate` i els tests.

------------------------------------------------------------------------

## 6. Arquitectura hexagonal

L'eina seguirà els principis de l'arquitectura hexagonal (Ports &
Adapters).

### 6.1. Domain

Conté el model i les regles de negoci:

-   Entitats i value objects.
-   Invariants.
-   State Machines.
-   Domain Events.
-   Regles de domini.

El domini no ha de dependre de FastAPI, SQLAlchemy, Pydantic ni de
serveis externs.

### 6.2. Application

Conté l'orquestració dels casos d'ús:

-   Resource Engine i Resource Manager.
-   Operacions CRUD genèriques.
-   Accions específiques.
-   Gates i polítiques d'aplicació.
-   Hooks d'aplicació.
-   Ports de repositori, Unit of Work i publicació d'esdeveniments.

### 6.3. Infrastructure

Implementa els detalls tècnics:

-   Repositoris SQLAlchemy.
-   Gestió de sessions i transaccions.
-   Adaptadors de missatgeria.
-   Integracions amb serveis externs.
-   Persistència de l'auditoria i de l'historial d'estats.

### 6.4. Adapters

Exposen el sistema o permeten interactuar-hi:

-   Adaptador HTTP amb FastAPI.
-   Adaptadors de workers o consumidors de missatges.
-   Adaptadors CLI o tasques programades.
-   Adaptadors de persistència que implementen els ports definits.

Les dependències de codi han d'apuntar cap al domini i les abstraccions
internes. La infraestructura implementa els ports definits per les capes
internes.

## 7. Flux d'execució d'una operació

Una operació CRUD o una acció hauria de seguir un pipeline comú.

Exemple: aprovar una factura.

1.  L'adaptador HTTP rep la petició.
2.  Es construeix el context d'execució amb l'actor, les dades i la
    traça de la petició.
3.  El Resource Manager resol el recurs i l'operació.
4.  S'executa el gate d'autorització.
5.  S'obre la unitat de treball.
6.  Es carrega l'entitat mitjançant el repositori.
7.  S'executen els hooks previs.
8.  Es comproven les precondicions i s'executa la transició de la State
    Machine.
9.  Es persisteixen els canvis i l'auditoria dins la mateixa transacció.
10. S'executen els hooks interns posteriors.
11. Es confirma la transacció.
12. Es publiquen els esdeveniments i efectes post-commit.

L'ordre exacte dels hooks i les comprovacions s'ha de definir com a part
del contracte del motor. Els errors han de provocar rollback quan
l'operació encara forma part de la transacció.

## 8. API generada

Per a un recurs anomenat `invoices`, el motor podrà exposar
automàticament operacions com:

  Mètode   Endpoint           Operació
  -------- ------------------ -----------
  GET      `/invoices`        Llistar
  GET      `/invoices/{id}`   Consultar
  POST     `/invoices`        Crear
  PATCH    `/invoices/{id}`   Modificar
  DELETE   `/invoices/{id}`   Eliminar

Les transicions es podran exposar com a operacions específiques:

  Mètode   Endpoint                               Operació
  -------- -------------------------------------- ----------
  POST     `/invoices/{id}/transitions/submit`    Enviar
  POST     `/invoices/{id}/transitions/approve`   Aprovar
  POST     `/invoices/{id}/transitions/reject`    Rebutjar

Les accions de negoci també podran tenir endpoints propis.

Els endpoints generats hauran de respectar els schemas, les operacions
habilitades i les polítiques del recurs. FastAPI serà responsable de
l'adaptació HTTP i de la documentació OpenAPI, no de contenir la lògica
de negoci.

------------------------------------------------------------------------

## 9. Stack tecnològic proposat

La selecció inicial de tecnologies és:

  -----------------------------------------------------------------------
  Àmbit                   Tecnologia              Responsabilitat
  ----------------------- ----------------------- -----------------------
  Llenguatge              Python                  Implementació

  API HTTP                FastAPI                 Adaptador d'entrada i
                                                  OpenAPI

  Validació i DTOs        Pydantic                Schemas d'entrada i
                                                  sortida

  Persistència            SQLAlchemy              ORM i accés a base de
                                                  dades

  Migracions              Alembic                 Evolució de l'esquema

  State Machines          python-statemachine     Estats i transicions

  Autorització            Implementació pròpia    Policies RBAC / ABAC
                          inicialment; Casbin si  
                          cal                     

  Testing                 pytest                  Tests unitaris i
                                                  d'integració
  -----------------------------------------------------------------------

La selecció és una proposta inicial. Cal validar especialment la
integració de `python-statemachine` amb persistència asíncrona,
transaccions i concurrència.

No es considera necessari incorporar una llibreria específica per als
hooks en la primera versió: es pot implementar un dispatcher propi,
petit i tipat.

## 10. Estructura orientativa del projecte

Es proposa organitzar el codi per mòduls de negoci, mantenint les capes
hexagonals dins de cada mòdul i separant el motor compartit.

``` text
src/
├── core/
│   ├── application/
│   │   ├── resources/
│   │   │   ├── resource.py
│   │   │   ├── manager.py
│   │   │   ├── registry.py
│   │   │   └── operations/
│   │   ├── hooks/
│   │   ├── gates/
│   │   ├── documentation/
│   │   │   ├── models.py
│   │   │   ├── introspector.py
│   │   │   ├── validator.py
│   │   │   └── generators/
│   │   │       ├── markdown.py
│   │   │       ├── mermaid.py
│   │   │       ├── json.py
│   │   │       └── ai_context.py
│   │   └── ports/
│   │       ├── repository.py
│   │       ├── unit_of_work.py
│   │       └── event_publisher.py
│   ├── domain/
│   │   ├── events.py
│   │   └── exceptions.py
│   ├── ai/
│   │   ├── skill/
│   │   │   ├── SKILL.md
│   │   │   ├── references/
│   │   │   └── examples/
│   │   ├── templates/
│   │   ├── llms.txt
│   │   └── installer.py
│   └── cli/
│       ├── inspect.py
│       ├── check.py
│       ├── scaffold.py
│       └── ai.py
│
├── modules/
│   ├── invoices/
│   │   ├── domain/
│   │   │   ├── entities.py
│   │   │   ├── state_machine.py
│   │   │   └── events.py
│   │   ├── application/
│   │   │   ├── resource.py
│   │   │   ├── gates.py
│   │   │   ├── hooks.py
│   │   │   └── use_cases/
│   │   ├── documentation.py
│   │   └── infrastructure/
│   │       ├── models.py
│   │       └── repository.py
│   └── users/
│       └── ...
│
├── adapters/
│   ├── http/
│   │   ├── routers/
│   │   ├── schemas/
│   │   └── dependencies.py
│   └── persistence/
│       ├── database.py
│       └── unit_of_work.py
│
└── bootstrap.py
```

Aquesta estructura és orientativa. La ubicació definitiva de cada
component s'ha de decidir segons les dependències reals: per exemple,
els schemas HTTP no han de convertir-se automàticament en models del
domini.

------------------------------------------------------------------------

## 11. Requisits transversals

### 11.1. Transaccions i consistència

-   Les operacions que modifiquen dades han d'executar-se dins d'una
    unitat de treball.
-   Els canvis de l'entitat i el seu estat han de persistir de manera
    atòmica.
-   Els errors durant l'operació han de provocar rollback.
-   Els efectes externs s'han d'executar després del commit o mitjançant
    Outbox.
-   Cal definir el comportament davant d'operacions concurrents i
    transicions simultànies.

### 11.2. Auditoria

El sistema hauria de permetre registrar:

-   Actor que executa l'operació.
-   Recurs i identificador del registre.
-   Operació o transició executada.
-   Resultat de l'operació.
-   Timestamp.
-   Estat anterior i posterior, quan correspongui.
-   Metadades de correlació i traçabilitat.

L'auditoria funcional no hauria de dependre exclusivament dels events
interns de SQLAlchemy, perquè les operacions també poden arribar des
d'altres adaptadors.

### 11.3. Seguretat

-   Autorització per operació i, quan calgui, per registre i camp.
-   Aïllament entre organitzacions o tenants si l'aplicació ho
    requereix.
-   No exposar automàticament tots els camps del model de persistència.
-   Validar les dades d'entrada amb schemas específics.
-   Evitar que un `PATCH` permeti modificar camps protegits, com ara
    l'estat, al marge de les transicions autoritzades.

### 11.4. Observabilitat

-   Logs estructurats.
-   Identificadors de correlació.
-   Mesura de durada de les operacions.
-   Registre d'errors de hooks, gates i transicions.
-   Traçabilitat dels esdeveniments i de les tasques post-commit.

------------------------------------------------------------------------

## 12. Abast proposat per a una primera versió (MVP)

La primera versió hauria de validar el concepte amb un conjunt reduït de
funcionalitats.

### Inclòs

1.  Definició declarativa de `Resource`.
2.  Registre de recursos.
3.  Generació d'endpoints CRUD amb FastAPI.
4.  Schemas Pydantic diferenciats per operació.
5.  Repositoris i Unit of Work mitjançant protocols.
6.  Gates per operació i registre.
7.  Hooks síncrons i asíncrons.
8.  Integració amb `python-statemachine`.
9.  Exposició de transicions com a operacions.
10. Gestió transaccional de les operacions.
11. Tests unitaris i d'integració.
12. Documentació OpenAPI.
13. Convencions de docstrings per als elements funcionals.
14. Introspector i model documental intermedi.
15. Generació inicial de documentació Markdown i diagrames Mermaid.
16. Validació bàsica de cobertura documental.
17. Skill bàsica inclosa al paquet, amb referències, antipatrons i el
    mòdul d'exemple `invoices`.
18. Comanda `resource-engine ai install` per a Claude i `AGENTS.md`.
19. Generador `ai_context` amb el catàleg del projecte.
20. Comandes `inspect` i `check` amb sortida `--json`.
21. Codis d'error estables i accionables per a les excepcions del motor.

### Fora de l'abast inicial

-   Editor visual de recursos o màquines d'estats.
-   Configuració de policies des d'una interfície d'administració.
-   Motor de workflows distribuïts.
-   Orquestració de processos de llarga durada.
-   Generació automàtica de tota la capa de domini.
-   Sistema avançat de plugins de tercers.
-   Suport complet per a múltiples màquines d'estats per recurs.
-   Servidor MCP per a agents d'IA.
-   Adaptadors específics per a altres eines d'IA a part de Claude i
    `AGENTS.md` (Cursor, Copilot...).
-   Scaffolding complet de tots els tipus d'element.

Aquestes funcionalitats es podran valorar quan el nucli estigui validat.

## 13. Criteris d'acceptació del MVP

El MVP es considerarà validat quan sigui possible:

1.  Crear un recurs declarant el model, els schemas i les operacions.
2.  Registrar-lo i obtenir automàticament els endpoints CRUD.
3.  Executar gates abans de permetre una operació.
4.  Executar hooks en l'ordre documentat.
5.  Rebutjar una operació que incompleixi una regla d'autorització.
6.  Definir una State Machine i exposar-ne les transicions.
7.  Impedir transicions no vàlides.
8.  Persistir l'entitat i el nou estat dins d'una única transacció.
9.  Fer rollback si falla un hook transaccional o una operació de
    persistència.
10. Publicar un esdeveniment post-commit sense exposar la
    infraestructura al domini.
11. Substituir el repositori real per un repositori en memòria en tests.
12. Afegir una acció de negoci personalitzada sense haver de modificar
    el nucli del motor.
13. Generar documentació funcional dels recursos registrats a partir
    dels seus docstrings i metadades.
14. Generar com a mínim un diagrama Mermaid de màquina d'estats.
15. Detectar en CI un recurs o una operació obligatòria sense
    documentació.
16. Instal·lar la skill i el bloc gestionat d'`AGENTS.md` amb una sola
    comanda, sense sobreescriure contingut escrit per l'equip.
17. Detectar en CI que el context d'IA generat no està sincronitzat amb
    el codi o amb la versió del paquet.
18. Detectar amb `check` un import d'infraestructura dins del domini i
    una operació sense gate.
19. Que un agent d'IA, amb la skill instal·lada i sense altra
    intervenció, afegeixi un recurs amb gate i màquina d'estats que
    superi `check`, `docs validate` i els tests.

## 14. Decisions pendents de disseny

Abans de consolidar l'API pública del motor, caldrà resoldre:

-   Si els recursos representen sempre una entitat o poden representar
    agregats i vistes.
-   Com es defineixen els schemas de creació, modificació, consulta i
    resposta.
-   Quin és l'ordre exacte d'execució de gates, hooks, validacions i
    persistència.
-   Com es gestionen els errors i les excepcions de cada extensió.
-   Com s'integren les State Machines amb entitats persistides i
    concurrència.
-   Si l'historial de transicions forma part del motor o és
    responsabilitat de cada mòdul.
-   Com es defineixen els permisos per camps i filtres de consulta.
-   Com es registren i versionen les accions personalitzades.
-   Com es garanteix la idempotència de les operacions sensibles.
-   Quina convenció de docstrings s'adopta (Google o NumPy) i quines
    seccions són obligatòries per a cada tipus d'element.
-   Quin nivell de validació documental serà bloquejant a CI.
-   Quins formats de sortida, a més de Markdown i Mermaid, s'inclouran
    en versions posteriors.
-   Com es gestionen els efectes post-commit i l'Outbox.
-   Nom i estructura definitiva de la CLI.
-   Quines eines d'IA es donaran suport oficialment i com es
    mantindran els seus adaptadors.
-   Quines regles de `check` seran bloquejants a CI.
-   Com s'avaluarà de manera periòdica l'eficàcia de la skill amb
    tasques de referència.
-   Quan s'incorporarà el servidor MCP i quines eines exposarà.

------------------------------------------------------------------------

## 15. Visió final

Volem crear una eina que permeti construir backends de negoci a partir
de recursos declaratius, amb un conjunt de mecanismes comuns i
consistents.

El desenvolupador hauria de poder concentrar-se en definir:

-   Quines entitats existeixen.
-   Quines operacions s'hi poden executar.
-   Qui està autoritzat a executar-les.
-   Quines regles i hooks s'han d'aplicar.
-   Quins estats i transicions formen part del procés de negoci.
-   Quines accions específiques necessita l'aplicació.

El motor s'encarregarà de proporcionar l'execució comuna: CRUD,
autorització, lifecycle, transaccions, persistència i exposició HTTP.

El Documentation Engine utilitzarà els docstrings i les definicions
estructurades per generar documentació funcional coherent i verificable.
Així, el framework oferirà dues visions complementàries del mateix
sistema: la seva execució i la seva explicació funcional.

Aquesta mateixa explicació arribarà també als assistents d'IA: el
framework s'instal·larà amb una skill pròpia, generarà el context de
cada projecte i oferirà eines per verificar el codi, de manera que els
agents el puguin utilitzar de forma eficient i coherent amb les seves
regles.

**La proposta combina arquitectura hexagonal, recursos declaratius,
policies d'autorització, hooks de cicle de vida i màquines d'estats**,
mantenint les regles de negoci explícites i independents de la
infraestructura.
