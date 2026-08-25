# Metodologia del modello

**value-quant-app** — guida completa a cosa calcola ogni modulo, con quali formule,
e soprattutto **come si leggono i numeri che produce**.

---

## Indice

1. [L'impianto: quattro domande, quattro moduli](#1-limpianto)
2. [Modulo 0 — Profili di settore](#15-profili-di-settore)
3. [Modalità Buffett](#buffett-mode)
4. [Modulo 1 — Quality Score](#2-modulo-1--quality-score)
5. [Modulo 2 — Valuation](#3-modulo-2--valuation)
6. [Modulo 3 — Backtest](#4-modulo-3--backtest)
7. [Modulo 4 — Visualize](#5-modulo-4--visualize)
8. [Come si usano insieme](#6-come-si-usano-insieme)
9. [Glossario](#7-glossario)
10. [Limiti e avvertenze](#8-limiti-e-avvertenze)

---

<a name="1-limpianto"></a>
## 1. L'impianto: quattro domande, quattro moduli

Il modello risponde a quattro domande in sequenza. Ognuna ha senso solo se la
precedente ha già risposto.

| # | Domanda | Modulo | Output |
|---|---|---|---|
| 0 | *Che tipo di azienda è?* | `sectors.py` | profilo di metriche e soglie |
| 1 | *È una buona azienda?* | `quality_score.py` | punteggio 0-100 |
| 2 | *A che prezzo vale la pena comprarla?* | `valuation.py` | fair value e margine di sicurezza |
| 3 | *Questa regola di selezione ha funzionato?* | `backtest.py` | equity curve e metriche di rischio |
| 4 | *Come lo mostro a qualcuno?* | `visualize.py` | tear sheet e grafici |

La logica è quella del value investing classico: **prima la qualità, poi il prezzo**.
Un'azienda mediocre comprata a sconto resta un'azienda mediocre — il tempo lavora
contro di te. Un'azienda eccellente comprata a un prezzo qualsiasi può comunque essere
un pessimo investimento, perché tutto il valore futuro è già nel prezzo di oggi.
Il modello separa i due giudizi apposta: non li mescola mai in un unico numero.

**Un principio attraversa tutti i moduli**: nessun numero esce senza le ipotesi che
l'hanno generato. Ogni output porta con sé una sezione `data_quality` che elenca cosa
è stato stimato e cosa mancava. Un valore intrinseco senza le sue assunzioni è
un'opinione travestita da calcolo.

---


<a name="15-profili-di-settore"></a>
## 1-bis. Modulo 0 — Profili di settore

**File**: `backend/models/sectors.py`
**Domanda**: con quale metro va misurata questa azienda?

### La radice: per una banca il debito è materia prima

In un'azienda industriale il debito è *come ti finanzi* per comprare gli impianti con
cui produci. In una banca il debito — depositi, obbligazioni, raccolta interbancaria —
**è l'input produttivo**: la banca compra denaro a un tasso e lo rivende a un tasso più
alto. Il margine di interesse *è* il ricavo.

Da questo discende tutto il resto:

- **Non esiste l'EBIT.** Non puoi "aggiungere indietro" gli oneri finanziari, perché
  sono il costo del venduto. Quindi niente NOPAT, quindi **niente ROIC**.
- **Debt/Equity 10x è normale.** Una banca con D/E di 1 sarebbe una banca che non fa il
  suo mestiere.
- **Interest Coverage non ha senso**: dividerebbe una grandezza inesistente per il costo
  principale.
- **Current Ratio non è definito**: lo stato patrimoniale bancario non è classificato in
  corrente / non corrente.
- **Il CapEx è irrilevante**, quindi gli Owner Earnings collassano sull'utile netto e non
  aggiungono informazione.

**Il pericolo non è l'errore, è il numero plausibile.** Applicare il profilo industriale
a una banca non fa fallire il calcolo: produce un ROIC, un DCF, un fair value con due
decimali. Un dato mancante avverte chi legge; un numero verosimile ma privo di
significato no. È per questo che i profili esistono.

### Cosa cambia, dimensione per dimensione

| Dimensione | Industriale | Banca | Assicurazione |
|---|---|---|---|
| Redditività primaria | ROIC | **ROTCE** | **Combined ratio** + ROTCE |
| "Margine lordo" | Margine lordo | **NIM** (margine di interesse / attivo) | Rendimento degli investimenti |
| "Margine operativo" | Reddito op. / ricavi | **Efficiency ratio** (costi/ricavi, inverso) | — |
| Generazione di valore | Owner Earnings | Crescita del **patrimonio tangibile/azione** | Crescita del **patrimonio/azione** |
| Solidità #1 | Debt/Equity | **Patrimonio / attivo** (proxy di CET1) | Patrimonio / attivo |
| Solidità #2 | Debt/EBITDA | **Loan-to-Deposit** | Debt/Equity |
| Solidità #3 | Interest Coverage | **Costo del credito** | — |
| Solidità #4 | Current Ratio | — | — |

**Le soglie cambiano di un ordine di grandezza.** Un ROA dell'1% per una banca è buono;
per un industriale è pessimo. È lo stesso numero che significa cose opposte, perché la
banca lavora con leva ~10x. Applicare le soglie industriali (ROA: 1% → 0 punti, 12% →
100) a una banca darebbe **zero a ogni banca del mondo**.

Riferimenti del profilo bancario:

| Metrica | 0 punti | 100 punti | Peso interno |
|---|---|---|---|
| ROTCE medio | 6% | 18% | 35% (profittabilità) |
| ROE medio | 5% | 15% | 20% |
| ROA medio | 0.40% | 1.50% | 20% |
| Cost/Income | 75% | 50% | 15% |
| NIM / attivo | 1.20% | 3.50% | 10% |
| Patrimonio / attivo | 5% | 12% | 40% (solidità) |
| Loan / Deposit | 1.10 | 0.70 | 30% |
| Costo del credito | 1.50% | 0.20% | 30% |

Il **loan-to-deposit sotto 1** significa finanziata dai depositi e non dal mercato: è la
differenza fra una raccolta stabile e una che evapora in una crisi di fiducia.

### Il profilo assicurativo e il problema di Berkshire

Per un'assicurazione il perno è il **combined ratio** = (sinistri + spese) / premi. Sotto
100 significa che l'assicurazione guadagna *sull'attività tecnica*, prima ancora dei
rendimenti finanziari — è il meccanismo del *float* di Buffett: premi incassati oggi,
sinistri pagati domani, cioè leva finanziaria a costo negativo.

Ma c'è un secondo problema, contabile e insidioso. **Dal 2018 le regole GAAP obbligano a
far transitare nel conto economico le plusvalenze e minusvalenze *non realizzate* del
portafoglio titoli.** Per un'assicurazione con un grande portafoglio azionario — Berkshire
sopra tutte — l'utile netto oscilla di decine di miliardi da un anno all'altro seguendo
il mercato, non il business.

Conseguenza pratica: **qualunque metrica di consistenza basata sull'utile netto dichiara
Berkshire "instabile"**, il che è vero della contabilità e falso dell'azienda.

Il profilo assicurativo risponde spostando il peso della consistenza sulla **crescita del
patrimonio netto per azione** (35% della categoria) — il metro con cui Berkshire ha
misurato sé stessa per decenni, e l'unico immune al rumore contabile — e allargando molto
la soglia di variabilità accettata sul ROE (CV fino a 0.90 contro lo 0.60 industriale).

### Come viene riconosciuto il settore

Dalla **struttura del bilancio**, non dall'etichetta:

1. presenza di **depositi** o di **margine di interesse** → banca;
2. presenza di **premi assicurativi**, **riserve tecniche** o **sinistri** → assicurazione;
3. altrimenti → industriale.

Non si usa il campo `sector` di Yahoo perché mette "Financial Services" su banche,
assicurazioni, asset manager e gestori di mercati, che vogliono metriche diverse, ed è un
endpoint spesso non raggiungibile. In caso di dubbio si assume industriale — l'ipotesi più
conservativa — e si può forzare con `--sector`.

### Valutazione: il DCF va sostituito, non adattato

Per un finanziario **non si può calcolare il free cash flow**, perché non si riesce a
separare i flussi operativi da quelli di finanziamento: sono la stessa cosa.

Si sostituisce con il **modello a rendimenti in eccesso** (residual income), lo standard
accademico per le banche:

```
Valore = Patrimonio contabile
       + Σ [(ROE_t − Ke) × Patrimonio_(t−1)] / (1 + Ke)^t
```

L'intuizione è elegante: una banca vale il suo patrimonio contabile, **più** il valore
attualizzato di quanto rende *sopra* il costo del capitale. Se ROE = Ke vale esattamente
il book value, né più né meno — ed è esattamente quello che un test verifica
analiticamente.

Funziona per i finanziari e non per gli industriali perché il patrimonio contabile di una
banca è vicino al valore di mercato dei suoi attivi (crediti e titoli, largamente valutati
a mercato), mentre per un industriale il book value non dice quasi nulla.

**Il ROE del modello sfuma verso Ke** entro l'orizzonte esplicito: i rendimenti in eccesso
si azzerano, quindi **non c'è valore terminale**. È la scelta più conservativa possibile —
la stima non dipende da nessuna ipotesi oltre il decimo anno, che è invece il tallone
d'Achille del DCF.

Da qui deriva anche il **P/B giustificato**:

```
P/B = (ROE − g) / (Ke − g)
```

Una banca che rende il 15% con Ke 10% e crescita 3% dovrebbe trattare a
`(0.15−0.03)/(0.10−0.03)` = **1.7x** il patrimonio tangibile. Se tratta a 1.0x, è a sconto.

> **I due metodi incorporano ipotesi opposte sulla durata del vantaggio.** Il P/B
> giustificato assume che il ROE resti sopra Ke *per sempre*; il residual income lo azzera
> in dieci anni. La forbice fra i due **è** il valore attribuito alla durata del moat, e il
> modello la dichiara con un avviso quando supera il 80%.

**Si sconta al costo dell'equity, mai al WACC.** Il WACC ha senso solo quando il debito è
finanziamento; per una banca sarebbe un errore concettuale, non un'imprecisione.

Al posto del reverse DCF c'è il **ROE implicito nel prezzo**: quale redditività sostenibile
il mercato sta già scontando. Ha lo stesso pregio dell'originale — non richiede di stimare
il futuro, misura le aspettative già incorporate.

### Il limite dichiarato: i ratios di vigilanza

yfinance **non espone** CET1, NPL ratio, LCR, NSFR: quei numeri vivono nelle segnalazioni
regolamentari (FR Y-9C per le holding bancarie USA, FFIEC/FDIC che hanno API pubbliche
gratuite). Il profilo bancario usa quello che si ricostruisce dai prospetti — patrimonio
su attivo come proxy di leva, costo del credito, loan-to-deposit — e **scrive in
`data_quality.missing` che i ratios veri non ci sono**.

Un proxy segnalato è onesto; un proxy spacciato per il ratio vero no.

---


<a name="buffett-mode"></a>
## 1-ter. Modalità Buffett

**Attivazione**: `--buffett` da riga di comando, `mode="buffett"` da codice.

Il modello standard è *ispirato* ai principi di Buffett. Questa modalità li applica
**alla lettera**, ricostruiti dalle fonti primarie: i criteri di acquisizione stampati in
ogni annual report dal 1982, l'appendice alla lettera del 1986, la lettera del 2007 e la
risposta sul tasso di sconto all'assemblea del 1998.

### 1. Il CapEx di mantenimento, stimato invece che approssimato

La definizione del 1986 recita: Owner Earnings = utile riportato **+** ammortamenti e
altre poste non monetarie **−** *la media annua degli investimenti capitalizzati che
l'azienda richiede per mantenere pienamente la propria posizione competitiva e il proprio
volume*.

Quella cifra non compare in nessun bilancio. Il modello standard usava il **CapEx totale**
come proxy — prudenziale, perché sottrae anche gli investimenti di crescita, comprimendo
gli Owner Earnings di un'azienda in espansione.

La modalità Buffett la stima col metodo di **Bruce Greenwald** (Columbia), lo standard
della letteratura value:

```
immobilizzazioni/ricavi = media storica di (immobilizzazioni nette / ricavi)
CapEx di crescita       = immobilizzazioni/ricavi × incremento dei ricavi
CapEx di mantenimento   = CapEx totale − CapEx di crescita
```

L'intuizione: se servono 40 centesimi di impianti per ogni euro di ricavo, crescere di 100
di ricavi richiede 40 di investimento *aggiuntivo*; il resto serviva a stare fermi. Quando
i ricavi calano il CapEx di crescita è zero e tutto il CapEx è di mantenimento.

Ripieghi, in ordine: mancano le immobilizzazioni → si usano gli **ammortamenti**; manca
anche quello → **CapEx totale**, la stima più prudente. Ogni ripiego è annotato.

### 2. Il rendimento sul capitale tangibile

Nella lettera del 2007 Buffett misura le aziende sul *"return on unleveraged net tangible
assets"*, e cita See's Candies **oltre il 200% ante imposte**. La differenza dal ROIC
ordinario è il denominatore:

```
Capitale tangibile = Debito + Patrimonio Netto − Cassa − Avviamento − Immateriali
Rendimento = EBIT / Capitale tangibile      (ante imposte, come lo cita lui)
```

**Perché toglie l'avviamento**: quello è il prezzo pagato in passato per delle
acquisizioni, non il capitale che il business richiede *oggi* per funzionare. Un ROIC che
lo include dice quanto è stato bravo chi ha comprato; questo dice quanto è buono il
business in sé. Su un'azienda cresciuta per acquisizioni i due numeri divergono
moltissimo.

### 3. Debito / Owner Earnings al posto del Debt/EBITDA

Nella lettera del 2000: *"I riferimenti all'EBITDA ci fanno rabbrividire — il management
pensa che la fatina dei denti paghi il CapEx?"*. Nel 2002 rincara: *"Ogni centesimo di
ammortamento che riportiamo è un costo reale"*.

Il profilo Buffett sostituisce quindi il Debt/EBITDA con **gli anni di Owner Earnings
necessari a estinguere il debito**. È la domanda che conta davvero — *quanto ci metterei a
liberarmene con la cassa che il business produce* — ed è insensibile alle poste non
monetarie che rendono l'EBITDA poco affidabile.

### 4. Il tasso di sconto, e perché non basta abbassarlo

All'assemblea del 1998: *"Non scontiamo i flussi futuri al 9% o 10%: usiamo il tasso del
Treasury. Cerchiamo di occuparci di cose di cui siamo abbastanza certi. **Non si compensa
il rischio usando un tasso di sconto più alto.**"*

È la divergenza più grande dal modello standard, che usa un WACC da CAPM con il beta —
una costruzione che Buffett rifiuta esplicitamente (per lui la volatilità non è rischio;
il rischio è la perdita permanente di capitale).

**Ma il tasso basso da solo è una trappola.** Scontare al 4% invece che al 9% può alzare
un fair value del 50-80%; su un business imprevedibile produce numeri privi di senso. Il
tasso basso funziona per Buffett perché è il *complemento di una selezione preventiva
severissima*, non un'ipotesi generosa presa da sola.

Il modello lo riproduce con **tre presidi che agiscono insieme**:

**a) Il filtro di prevedibilità.** Il criterio #1 pubblicato — *"demonstrated consistent
earning power; future projections are of no interest to us, nor are turnaround
situations"* — diventa un test con quattro requisiti:

| Requisito | Soglia |
|---|---|
| Storico disponibile | ≥ 4 esercizi |
| Utile netto positivo | in **ogni** esercizio |
| Owner Earnings positivi | in **ogni** esercizio |
| Coefficiente di variazione degli Owner Earnings | ≤ 0.50 |

Se l'azienda non li supera tutti, **il tasso del Treasury non viene applicato**: il
modello resta al WACC e lo dichiara. È il comportamento fedele — Buffett non alza il tasso
per compensare il rischio, semplicemente non compra.

**b) Le ipotesi vanno come blocco.** Insieme al tasso cambiano: crescita terminale
**zero** (nessuna crescita perpetua regalata), tetto alla crescita esplicita al **10%**,
normalizzazione su **5 anni** ("media annua", non l'ultimo esercizio).

**c) Il margine di sicurezza sale al 50%.** Il tasso basso alza il fair value; il margine
doppio riporta il prezzo d'acquisto a livelli sensati. Nei test su bilanci sintetici il
fair value passa da ~113 a ~246, ma il prezzo d'acquisto obiettivo resta sotto 123 — cioè
circa 20 volte gli Owner Earnings, che è un multiplo d'ingresso plausibile per un
compounder di qualità.

### 5. Owner Earnings yield contro il titolo di Stato

*"Usiamo il tasso risk free semplicemente per equiparare una cosa all'altra... possiamo
sempre comprare titoli di Stato."*

Il modello stampa quindi il rendimento degli Owner Earnings sul prezzo, il rendimento del
titolo di Stato e la differenza. È il confronto che Buffett fa davvero, ed è più diretto
di qualunque fair value: se un'azienda rende meno di un titolo di Stato, tutto il premio
che stai pagando è scommessa sulla crescita futura.

### 6. La scorecard, e cosa dichiara di non poter misurare

La checklist riproduce i criteri di acquisizione pubblicati, con soglie esplicite:

| Criterio | Misura | Soglia |
|---|---|---|
| Capacità di reddito dimostrata | anni con utile positivo | 100% |
| Owner Earnings in crescita | anni di crescita | ≥ 60% |
| Buon rendimento sul capitale proprio | ROE medio | ≥ 15% |
| Economics del business | rendimento su capitale tangibile | ≥ 25% |
| Poco o nessun debito | Debt/Equity | ≤ 0.50 |
| Debito ripagabile | anni di Owner Earnings | ≤ 3 |
| Margine di sicurezza | sconto sul fair value | ≥ 50% |

Tre criteri di Buffett **non sono verificabili da un bilancio** e compaiono marcati come
giudizio manuale invece di essere omessi: business comprensibile (*"se c'è molta
tecnologia, non lo capiamo"*), management già al suo posto (*"non possiamo fornirlo noi"*),
cerchio di competenza. Un modello che li tace lascia credere che la checklist sia completa.

### Dove il modello resta inevitabilmente diverso

**Buffett non assegna punteggi.** I suoi criteri sono filtri passa/non passa su poche cose;
questo è un sistema di ranking. Sono strumenti per scopi diversi: lui sceglie cinque
aziende in un decennio, un modello quantitativo ordina un universo.

**E c'è una tensione irriducibile**: lui dice *"le proiezioni future non ci interessano
affatto"*, ma un DCF **è** una proiezione. Lui la risolve comprando solo business il cui
futuro è altamente prevedibile — che è esattamente ciò che il filtro di prevedibilità
cerca di imitare. Ma resta un'imitazione.

Per questo il **reverse DCF** è lo strumento più fedele di tutto il modello: non proietta
niente, misura solo cosa il mercato sta già scontando. È l'unico che rispetta davvero il
*"no projections"*.

---

<a name="2-modulo-1--quality-score"></a>
## 2. Modulo 1 — Quality Score

**File**: `backend/models/quality_score.py`
**Domanda**: questa azienda ha un vantaggio competitivo duraturo, o sta solo
attraversando un buon periodo?

### 2.1 Da dove vengono i dati

`fetch_financials(ticker)` scarica tre prospetti annuali da yfinance:

- **conto economico** (income statement): ricavi, margine lordo, reddito operativo,
  oneri finanziari, imposte, utile netto;
- **stato patrimoniale** (balance sheet): attivo, passivo, patrimonio netto, debito,
  cassa, attivo/passivo corrente, numero di azioni;
- **rendiconto finanziario** (cash flow): ammortamenti, investimenti (CapEx),
  variazione del capitale circolante.

Yahoo usa etichette diverse a seconda del titolo e del settore, quindi ogni voce viene
cercata su una **lista di alias** (per esempio l'utile netto può chiamarsi
`Net Income`, `Net Income Common Stockholders` o
`Net Income From Continuing Operation Net Minority Interest`). La prima etichetta
trovata vince.

> **Nota sullo storico**: il modulo chiede 10 esercizi, ma yfinance ne espone
> tipicamente **4-5**. Il numero effettivamente usato è sempre riportato in
> `data_quality.years_available`. Su 4 esercizi le metriche di consistenza sono
> indicative, non conclusive.

### 2.2 Le metriche di redditività

#### ROIC — Return On Invested Capital

```
NOPAT              = EBIT × (1 − aliquota fiscale effettiva)
Capitale Investito = Debito Totale + Patrimonio Netto − Cassa
ROIC               = NOPAT / Capitale Investito
```

**Cosa misura**: quanto rende ogni euro di capitale *effettivamente impiegato*
nell'attività, al netto delle imposte e prima della struttura finanziaria.

**Perché è la metrica principale**: è l'indicatore più vicino all'esistenza di un
*moat*. Un'azienda che produce ROIC del 25% per dieci anni di fila sta facendo
qualcosa che i concorrenti non riescono a replicare — altrimenti il capitale
affluirebbe nel settore fino a schiacciare i rendimenti verso il costo del capitale.

**Come si legge**:

| ROIC | Lettura |
|---|---|
| > 20% | vantaggio competitivo probabile |
| 12–20% | azienda buona, moat da verificare |
| 8–12% | crea appena valore sopra il costo del capitale |
| < 8% | probabilmente distrugge valore |

Il confronto che conta davvero è **ROIC contro WACC** (il costo del capitale, calcolato
dal modulo 2): se il ROIC è sotto il WACC, l'azienda brucia valore mentre cresce.
Crescere in quella condizione peggiora le cose.

**Attenzione**: la cassa viene sottratta dal capitale investito perché non serve
all'attività operativa. Su aziende con enormi riserve liquide questo alza il ROIC, ed è
corretto: quella cassa non sta producendo il reddito operativo. Se però il patrimonio
netto è stato eroso da riacquisti di azioni, il capitale investito può diventare molto
piccolo e il ROIC gonfiarsi artificialmente — il modulo segnala il caso quando il
capitale investito risulta non positivo, e in quel caso non calcola il ROIC.

#### ROE e ROA

```
ROE = Utile Netto / Patrimonio Netto
ROA = Utile Netto / Totale Attivo
```

**ROE** misura il rendimento per l'azionista, ma è facilmente manipolabile con la leva:
indebitarsi riduce il patrimonio netto e alza meccanicamente il ROE senza che l'azienda
sia migliorata. Per questo pesa meno del ROIC nel punteggio (15% contro 35%).

**ROA** è il ROE senza l'effetto leva: rendimento su tutto ciò che l'azienda controlla.
La distanza fra ROE e ROA racconta quanto debito c'è nella struttura.

| Metrica | Buono | Eccellente |
|---|---|---|
| ROE | > 15% | > 25% |
| ROA | > 8% | > 12% |

#### Margine operativo e margine netto

```
Margine operativo = Reddito Operativo / Ricavi
Margine netto     = Utile Netto / Ricavi
```

**Cosa misurano**: potere di prezzo ed efficienza. Un margine operativo stabile e alto
significa che l'azienda può alzare i prezzi senza perdere clienti — è il moat visto dal
lato del conto economico.

Il numero assoluto va letto **per settore**: il 4% di un distributore alimentare è
ottimo, il 4% di un software è un disastro. Quello che il modello misura davvero è la
**traiettoria e la stabilità** del margine, che sono confrontabili fra settori diversi.

#### Owner Earnings

```
Owner Earnings = Utile Netto
               + Ammortamenti (D&A)
               − CapEx di mantenimento
               ± Variazione del capitale circolante
```

**Cosa misura**: la cassa che il proprietario potrebbe estrarre ogni anno senza
indebolire l'azienda. È la definizione di Buffett nella lettera agli azionisti del 1986,
ed è più onesta dell'utile netto (che contiene poste non monetarie) e del free cash flow
contabile (che sottrae *tutto* il CapEx, incluso quello di espansione).

**L'approssimazione più importante di tutto il modello**: il CapEx di mantenimento —
quello necessario solo a mantenere la capacità produttiva attuale — **non è separabile
in bilancio**. Nessuna azienda lo pubblica. Il modello usa il **CapEx totale** come
proxy, il che significa che sottrae anche gli investimenti di crescita. Il risultato è
**sistematicamente prudenziale**: gli Owner Earnings veri di un'azienda in forte
espansione sono più alti di quelli calcolati qui. La cosa è segnalata esplicitamente in
`data_quality.estimated` a ogni esecuzione.

**Sul segno della variazione di circolante**: yfinance riporta la voce
`Change In Working Capital` già come *impatto sulla cassa* (positiva = circolante
liberato = cassa in entrata), quindi si somma. Se la voce manca, viene stimata dalla
differenza anno su anno di (attivo corrente − passivo corrente), col segno invertito
perché un aumento del circolante assorbe cassa.

### 2.3 Le metriche di solidità patrimoniale

```
Debt / Equity     = Debito Totale / Patrimonio Netto
Debt / EBITDA     = Debito Totale / EBITDA
Interest Coverage = EBIT / Oneri Finanziari
Current Ratio     = Attivo Corrente / Passivo Corrente
```

| Metrica | Sicuro | Attenzione | Pericolo |
|---|---|---|---|
| Debt / Equity | < 0.5 | 0.5 – 1.5 | > 2 |
| Debt / EBITDA | < 1.5 | 1.5 – 3 | > 4 |
| Interest Coverage | > 10x | 4 – 10x | < 3x |
| Current Ratio | > 1.5 | 1 – 1.5 | < 1 |

**Interest Coverage è la più informativa delle quattro**: dice quante volte il reddito
operativo copre gli interessi. Sotto 3x un anno storto basta a mettere l'azienda in
difficoltà con le banche. Sopra 15x il debito è di fatto irrilevante.

**Current Ratio va letto con criterio**: sotto 1 di solito è un allarme, ma non per le
aziende con potere contrattuale enorme sui fornitori (grande distribuzione, alcune big
tech), che incassano dai clienti prima di pagare i fornitori e quindi operano
strutturalmente con circolante negativo. Non è debolezza, è potere di mercato.

Quando l'azienda non ha debito e non ha oneri finanziari, l'Interest Coverage viene
posto convenzionalmente al massimo (999) e la cosa viene annotata.

### 2.4 La consistenza

`calculate_consistency(serie)` prende una serie annuale e restituisce:

```
Deviazione standard         σ  (campionaria)
Coefficiente di variazione  CV = σ / |media|
Anni in crescita %          = (numero di anni con valore > anno precedente) / (n − 1) × 100
Anni positivi %             = (numero di valori > 0) / n × 100
```

**Perché il coefficiente di variazione e non la deviazione standard**: σ dipende
dall'unità di misura, quindi non è confrontabile fra ROIC (percentuale) e ricavi
(miliardi). Il CV è adimensionale: divide la dispersione per il livello medio, e quindi
si può usare per confrontare la stabilità di grandezze diverse o di aziende diverse.

**Come si legge il CV**:

| CV del ROIC | Lettura |
|---|---|
| < 0.15 | rendimenti molto stabili, business prevedibile |
| 0.15 – 0.35 | normale oscillazione |
| > 0.50 | ciclico o in transizione: la media storica dice poco sul futuro |

**Perché la consistenza pesa il 30%**: la prevedibilità è ciò che rende possibile una
valutazione. Un'azienda con ROIC del 40% un anno e del 5% l'anno dopo può avere una
media eccellente e restare invalutabile — non sai su quale numero costruire il DCF.
Buffett lo dice in altro modo: preferisce un rendimento del 12% prevedibile a uno del
20% imprevedibile.

### 2.5 Come si costruisce il punteggio

Ogni componente viene portata su scala 0-100 con una **normalizzazione lineare**
delimitata da due soglie, e poi troncata agli estremi:

```
punteggio = 100 × (valore − soglia_zero) / (soglia_cento − soglia_zero)   [limitato a 0…100]
```

Quando `soglia_zero > soglia_cento` la scala si inverte (serve per le metriche in cui
"meno è meglio", come il debito).

**Categoria Profittabilità (peso 40%)**

| Componente | Peso interno | 0 punti | 100 punti |
|---|---|---|---|
| ROIC medio | 35% | 4% | 25% |
| ROE medio | 15% | 5% | 25% |
| ROA medio | 15% | 1% | 12% |
| Margine operativo medio | 15% | 3% | 25% |
| Margine netto medio | 10% | 2% | 20% |
| Owner Earnings / Ricavi | 10% | 2% | 18% |

**Categoria Consistenza (peso 30%)**

| Componente | Peso interno | 0 punti | 100 punti |
|---|---|---|---|
| CV del ROIC | 30% | 0.60 | 0.05 |
| CV del margine netto | 20% | 0.60 | 0.05 |
| Anni di crescita dei ricavi | 20% | 40% | 100% |
| Anni di crescita degli Owner Earnings | 15% | 40% | 100% |
| Anni con utile positivo | 15% | 60% | 100% |

**Categoria Solidità (peso 30%)**

| Componente | Peso interno | 0 punti | 100 punti |
|---|---|---|---|
| Debt / Equity medio | 30% | 2.5 | 0.1 |
| Debt / EBITDA medio | 30% | 4.0 | 0.5 |
| Interest Coverage medio | 25% | 2x | 15x |
| Current Ratio medio | 15% | 0.8 | 2.0 |

Le soglie sono **convenzioni ragionevoli, non verità**. Sono state scelte perché il
punto di 100 corrisponde grosso modo al livello di un'azienda eccellente e lo zero al
livello di un'azienda in difficoltà. Vanno guardate e, se non convincono, cambiate: sono
tutte in cima al file, in un unico posto, e ogni report le stampa accanto al valore.

Se una componente non è calcolabile viene esclusa e il suo peso ridistribuito sulle
altre della stessa categoria. Se un'intera categoria manca, il suo peso si redistribuisce
sulle categorie rimaste, e la cosa viene annotata.

### 2.6 Come si legge il risultato

| Punteggio | Giudizio | Interpretazione |
|---|---|---|
| ≥ 80 | Eccellente | qualità da compounder, i tre pilastri reggono insieme |
| 65 – 79 | Buona | solida, con qualche punto debole identificabile |
| 50 – 64 | Discreta | nella media, servono ragioni specifiche per comprarla |
| 35 – 49 | Debole | almeno una categoria è compromessa |
| < 35 | Scarsa | fuori dal perimetro di una strategia quality |

**Il punteggio totale è la parte meno interessante dell'output.** Quello che conta è
*da dove viene*: un 70 fatto di profittabilità 100 e solidità 30 è un'azienda
eccezionale carica di debito — un profilo completamente diverso da un 70 uniforme su
tutte e tre le categorie. Il radar del modulo 4 serve esattamente a rendere visibile
questa differenza, che il numero singolo nasconde.

### 2.7 Personalizzazione

```python
calculate_quality_score("AAPL", weights={
    "profitability": 0.50,   # più peso alla redditività
    "consistency":   0.30,
    "balance_sheet": 0.20,
})
```

I pesi vengono normalizzati automaticamente a somma 1, quindi si possono passare anche
come `{"profitability": 5, "consistency": 3, "balance_sheet": 2}`.

---

<a name="3-modulo-2--valuation"></a>
## 3. Modulo 2 — Valuation

**File**: `backend/models/valuation.py`
**Domanda**: quanto vale davvero questa azienda, e quanto margine di errore mi lascia
il prezzo di oggi?

### 3.1 Il costo del capitale (WACC)

Prima di scontare qualsiasi flusso serve un tasso. Il modello lo costruisce invece di
inventarlo:

```
Ke (costo dell'equity) = risk free + β × premio al rischio azionario
Kd (costo del debito)  = Oneri Finanziari / Debito Totale
WACC = (E/V) × Ke + (D/V) × Kd × (1 − aliquota)

dove  E = capitalizzazione di mercato,  D = debito totale,  V = E + D
```

**Valori di default**: risk free 4.0% (decennale USA), premio al rischio 5.0% (stime
Damodaran storicamente 4.5–5.5%), beta 1.00 se non recuperabile.

**Protezioni applicate**, tutte annotate quando scattano:

- il beta viene limitato a [0.3, 2.5] — fuori da lì è quasi sempre un artefatto di
  calcolo su titoli poco liquidi;
- il costo del debito viene accettato solo se cade fra 0.1% e 25%, altrimenti sostituito
  con `risk free + 150 punti base`;
- il WACC finale è limitato alla fascia **7%–15%**: sotto il 7% nessun titolo azionario
  dovrebbe scontarsi, sopra il 15% il DCF perde significato perché il valore terminale
  collassa.

**Come si legge**: il WACC è il rendimento minimo che l'azienda deve produrre per non
distruggere valore. Confrontalo con il ROIC del modulo 1. `ROIC > WACC` significa che
ogni euro reinvestito crea valore; `ROIC < WACC` significa che la crescita è un problema,
non una virtù.

### 3.2 Normalizzazione degli utili

Prima di proiettare dieci anni di flussi, il punto di partenza va ripulito dal ciclo:

| Metodo | Cosa fa | Quando usarlo |
|---|---|---|
| `last` | ultimo esercizio | business molto stabile |
| `mean3` / `mean5` | media su 3 o 5 anni | attenua il rumore |
| `median3` (default) | mediana su 3 anni | robusta agli anni anomali |

La mediana è il default perché un singolo anno eccezionale (o disastroso) non la sposta,
mentre sposterebbe sia l'ultimo valore sia la media.

### 3.3 Il DCF a due stadi con fade

Il cuore del modulo.

**Sentiero di crescita** — con i default (10 anni di proiezione, 5 di fade):

```
anni 1-5   crescita esplicita  g₁
anni 6-10  discesa lineare da g₁ fino a g_terminale
```

Il *fade* serve a evitare il difetto più comune dei DCF fatti male: proiettare dieci
anni al 15% e poi passare di colpo al 2.5% perpetuo. Quel salto non esiste nella realtà
— i vantaggi competitivi si erodono gradualmente — e produce valutazioni gonfiate.

**Quale g₁ viene usato**: il **minimo** fra i CAGR storici disponibili (Owner Earnings,
ricavi, utile netto), poi limitato all'intervallo [−5%, +15%]. Prendere il minimo è una
scelta deliberatamente conservativa: proiettare la migliore delle crescite passate è
l'errore più costoso che si possa fare in un DCF. Il tetto al 15% impedisce di
estrapolare all'infinito una crescita eccezionale.

**Attualizzazione** (con convenzione *mid-year*, standard nei modelli di M&A e private
equity, perché i flussi arrivano distribuiti nell'anno e non tutti il 31 dicembre):

```
FCF_t = FCF_(t−1) × (1 + g_t)
PV_t  = FCF_t / (1 + WACC)^(t − 0.5)
```

**Valore terminale** (formula di Gordon):

```
TV     = FCF_N × (1 + g_terminale) / (WACC − g_terminale)
PV(TV) = TV / (1 + WACC)^N
```

**Dal valore d'impresa al valore per azione**:

```
Enterprise Value = Σ PV_t + PV(TV)
Equity Value     = Enterprise Value − Debito Netto        (Debito Netto = Debito − Cassa)
Valore per azione = Equity Value / numero di azioni
```

**Il controllo che va sempre fatto**: il modello riporta `terminal_weight`, cioè quanta
parte del valore viene dal valore terminale. **Sopra il 75% scatta un avviso.** Se
l'80% del valore dipende da cosa succede dopo il decimo anno, il DCF non sta valutando
l'azienda: sta valutando un'ipotesi di perpetuità. In quel caso l'EPV e i multipli
storici contano più del DCF.

**Guardie**: se il WACC non è superiore alla crescita terminale il valore terminale
diverge (denominatore ≤ 0) e il metodo restituisce un errore invece di un numero
enorme. Lo stesso se il flusso di partenza è negativo.

### 3.4 Reverse DCF — la domanda più utile

Invece di stimare la crescita futura e ricavare il valore, il reverse DCF fa il
percorso inverso: **prende il prezzo di mercato e ricava la crescita che lo giustifica**.

Tecnicamente: si cerca per bisezione il g₁ che rende il valore per azione uguale al
prezzo corrente (il valore è monotono crescente in g₁, quindi la bisezione converge
sempre; l'intervallo esplorato è [−30%, +50%]).

**Perché è il numero più onesto del modulo**: elimina la parte in cui ti illudi di saper
prevedere il futuro. Non devi stimare niente — leggi solo cosa il mercato sta già
scontando, e ti chiedi se sia ragionevole.

**Come si legge**:

| Confronto | Lettura |
|---|---|
| crescita implicita **<** crescita storica | il mercato è più pessimista del passato recente — possibile occasione |
| crescita implicita **≈** crescita storica | il prezzo incorpora la continuazione del trend |
| crescita implicita **>** crescita storica | stai pagando per un'accelerazione che deve ancora arrivare |

Esempio concreto: se il reverse DCF dice 15% annuo per dieci anni e l'azienda è cresciuta
storicamente all'8%, la domanda non è "quanto vale" ma "cosa deve succedere perché quel
15% si realizzi, e quanto è probabile?".

### 3.5 EPV — Earnings Power Value (Greenwald)

```
NOPAT normalizzato = EBIT normalizzato × (1 − aliquota)
EPV (enterprise)   = NOPAT normalizzato / WACC
EPV (equity)       = EPV enterprise − Debito Netto
```

**Cosa misura**: quanto vale l'azienda se la sua capacità di reddito attuale continuasse
per sempre **senza crescere di un centesimo**.

**A cosa serve**: è il pavimento. Il DCF dipende da ipotesi di crescita; l'EPV no. Se il
prezzo di mercato è sotto l'EPV, il mercato sta valutando la crescita futura a **zero o
meno di zero** — una situazione che merita di essere indagata, perché o il mercato sa
qualcosa che tu non sai, o è un'occasione.

Nel fair value di sintesi pesa il 15%: serve come ancora prudenziale contro un DCF
troppo ottimista.

### 3.6 Graham Number e NCAV — e perché sono esclusi dalla sintesi

```
Graham Number = √(22.5 × EPS × Valore contabile per azione)
NCAV per azione = (Attivo Corrente − Totale Passività) / azioni
```

Il 22.5 è il prodotto dei due tetti di Graham: P/E massimo 15 e P/B massimo 1.5.
L'NCAV è il valore di liquidazione approssimato: quanto resterebbe vendendo l'attivo
corrente e pagando *tutti* i debiti.

**Perché il modello li mostra ma li tiene fuori dal fair value**: entrambi nascono negli
anni '30-'50 per aziende industriali *asset-heavy* comprate a sconto sul patrimonio. Su
un'azienda moderna asset-light — dove il valore sta in marchi, software e relazioni, che
in bilancio non compaiono — o su un'azienda che ha eroso il patrimonio netto con
riacquisti di azioni, producono numeri sistematicamente e prevedibilmente troppo bassi.

Includerli nella media significherebbe lasciare che un numero privo di significato per
quel tipo di azienda decida il fair value. Nei test su bilanci sintetici la differenza
era **60 contro 113 dollari per azione**: non un dettaglio.

Restano nel report come **pavimento di liquidazione**, che è il ruolo per cui hanno
ancora senso.

### 3.7 Multipli storici

Per ogni esercizio il modello prende il prezzo alla data di chiusura del bilancio e
calcola P/E e P/Owner Earnings. Poi moltiplica la **mediana storica** per la metrica
corrente.

**Perché contro la propria storia e non contro il settore**: scegliere i "comparabili"
è il punto in cui si infila più bias in una valutazione — cambiando il gruppo di
riferimento si ottiene il risultato che si preferisce. La storia del titolo stesso non
si può scegliere: o c'è o non c'è.

**Limite da tenere presente**: se l'azienda è stata sistematicamente sopravvalutata negli
ultimi cinque anni, la sua mediana storica è alta e questo metodo la dichiarerà
"equamente prezzata" anche a livelli assurdi. Per questo pesa il 25% e non di più.

### 3.8 Il fair value di sintesi

```
Fair value = Σ (valore_metodo × peso) / Σ pesi          (solo sui metodi calcolabili)
```

| Metodo | Peso | Ruolo |
|---|---|---|
| DCF Owner Earnings | 60% | la stima principale |
| Multipli storici | 25% | controllo di mercato |
| EPV | 15% | ancora prudenziale |
| Graham Number, NCAV | — | riferimento, esclusi |

Se un metodo non è calcolabile, il suo peso si ridistribuisce proporzionalmente sugli
altri e la cosa viene annotata.

**Margine di sicurezza**:

```
Margine di sicurezza = (Fair value − Prezzo) / Fair value
Prezzo d'acquisto    = Fair value × (1 − margine obiettivo)      [default 30%]
```

| Margine | Giudizio |
|---|---|
| ≥ +30% | Sconto significativo |
| +10% … +30% | Moderatamente sottovalutata |
| −10% … +10% | In linea con il valore stimato |
| −30% … −10% | Moderatamente sopravvalutata |
| < −30% | Sopravvalutata |

**Perché il margine di sicurezza e non semplicemente "è sotto il fair value"**: il fair
value è una stima con un margine d'errore ampio. Il 30% non è un obiettivo di profitto,
è lo **spazio per sbagliare** — copre l'errore sulla crescita, sull'aliquota, sul WACC
e sulla qualità dei dati di partenza. È la traduzione operativa della frase di Graham:
non serve conoscere il peso esatto di una persona per sapere che è sovrappeso.

### 3.9 Scenari e sensitività

**Tre scenari** costruiti muovendo insieme le due leve principali:

| Scenario | Crescita | WACC |
|---|---|---|
| Bear | g₁ − 4 punti | WACC + 1.5 punti |
| Base | g₁ | WACC |
| Bull | g₁ + 4 punti | WACC − 1.5 punti |

**Griglia di sensitività**: valore per azione per 5 livelli di WACC × 5 livelli di
crescita terminale (25 combinazioni), che diventa la superficie 3D del modulo 4.

**Come si legge la griglia — è la parte più importante del report**: se muovendo il WACC
di un punto e la crescita terminale di mezzo punto il valore passa da 120 a 190, la
valutazione non è "190", è "un numero fra 120 e 190 con dentro un'ipotesi arbitraria".
La griglia serve a rendere visibile questa fragilità invece di nasconderla dietro un
numero con due decimali.

---

<a name="4-modulo-3--backtest"></a>
## 4. Modulo 3 — Backtest

**File**: `backend/models/backtest.py`
**Domanda**: se avessi applicato questa regola in passato, cosa sarebbe successo?

### 4.1 La strategia

A ogni data di ribilanciamento:

1. per ogni titolo dell'universo si calcolano due segnali:
   - **qualità** = Quality Score (modulo 1) sui bilanci disponibili a quella data;
   - **valore** = earnings yield `EBIT / Enterprise Value`;
2. entrambi vengono convertiti in **z-score** rispetto agli altri titoli della stessa data;
3. si sommano con i pesi scelti (default 50/50) ottenendo il punteggio composito;
4. si comprano i primi `top_n` titoli, a peso uguale;
5. si tiene fino al ribilanciamento successivo (default: annuale).

**Perché lo z-score e non i valori grezzi**: normalizzare *dentro* la data rende la
selezione indipendente dal livello assoluto dei punteggi, che cambia nel tempo. In un
anno in cui tutte le aziende hanno margini compressi, lo z-score continua a distinguere
le migliori dalle peggiori; il valore grezzo no.

```
z = (valore − media della sezione) / deviazione standard della sezione
```

**Perché EBIT/EV come misura di prezzo** (e non il P/E): l'Enterprise Value include il
debito, quindi confronta correttamente aziende con strutture finanziarie diverse. È il
lato "value" della magic formula di Greenblatt, di cui questa strategia è
sostanzialmente una versione con un filtro di qualità più ricco.

### 4.2 Point-in-time: la difesa contro il look-ahead bias

**Il problema**: il modo più facile di produrre un backtest bellissimo e completamente
falso è usare, per decidere nel 2020, dati di bilancio pubblicati nel 2021.

**La soluzione**: alla data D si usano solo esercizi che soddisfano

```
data di chiusura dell'esercizio + reporting_lag_days ≤ D          (default 90 giorni)
```

I tre prospetti vengono **fisicamente troncati** (`slice_financials`) prima di essere
passati al calcolo, così il resto del modello non può nemmeno accidentalmente vedere il
futuro.

**Conseguenza pratica**: al ribilanciamento del 2 gennaio 2024, l'ultimo bilancio
utilizzabile è quello chiuso al 31 dicembre **2022** (il 2023 non ha ancora scontato i
90 giorni). È conservativo, ed è corretto.

**Come è verificato**: il test `test_niente_look_ahead` inserisce nell'universo un
emittente costruito apposta — pessimo fino al 2021, eccezionale dal 2022 — e verifica
che il modello non lo selezioni prima che quei bilanci fossero disponibili. È l'unico
modo di dimostrare che il filtro funziona davvero, invece di sostenerlo a parole.

### 4.3 Costruzione del portafoglio e costi

**Turnover**: la somma dei |Δpeso| fra il portafoglio obiettivo e quello **derivato dal
periodo precedente** — cioè i pesi come sono diventati dopo la deriva dei prezzi, non i
pesi obiettivo di allora. Con questa convenzione una rotazione completa vale 200%.

Il dettaglio conta: se un titolo è salito e il suo peso è passato dal 33% al 40%,
ribilanciare costa solo la differenza residua, non l'intera posizione.

**Costi**: `costo = turnover × transaction_cost_bps / 10000`, applicato al capitale a
ogni ribilanciamento. Default 10 punti base.

Fra un ribilanciamento e l'altro i pesi **derivano liberamente** con i prezzi
(comportamento buy-and-hold), che è ciò che accade davvero in un portafoglio reale.

### 4.4 Le metriche di performance

Tutte calcolate su rendimenti giornalieri, annualizzate con 252 giorni di borsa.

| Metrica | Formula | Cosa dice |
|---|---|---|
| **CAGR** | `(V_f/V_i)^(1/anni) − 1` | crescita annua composta |
| **Volatilità** | `σ(rendimenti) × √252` | ampiezza delle oscillazioni |
| **Sharpe** | `media(r − rf) / σ(r − rf) × √252` | rendimento per unità di rischio totale |
| **Sortino** | come Sharpe ma con σ dei soli rendimenti negativi | rendimento per unità di rischio *al ribasso* |
| **Max Drawdown** | `min(V_t / max(V_≤t) − 1)` | la peggiore perdita da un massimo |
| **Calmar** | `CAGR / |Max Drawdown|` | rendimento per unità di dolore |
| **Beta** | `Cov(r_p, r_b) / Var(r_b)` | sensibilità al mercato |
| **Alpha di Jensen** | `CAGR_p − [rf + β × (CAGR_b − rf)]` | extra-rendimento non spiegato dal mercato |
| **Tracking Error** | `σ(r_p − r_b) × √252` | quanto ci si discosta dal benchmark |
| **Information Ratio** | `media(r_p − r_b) × 252 / TE` | qualità dello scostamento |

**Come leggerle, in ordine di importanza pratica**:

1. **Max Drawdown prima di tutto.** È l'unica metrica che misura ciò che ti fa vendere
   nel momento sbagliato. Una strategia con Sharpe 1.2 e drawdown del 60% è
   inapplicabile da un essere umano: nessuno la tiene fino in fondo.
2. **Sortino più di Sharpe.** Lo Sharpe penalizza anche la volatilità *al rialzo*, che
   non è un rischio. Il Sortino guarda solo alle discese.
3. **Alpha con molta cautela.** Su pochi anni e pochi titoli l'alpha è quasi tutto
   rumore. Serve un campione ampio prima di attribuirlo all'abilità.
4. **Calmar** è la sintesi più onesta per una strategia di lungo periodo: quanto
   guadagni per ogni punto di sofferenza massima sopportata.

Valori di riferimento: Sharpe > 1 è buono, > 2 è sospetto su un backtest casalingo;
Calmar > 0.5 è rispettabile.

### 4.5 Lo sweep dei parametri e il rischio di overfitting

`sweep_parameters` esegue il backtest su una griglia di due parametri (per esempio
`top_n` × `quality_weight`) e produce la superficie 3D.

**A cosa serve davvero**: a rispondere alla domanda *"la strategia funziona in una
regione di parametri o solo in un punto?"*.

- una superficie con un **altopiano ampio** → il risultato è robusto: piccoli errori
  nella scelta dei parametri non cambiano la sostanza;
- una superficie con un **picco isolato** → quasi certamente overfitting: hai trovato la
  combinazione che funzionava su *quel* campione storico, e non funzionerà sul prossimo.

**Avvertenza stampata a ogni esecuzione**: scegliere la cella migliore *dopo* aver visto
i risultati e poi presentarne la performance come attesa è multiple testing. Quel numero
è contaminato. Guarda l'ampiezza dell'altopiano, non l'altezza del picco.

### 4.6 I bias che restano

Nessun backtest costruito su dati gratuiti è pulito, e fingere il contrario è peggio che
non farlo. Questi limiti vengono **stampati a ogni esecuzione**:

| Bias | Effetto | Perché resta |
|---|---|---|
| **Survivorship** | rendimenti gonfiati | l'universo contiene solo società esistenti oggi: fallite e delistate non ci sono |
| **Restatement** | dati leggermente diversi da quelli visti allora | yfinance espone i bilanci nella versione *rivista*, non in quella depositata |
| **Campione corto** | conclusioni non significative | 4-5 esercizi = pochi ribilanciamenti; la differenza col benchmark è rumore |
| **Costi semplificati** | rendimenti leggermente ottimistici | tasse, slippage variabile e impatto di mercato non modellati |
| **Nessuna gestione di eventi societari** | distorsioni puntuali | fusioni, spin-off e sospensioni non trattati |

Il survivorship bias è il più grave e **non è correggibile** con dati gratuiti: servirebbe
un database point-in-time delle composizioni degli indici (CRSP, Compustat), che è a
pagamento. Va tenuto a mente come una tara sistematica verso l'alto.

---

<a name="5-modulo-4--visualize"></a>
## 5. Modulo 4 — Visualize

**File**: `backend/models/visualize.py`
**Domanda**: come si guarda tutto questo insieme, senza farsi ingannare dal grafico?

### 5.1 I grafici e come si leggono

**Equity curve + drawdown** — capitale della strategia contro il benchmark, in scala
logaritmica (in scala lineare gli anni recenti sembrano sempre più mossi dei primi, che
è un artefatto). Il pannello sotto mostra il drawdown: guarda **prima quello**, poi la
curva.

**Football field** — ogni metodo di valutazione su una riga, il prezzo di mercato come
linea bianca verticale, la banda azzurra come intervallo dei metodi che compongono il
fair value. La riga del DCF è una barra e non un punto perché copre l'intervallo
bear-bull. *Il messaggio è la posizione relativa*: linea bianca a sinistra della banda =
sconto, a destra = premio.

**Radar della qualità** — otto assi, uno per componente del punteggio. Serve a vedere la
**forma** del profilo: una figura regolare è un'azienda equilibrata, una figura con una
punta lunga e un rientro profondo è un'azienda con un'eccellenza e un problema.

**Small multiples storici** — ROIC, margini, Owner Earnings in riquadri affiancati.
Riquadri separati e non curve sovrapposte, perché le unità sono diverse.

**Heatmap dell'universo** — punteggi di più titoli su una scala unica 0-100. Serve a
individuare i candidati in una lista lunga.

**Matrice qualità / sconto** — qualità sull'asse x, margine di sicurezza sull'asse y.
In alto a destra c'è l'unico quadrante che interessa a un value investor. Gli altri tre
servono a ricordare perché la maggior parte delle idee va scartata.

**Superficie di sensitività** — la griglia WACC × crescita terminale in 3D. Esiste anche
la versione a curve di livello (`kind="contour"`), più brutta e più precisa, perché in
3D la prospettiva nasconde celle.

### 5.2 Le tre scelte grafiche che non sono estetiche

**Niente doppio asse y.** Mettere due scale diverse sullo stesso riquadro fa apparire
correlazioni che nei dati non esistono: l'allineamento fra le due scale è arbitrario, e
spostandolo si ottiene la storia che si preferisce. Per questo il drawdown ha un pannello
proprio.

**Niente scale arcobaleno.** Per una magnitudine si usa una sola tinta dal chiaro allo
scuro. Un arcobaleno crea confini visibili (dove il verde diventa giallo) che nei dati
non corrispondono a niente, e il lettore li interpreta come soglie. È esattamente il
difetto delle superfici 3D che si vedono in giro.

**Palette verificata per i deficit di visione dei colori.** Le tinte non sono scelte a
occhio: la separazione fra coppie adiacenti è misurata, e ogni valore è leggibile anche
come numero (le celle della heatmap riportano la cifra, non solo il colore). Circa una
persona su dodici fra gli uomini ha una qualche forma di daltonismo.

### 5.3 Lingua

I grafici sono in **inglese** di default, che è la convenzione dei documenti finanziari.
Per l'italiano: `--lang it` da riga di comando, oppure `visualize.set_language("it")` da
codice. I report testuali restano in italiano.

---

<a name="6-come-si-usano-insieme"></a>
## 6. Come si usano insieme

Il flusso operativo per cui il modello è costruito:

**Passo 1 — Filtra per qualità.** Fai girare il Quality Score su una lista ampia. Tieni
solo chi supera la soglia che hai deciso (per esempio 65). Guarda il radar dei
sopravvissuti: la forma dice più del numero.

**Passo 2 — Valuta solo i sopravvissuti.** Il DCF su un'azienda mediocre è tempo perso:
il valore che ottieni dipende interamente da ipotesi che quell'azienda non ti dà motivo
di fare.

**Passo 3 — Leggi prima il reverse DCF, poi il fair value.** La crescita implicita nel
prezzo è un fatto; il fair value è una tua opinione. Parti dal fatto.

**Passo 4 — Guarda la griglia di sensitività prima di innamorarti del numero.** Se il
valore si muove del 50% dentro un intervallo ragionevole di ipotesi, non hai una
valutazione: hai un ordine di grandezza. Comportati di conseguenza.

**Passo 5 — Usa il backtest per sfidare la regola, non per confermarla.** Se il risultato
è ottimo, cerca il motivo per cui potrebbe essere falso (guarda l'altopiano dello sweep,
non il picco; conta i periodi; ricorda il survivorship bias).

**Passo 6 — Compra solo con margine di sicurezza.** Il fair value non è il prezzo
d'acquisto. Il prezzo d'acquisto è il fair value meno lo spazio per sbagliare.

```bash
# il flusso in un comando
python run_analysis.py AAPL MSFT KO PG JNJ V MA HD --backtest --json
```

---

<a name="7-glossario"></a>
## 7. Glossario

| Termine | Significato |
|---|---|
| **CAGR** | tasso di crescita annuo composto |
| **CapEx** | investimenti in immobilizzazioni |
| **CV** | coefficiente di variazione: deviazione standard / media |
| **D&A** | ammortamenti materiali e immateriali |
| **Drawdown** | perdita percentuale da un massimo precedente |
| **EBIT** | reddito operativo, prima di interessi e imposte |
| **EBITDA** | EBIT + ammortamenti |
| **Enterprise Value (EV)** | capitalizzazione + debito − cassa: il valore dell'intera impresa |
| **EPV** | Earnings Power Value: valore della capacità di reddito attuale, crescita zero |
| **Fade** | discesa graduale della crescita verso il tasso perpetuo |
| **Look-ahead bias** | usare in una decisione passata dati non ancora disponibili allora |
| **Margine di sicurezza** | sconto del prezzo rispetto al valore stimato |
| **Moat** | vantaggio competitivo difendibile nel tempo |
| **NIM** | Net Interest Margin: margine di interesse sugli attivi fruttiferi |
| **NOPAT** | reddito operativo al netto delle imposte |
| **Owner Earnings** | cassa estraibile senza indebolire l'azienda |
| **Combined ratio** | (sinistri + spese) / premi: sotto 100 l'assicurazione guadagna sulla tecnica |
| **CET1** | capitale di migliore qualità su attivi ponderati per il rischio (vigilanza) |
| **Float** | premi incassati prima del pagamento dei sinistri: leva a costo potenzialmente negativo |
| **Residual income** | valore = patrimonio + rendimenti sopra il costo del capitale |
| **ROTCE** | rendimento sul patrimonio tangibile, avviamento escluso |
| **Point-in-time** | usare solo informazioni disponibili alla data della decisione |
| **Survivorship bias** | distorsione da esclusione delle aziende scomparse |
| **Turnover** | quota di portafoglio scambiata a ogni ribilanciamento |
| **WACC** | costo medio ponderato del capitale |
| **Z-score** | scarto dalla media in unità di deviazione standard |

---

<a name="8-limiti-e-avvertenze"></a>
## 8. Limiti e avvertenze

**Sui dati.** yfinance è gratuito e fa quello che può: espone 4-5 esercizi invece di
dieci, riporta i bilanci nella versione rivista, cambia le etichette delle voci senza
preavviso e occasionalmente restituisce valori sbagliati. Ogni numero prodotto dal
modello eredita questa fragilità. La sezione `data_quality` di ogni output elenca cosa è
stato stimato: leggerla non è opzionale.

**Sui profili di settore.** Il riconoscimento automatico copre banche, assicurazioni e
aziende operative. Non copre casi di confine — asset manager, gestori di mercati,
società immobiliari, utility regolate — che finiscono nel profilo industriale e vanno
letti con cautela o forzati a mano. E per i finanziari mancano i ratios di vigilanza:
il giudizio sulla solidità patrimoniale poggia su proxy dichiarati, non sui numeri veri.

**Sul modello.** Tutte le soglie di punteggio, i pesi delle categorie e i default di
valutazione sono **convenzioni ragionevoli**, non risultati di ottimizzazione. Sono
esplicite e concentrate in cima a ogni file proprio perché siano discusse e modificate,
non accettate.

**Sul backtest.** Il survivorship bias non è correggibile con dati gratuiti e spinge
sistematicamente i risultati verso l'alto. Con 4-5 ribilanciamenti nessuna differenza
rispetto al benchmark è statisticamente significativa. Il backtest serve a scartare le
idee palesemente sbagliate, non a validare quelle giuste.

**Sull'uso.** Questo è uno strumento di analisi, non un consiglio di investimento. La sua
utilità sta nel rendere espliciti i passaggi di un ragionamento — quali ipotesi,
quali soglie, quali approssimazioni — così che si possa discuterli. Un modello che
produce un numero senza mostrare da dove viene è peggio di nessun modello, perché dà
sicurezza senza dare informazione.

---

*Documento della metodologia di value-quant-app.
Il codice, i test e le formule implementate sono la fonte autorevole: dove questo
documento e il codice divergessero, ha ragione il codice.*
