# Instalacja remoteG90ctl

*English version: [install.md](install.md).*

## 0. Jednym poleceniem

Na komputerze przy radiu: Debian 12/13, Ubuntu 22.04 lub nowszy, Raspberry Pi
OS (64-bit) albo DietPi, procesor x86_64 lub aarch64 (32-bitowy ARM — nie).

```sh
curl -fsSL https://raw.githubusercontent.com/SP2EMW/remoteG90ctl/main/install.sh | sudo bash
```

Instalator (`install.sh` w głównym katalogu repozytorium) przygotowuje system
i instaluje program jako usługi systemd — **najnowsze wydanie** (tag
`vX.Y.Z`), a nie gałąź rozwojową `main`. Wydanie z samym numerem przeszło
listę kontrolną sprzętu na prawdziwym G90; dopóki takiego nie ma, instalator
bierze najnowszego kandydata (`vX.Y.ZrcN`) i o tym mówi; na nowym komputerze **usług nie
uruchamia** — najpierw konfiguracja sprzętu z listą kontrolną (sekcja 5).
Na nowym komputerze uruchamia potem kreator **`sudo remoteg90ctl setup`**
(można go też uruchomić samemu, np. po wymianie kart; D56). Pierwsze pytanie
to język (English / Polski, Enter = język systemu); pominięcie punktu listy:
`POMIŃ` albo `SKIP`. Kreator:
1. znajduje port DE-19, kartę DE-19 (ta na tym samym hubie USB co port) i
   kartę I/Q, nadaje kartom stałe nazwy (reguły udev z wykrytych
   identyfikatorów), na komputerze z pulpitem wyłącza kartę I/Q w PipeWire i
   zapisuje konfigurację ze sprzętem — z **wyłączonym nadawaniem**, gdy port
   jest nowy;
2. **przy pierwszej instalacji na tym porcie** prowadzi przez listę kontrolną
   sprzętu (VOX, kabel DE-19 w gnieździe IO/User, SWR THR, wpięcie USB bez nadawania, start
   strażnika, tożsamość radia, CAT, test PTT 2 s i przerwanie — te dwa na
   sztucznym obciążeniu albo na antenie z mocą 1 W w SSB, w paśmie wspólnym
   dla wszystkich regionów ITU, np. 7,074 lub 14,074 MHz); punkt można pominąć,
   wpisując `SKIP` — wtedy strona pokazuje stałe ostrzeżenie; dziennik TX spoza
   katalogu stanu strażnika proponuje przenieść;
3. pyta o maksymalną moc dla przeglądarki, strojenie tunera i **włączenie
   nadawania**;
4. **dostęp do strony:** na tym komputerze `http://127.0.0.1:8078`; z telefonu
   i innych komputerów przez Tailscale (HTTPS) — na „y” loguje komputer do
   Tailscale (`tailscale up`, link do otwarcia), dopisuje jego nazwę z
   tailnetu do `server.allowed_hosts` i uruchamia `tailscale serve`; podaje
   adres `https://<nazwa>/`;
5. na końcu pyta o uruchomienie usług (start strażnika = mignięcie nadawania;
   radio w SSB).

Co robi instalator, opisuje sekcja 8.2. Uruchomiony ponownie aktualizuje program i
restartuje usługi. Przeniesienie stanu z innego komputera (kopia z
`deploy/backup.sh`, sekcja 8.4): `sudo ./install.sh --restore PLIK.tgz` z
katalogu z kodem.

Instalator działa dwustopniowo: pobrany przez `curl` wybiera wydanie, pobiera
je i przekazuje pracę skryptowi `install.sh` z tego wydania — instalator
zawsze pasuje do instalowanego programu. Wydanie, którego `install.sh` nie
umie przejąć pracy (starsze), jest odrzucane, zanim cokolwiek się zmieni.

Konkretne wydanie: `REMOTEG90CTL_RELEASE=v0.9.0rc4` (także kandydat nowszy od
ostatniego wydania bez sufiksu). Gałąź zamiast wydań:
`REMOTEG90CTL_BRANCH=main` — kod rozwojowy, niesprawdzony na radiu; instalacja
śledząca gałąź śledzi ją przy kolejnych uruchomieniach.

**Log instalacji:** `~/remoteg90ctl-install.log` (Twoje konto, prawa 0600) —
wszystko, co widać na ekranie, i pełne wyjście każdego długiego kroku; każde
uruchomienie dopisuje część z datą. Token z adresu repozytorium do niego nie
trafia. Przy problemie wyślij ten plik. Deinstalator go zostawia.

**Usunięcie wszystkiego** (np. przed kolejnym testem instalacji):
```sh
sudo ~/remoteG90ctl/deploy/backup.sh      # kopia, jeśli dane są potrzebne
sudo ~/remoteG90ctl/uninstall.sh
```
Usuwa program, konfigurację, dziennik TX i blokadę nadawania, konta,
dziennik łączności i nagrania, reguły udev i regułę WirePlumbera kreatora,
`tailscale serve`. Cofa to, co zmienił instalator — według jego zapisu w
`/var/lib/remoteg90ctl-installer/changes` i tylko to: zamaskowane usługi,
ustawienia zegara i swapu DietPi, zainstalowane przez niego pakiety (w
miejsce chrony wraca systemd-timesyncd), uv i Tailscale (wylogowany; komputer
usuń jeszcze ze strony administracyjnej tailnetu), sklonowany kod. Pyta raz
(wpisz `tak` albo `yes`); `--yes` bez pytania. Bez tego
zapisu (instalacja ręczna): `--all` cofa wszystko, co instalator mógł zmienić, a pakiety tylko
wypisuje.

Dalej ta instrukcja opisuje ręczne kroki, które instalator wykonuje sam, i
pierwsze uruchomienie. **Najpierw zawsze w trybie symulatora.** Do pracy z
radiem przechodzisz dopiero po wykonaniu listy kontrolnej sprzętu (sekcja 5).

**Skrót** dla instalacji z lokalnej kopii kodu (sekcje 1 i 2; bezpieczny przy
wielokrotnym uruchomieniu, nie nadpisuje istniejącej konfiguracji):

```sh
sudo deploy/install.sh --start      # instalacja/aktualizacja z lokalnego repozytorium
sudo deploy/services.sh --stop      # zatrzymanie usług i wyłączenie autostartu (--start, --status)
sudo deploy/uninstall.sh            # cofnięcie; zostawia konfigurację, dziennik TX i stan blokady
sudo deploy/uninstall.sh --purge    # cofnięcie razem z danymi (także kontami i dziennikiem łączności) i użytkownikiem remoteg90ctl
```

## 1. Użytkownik, katalogi, program

```sh
# Użytkownik usług; członkowie grupy remoteg90ctl mogą używać CLI.
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin remoteg90ctl
sudo usermod -aG remoteg90ctl "$USER"        # wyloguj się i zaloguj ponownie

# Program w /opt/remoteg90ctl, środowisko w /opt/remoteg90ctl/venv
sudo git clone <adres-repozytorium> /opt/remoteg90ctl
cd /opt/remoteg90ctl
sudo UV_PROJECT_ENVIRONMENT=/opt/remoteg90ctl/venv uv sync --no-dev --frozen

# Konfiguracja
sudo install -d -m 0750 -o root -g remoteg90ctl /etc/remoteg90ctl
sudo install -m 0640 -o root -g remoteg90ctl config/example.toml /etc/remoteg90ctl/config.toml
```

Katalogi `/run/remoteg90ctl` (gniazda) i `/var/lib/remoteg90ctl` (trwała
blokada TX, historia cyklu pracy i dziennik TX) tworzy systemd na podstawie
pliku usługi strażnika. Konfiguracja z dziennikiem TX w
`/var/log/remoteg90ctl/tx.jsonl` (dawne ustawienie domyślne) dalej działa, ale
`install.sh` odmawia, jeśli ten katalog jest w RAM (tmpfs) — przenieś wtedy
plik i ścieżkę, jak w sekcji 8.2.

**Zegar:** FT8/FT4 wymaga zegara z dokładnością lepszą niż ok. 0,5 s, a czas
dźwięku odbioru pochodzi z zegara serwera. Bez synchronizacji czasu zegar
laptopa uciekł o ok. 2,3 s (11.11.2026): wszystkie dekody miały DT ≈ 2,3 s i
słabe sygnały przestały się dekodować. Zainstaluj i włącz synchronizację
(`sudo apt install chrony` albo systemd-timesyncd) i sprawdź
`timedatectl` (`System clock synchronized: yes`) oraz `chronyc tracking`
(`System time` w milisekundach). **Zalecane chrony:** serwer odczytuje z niego
faktyczny błąd zegara i odmawia nadawania FT8/FT4 powyżej 0,2 s (D53); z
systemd-timesyncd zna tylko flagę „zsynchronizowany”. Na Futro S740 — sekcja
8.1 pkt 2.

CLI dla siebie:

```sh
sudo ln -s /opt/remoteg90ctl/venv/bin/remoteg90ctl /usr/local/bin/remoteg90ctl
```

## 2. Usługi

```sh
sudo cp deploy/systemd/remoteg90ctl-guard.service deploy/systemd/remoteg90ctl-server.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remoteg90ctl-guard remoteg90ctl-server
systemctl status remoteg90ctl-guard remoteg90ctl-server
```

- Strażnik ma `Type=notify` i `WatchdogSec=5`: jeśli jego pętla bezpieczeństwa
  się zawiesi, systemd go zabije, a jądro opuści linie DTR/RTS (HUPCL).
- Strażnik działa bez dostępu do sieci (`PrivateNetwork=yes`), a serwer ma
  dostęp tylko do pętli zwrotnej (`IPAddressDeny=any`, `IPAddressAllow=localhost`).
- Dzienniki: `journalctl -u remoteg90ctl-guard -u remoteg90ctl-server`.
  Dziennik nadawania: `/var/lib/remoteg90ctl/tx.jsonl` (`guard.tx_log_path`).
- **Kolejność: najpierw radio, potem usługi.** Według informacji z internetu
  ruch CI-V w trakcie rozruchu może zawiesić G90; u nas niezmierzone
  ([pomiary](g90-measurements.md), poz. 15). Serwer odpytuje radio cały czas (moc co 1 s,
  VFO co 0,5 s przy otwartej przeglądarce). Do czasu pomiaru:
  1. włącz radio (w trybie SSB, D23) i poczekaj, aż skończy rozruch;
  2. dopiero wtedy uruchom usługi: `sudo systemctl start remoteg90ctl-guard remoteg90ctl-server`.

  Jeśli radio trzeba wyłączyć i włączyć, najpierw zatrzymaj usługi
  (`sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard`).

## 3. Sprawdzenie w trybie symulatora

Przy `radio.backend = "simulator"` (wartość z przykładu) nic nie dotyka radia.

```sh
remoteg90ctl status
remoteg90ctl freq 14074000
remoteg90ctl freq
remoteg90ctl mode USB
remoteg90ctl tx-test --seconds 3       # nadawanie symulowane
sudo tail -n 5 /var/lib/remoteg90ctl/tx.jsonl
```

Sprawdź też ścieżki awaryjne: przerwij `tx-test --seconds 30` przez Ctrl+C,
potem w trakcie `tx-test` wykonaj `sudo systemctl restart remoteg90ctl-server`.
W obu przypadkach dziennik TX musi pokazać zakończenie nadawania.

## 4. Diagnostyka

Szczegółowy ślad włączasz w konfiguracji i restartem usług:

```toml
[logging]
level = "DEBUG"
```

```sh
sudo systemctl restart remoteg90ctl-guard remoteg90ctl-server
journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -f
```

Co zobaczysz na poziomie DEBUG:

| Wpis | Znaczenie |
|---|---|
| `civ_tx frame=...` | ramka zapisana przez strażnika do portu |
| `civ_rx frame=... kind=reply` | odpowiedź radia (przekazana serwerowi) |
| `civ_rx ... kind=echo` | echo własnej ramki na szynie CI-V |
| `civ_rx ... kind=broadcast` | komunikat radia bez pytania (transceive) |
| `civ_rx ... kind=other_device` | ramka od innego adresu — np. zły `radio_address`; także echo, w którym zginął bajt (widziane na G90 z DE-19 przy 11-bajtowych ramkach) |
| `civ_rx_garbage` (WARNING) | bajty niebędące ramką — często zła prędkość portu |
| `lines reason=... set_* read_*` | co strażnik ustawił na DTR/RTS i co odczytał z powrotem |
| `port_open_step` | kolejne kroki bezpiecznego otwierania portu |
| `request` / `response` | żądania serwera do strażnika i wynik |
| `guard_request` / `guard_response elapsed_ms=` | to samo po stronie serwera, z czasem |
| `cat_reply elapsed_ms=` / `cat_timeout` | czas odpowiedzi radia albo jej brak |

Jak czytać typowe objawy:
- **`cat_timeout` i żadnego `civ_rx`** — radio milczy: zły port, zła prędkość,
  CI-V wyłączone w menu, albo nie to gniazdo.
- **`civ_rx_garbage`** — bajty przychodzą, ale nie składają się w ramki:
  najczęściej zła prędkość.
- **`civ_rx ... kind=other_device`** — radio odpowiada z innego adresu niż
  `radio.radio_address`; adres z ramki (czwarty bajt) to adres radia.
- **Brak `kind=echo`** — interfejs nie odsyła echa; to normalne i nie
  przeszkadza.

CLI pokazuje surowe wiadomości z opcją `-v`:

```sh
remoteg90ctl -v freq
```

Podczas nadawania na poziomie DEBUG pojawia się kilka wpisów na sekundę
(odnowienia dzierżawy). Po diagnostyce wróć do `level = "INFO"`.

## 5. Lista kontrolna sprzętu — przed pierwszym podłączeniem

Kreator `sudo remoteg90ctl setup` (sekcja 0) prowadzi przez tę listę sam;
poniżej jest opis każdego punktu. Stan wiedzy o G90 i wyniki pomiarów:
[g90-measurements.md](g90-measurements.md) — „poz.” w tekście to numery
pozycji z tej tabeli. Wszystko poniżej rób **z radiem na sztucznym obciążeniu
albo z małą mocą**. Wyniki pierwszego przejścia listy (02.11.2026: G90 z
firmware 1.81, DE-19, laptop z Ubuntu) są przy poszczególnych punktach.

Zanim podłączysz kable do radia:

- **Laptop: zablokuj usypianie**, także przy zamknięciu klapy. Uśpiony
  komputer nie sprawdza dzierżawy, a stan linii DE-19 w uśpieniu nie jest znany:
  ```sh
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  ```
  (cofnięcie: to samo z `unmask`).
- **Zatrzymaj ModemManagera przed pierwszym wpięciem DE-19**
  (`sudo systemctl stop ModemManager`) — sprawdza nowe porty szeregowe, czyli je
  otwiera. Na stałe wyłączysz go dla DE-19 regułą udev z punktu 4.

- **VOX wyłączony** w menu radia (FUNC + VOL → VOX: OFF według Radioddity;
  poz. 12). Włączony VOX zamienia każdy dźwięk z karty audio w nadawanie
  z pominięciem strażnika, dzierżawy i limitu 180 s. Programowo nie da się
  tego sprawdzić.
- **Właściwe gniazda** (poz. 7b): DE-19 podłączony jak w jego instrukcji —
  kabel jack z gniazda IO/User w **panelu przednim** do gniazda CI-V w DE-19
  i wielopinowy kabel DIN z tylnego gniazda ACC do ACC w DE-19; osobny kabel
  CAT nie jest potrzebny. Kabel jack nie do tylnego COMM. Instrukcja G90 (s. 58) ostrzega, że włączenie radia
  z prostym kablem USB-serial w gnieździe COMM może przełączyć je w tryb
  aktualizacji firmware i unieruchomić do czasu ponownego wgrania firmware.

1. **Firmware** korpusu i panelu — zapisz wersje (poz. 10).
2. **Ochrona po stronie radia** (poz. 11, 11a). Według instrukcji G90 nie ma
   ograniczenia czasu nadawania (TOT) — przejrzyj menu i potwierdź. Odczytaj
   próg **SWR THR** (domyślnie 3:1) i zostaw go nisko: powyżej progu radio
   samo zmniejsza moc. **Wygaszanie ekranu:** wyłącz je w menu, jeśli się da —
   przy wygaszonym ekranie G90 odkłada strojenie tunera do włączenia ekranu
   i wtedy nadaje nośną bez wiedzy programu (poz. 13).
3. **Adapter USB w DE-19** — typ chipu i stały identyfikator (poz. 6, 8b):
   ```sh
   lsusb
   ls -l /dev/serial/by-id/
   ```
   **Wynik (02.11.2026):** DE-19 ma w środku hub USB (Genesys `05e3:0608`),
   układ **CH340** (`1a86:7523`, bez numeru seryjnego) i kartę dźwiękową C-Media
   (`0d8c:0012`, karta nr 1). Port: `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.

   Ścieżkę z `/dev/serial/by-id/` wpisz w `radio.port`. Jeśli urządzenie to
   `ttyUSB*` albo `ttyACM*`, plik usługi już na nie pozwala; inne — popraw
   `DeviceAllow=` w `remoteg90ctl-guard.service`.

   **Czy nazwa jest unikalna.** Nazwa w `by-id` jest stała tylko wtedy, gdy
   adapter ma własny numer seryjny. Układy CH340/CH341 zwykle go nie mają —
   wtedy każdy taki adapter dostaje tę samą nazwę i drugi podpięty do komputera
   może ją przejąć. Sprawdź:
   ```sh
   udevadm info /dev/serial/by-id/... | grep -E 'ID_SERIAL=|ID_SERIAL_SHORT='
   ```
   Brak `ID_SERIAL_SHORT` albo wartość wyglądająca na ogólną (np. same zera)
   oznacza, że nazwa nie jest unikalna. Wtedy nie podłączaj drugiego adaptera
   tego typu. Strażnik i tak sprawdza tożsamość radia (punkt 7), więc na obcym
   urządzeniu nie zakluczuje nadawania. Inny sposób wskazania portu (`by-path`
   albo własne dowiązanie z reguły udev) wymagałby zmiany walidacji
   konfiguracji (zasada 8 w `AGENTS.md`) — jeśli go potrzebujesz, zgłoś to.
4. **Nikt inny nie otwiera portu.** Jądro podnosi DTR/RTS przy każdym otwarciu
   urządzenia — także przez inny program, co może zakluczować radio. Strażnik
   blokuje inne otwarcia (`TIOCEXCL`), ale jądro zwalnia z tej blokady roota,
   a programy systemowe działają jako root. Strażnik pilnuje linii w każdym
   obiegu pętli i od razu je opuszcza (stan `line_fault`), lecz lepiej, żeby do
   tego w ogóle nie dochodziło:
   - **ModemManager** sonduje nowe porty szeregowe. Wyłącz to dla DE-19
     regułą udev (wartość `ID_SERIAL` odczytasz poleceniem
     `udevadm info /dev/serial/by-id/...`; dla DE-19 z CH340 to
     `1a86_USB_Serial` — reguła obejmie wtedy każdy CH340, co nie przeszkadza):
     ```
     # /etc/udev/rules.d/90-remoteg90ctl.rules
     SUBSYSTEM=="tty", ENV{ID_SERIAL}=="1a86_USB_Serial", ENV{ID_MM_DEVICE_IGNORE}="1"
     ```
     ```sh
     sudo udevadm control --reload && sudo udevadm trigger
     udevadm info /dev/serial/by-id/... | grep ID_MM_DEVICE_IGNORE   # ma być =1
     ```
   - **brltty** (Ubuntu) przejmuje CH340, gdy DE-19 jest wpięty przez hub
     Terminus `1a40:0101` (popularny tani hub; tak zapisano regułę w pakiecie
     Ubuntu). **Zaobserwowane 02.11.2026:** port `ttyUSB0` znikał zaraz po
     wpięciu, a brltty wysyłał i odbierał bajty na linii CI-V z pominięciem
     strażnika. Objaw w `journalctl -k`: `interface 0 claimed by ch341 while
     'brltty' sets config`. Jeśli nie używasz monitora brajlowskiego:
     ```sh
     sudo systemctl stop brltty-udev.service
     sudo systemctl mask brltty-udev.service brltty.service   # albo: sudo apt remove brltty
     ```
     Potem wypnij i wepnij kabel DE-19.
   - Nie uruchamiaj `minicom`, `screen` ani `cat` na porcie radia.
5. **Linie PTT bez programu** (poz. 8, 8a) — z radiem na sztucznym obciążeniu
   i **zatrzymanym strażnikiem** (`sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard`)
   sprawdź, czy radio przechodzi w nadawanie przy: rozruchu komputera, wpięciu
   kabla USB DE-19, odpięciu i ponownym wpięciu. Jeśli tak — **nie idź dalej**;
   potrzebne jest rozwiązanie sprzętowe albo reguła udev — zgłoś to.
   **Wynik (02.11.2026):** brak kluczowania przy wpięciu kabla i przy starcie
   komputera (usługi wyłączone).
6. **Impuls przy otwarciu portu** (poz. 8). Strażnik ustawia DTR i RTS na
   nieaktywne przed `open()`, opuszcza je zaraz po otwarciu i potwierdza
   odczytem. Jądro może jednak podnieść linie na ułamek sekundy w samym
   `open()`, zanim program zdąży zareagować — **programowo nie da się tego
   wykluczyć**. Sprawdź to: w konfiguracji ustaw `backend = "hardware"` z
   `allow_transmit = false`, uruchom strażnika kilka razy
   (`sudo systemctl restart remoteg90ctl-guard`) i obserwuj radio (dioda TX,
   miernik mocy). Jeśli radio choćby mignie nadawaniem — zatrzymaj się i zgłoś.

   **Wynik (02.11.2026): radio miga nadawaniem przy każdym starcie strażnika**
   (ułamek sekundy). Jądro podnosi RTS w `open()`, strażnik opuszcza je ok. 1 ms
   później, ale to wystarcza, żeby G90 przełączył się na nadawanie. Ryzyko
   przyjęte w projekcie ([decisions.md](decisions.md), D23). Zasada:
   **strażnika uruchamiaj i `port-open` wykonuj z radiem w trybie SSB (USB/LSB),
   na sztucznym obciążeniu albo antenie** — w SSB bez modulacji takie mignięcie
   nie wysyła w antenę praktycznie nic; w CW, AM i FM wysłałoby nośną.
   Mignięcie zdarza się tylko przy otwarciu portu: przy starcie i restarcie
   strażnika (także po awarii) i przy `port-open`. W zwykłej pracy port jest
   otwarty cały czas.
7. **Adres CI-V i prędkość** (poz. 7, 7a) — odczytaj z menu radia; wpisz w
   `radio.radio_address` i `radio.baudrate`. Źródła podają różne adresy
   (0x88 i 0x70; według Radioddity domyślny to 0x70, a radio odpowiada też na
   inne), dlatego nie ma wartości domyślnej. Szczegóły: `docs/civ-g90.md`.
   Jeśli nie znajdziesz adresu w menu, ustal go odczytem częstotliwości
   (`remoteg90ctl freq` przy `allow_transmit = false`): odpowiedź przychodzi
   z adresu radia. **Wynik (02.11.2026):** 19200 bodów, adres **0x70**.

   **Tożsamość radia** (`docs/decisions.md` D32). Po każdym otwarciu portu
   strażnik pyta radio o identyfikator (`1D 19`) i bez zgodności z
   `radio.identity` blokuje nadawanie (CAT działa). Przy pierwszym uruchomieniu
   usuń `identity` z konfiguracji albo zostaw wartość z przykładu, uruchom
   strażnika i odczytaj odpowiedź radia:
   ```sh
   remoteg90ctl status      # wiersz "radio identity", pole z odpowiedzią radia
   ```
   Wpisz ją jako `radio.identity`, jeśli po drugiej stronie na pewno jest G90.
   **Wynik (02.11.2026, firmware 1.81):** odpowiedź `1D 19 00 90`, czyli
   `radio.identity = "190090"` (model G90, 0x0090).
8. **CAT bez nadawania** — przy `allow_transmit = false`: `remoteg90ctl status`
   (port `open`, linie potwierdzone), `freq`, `mode`. Strażnik przepuszczał wtedy
   tylko cztery polecenia CI-V (odczyt i ustawienie częstotliwości i trybu).
   **Wynik (02.11.2026):** wszystko działa (ustawienie trybu po poprawce:
   G90 wymaga bajtu filtra). Tryb danych U-D/L-D nie jest widoczny w tym odczycie —
   `remoteg90ctl mode` pokazuje wtedy USB/LSB (`docs/civ-g90.md`). Zmiana pasma
   przełącza przekaźniki filtrów w radiu — słychać kliknięcie, to nie nadawanie.
9. **Która linia kluczuje PTT** (poz. 4) — `radio.ptt_line = "rts"` albo
   `"dtr"`. Ustal pomiarem (dokumentacja DE-19, miernik), nie metodą prób na
   antenie. Źródła o DE-19 nie podają linii (zalecają PTT przez CAT i obie linie
   nisko). **Wynik (02.11.2026, `tx-test --seconds 2` na sztucznym obciążeniu):
   kluczuje RTS, DTR nie kluczuje** — `ptt_line = "rts"`.
10. **Pierwsze nadawanie** — tylko na sztuczne obciążenie, mała moc, przy
   obserwacji poziomu wejścia karty I/Q (poz. 3):
   ```toml
   [safety]
   allow_transmit = true
   ```
   ```sh
   sudo systemctl restart remoteg90ctl-guard remoteg90ctl-server
   remoteg90ctl tx-test --seconds 2 --dummy-load-connected
   ```
   Sprawdź, że radio wraca do odbioru: po końcu testu, po Ctrl+C, po
   `sudo systemctl stop remoteg90ctl-server` w trakcie testu, po
   `sudo systemctl kill -s SIGKILL remoteg90ctl-guard` w trakcie testu
   (to ostatnie zostawia blokadę TX — zdejmij ją `remoteg90ctl tx-unlock`).
   **Wynik (02.11.2026):** wszystkie przypadki zwalniają PTT, także zamrożony
   klient (`kill -STOP` na procesie CLI — zwolnienie po 2 s) i
   `systemctl kill -s SIGKILL` serwera. Po zabiciu strażnika radio wróciło do
   odbioru od razu (HUPCL), a strażnik wystartował z blokadą. Wyjście I/Q podczas
   nadawania jest wyciszone o ok. 16 dB; przy przełączaniu TX↔RX pojawia się
   trzask (szczyt −6 dBFS, bez przesterowania karty).

   Po testach wyłącz nadawanie z powrotem (`allow_transmit = false`) i włączaj
   je świadomie, gdy jest potrzebne.

## 6. Karta I/Q

Wodospad czyta wyjście I/Q radia z osobnej karty dźwiękowej (karta nr 2).
Otwiera ją wyłącznie serwer, bezpośrednio przez ALSA (`docs/decisions.md` D35).
Wymagany jest systemowy PortAudio (`install.sh` sprawdza go przed instalacją):

```sh
sudo apt install libportaudio2
```

1. **Stała nazwa karty.** Reguła udev nadaje karcie I/Q nazwę ALSA `G90IQ`
   (numer karty zmienia się między startami — zasada 8). W regule są
   identyfikatory karty „Speed Dragon” (`0d8c:8810`); inną kartę odczytaj
   z `lsusb` i popraw regułę. **Nie dopasuj karty z DE-19** (`0d8c:0012`).
   ```sh
   sudo cp deploy/udev/91-remoteg90ctl-iq.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   Wypnij i wepnij kartę I/Q, potem sprawdź: `cat /proc/asound/cards` — przy
   karcie ma być `[G90IQ ]`.
2. **PipeWire bez tej karty.** Na komputerze z pulpitem PipeWire zajmuje
   każdą kartę. Wyłącz w nim kartę I/Q (WirePlumber 0.4 — plik `.lua`,
   0.5 — plik `.conf`; wersję pokaże `wireplumber --version`):
   ```sh
   mkdir -p ~/.config/wireplumber/main.lua.d
   cp deploy/wireplumber/51-remoteg90ctl-iq.lua ~/.config/wireplumber/main.lua.d/
   systemctl --user restart wireplumber
   ```
   Sprawdź: `pactl list cards short` nie pokazuje już karty Speed Dragon.
3. **Konfiguracja** (`[iq]`, opis w `config/example.toml`):
   ```toml
   [iq]
   source = "alsa"
   device = "hw:CARD=G90IQ,DEV=0"
   sample_rate = 96000     # wyjście I/Q G90 sięga ok. ±40 kHz (pomiary, poz. 1)
   sample_bits = 24
   swap_channels = false
   invert_q = true          # zmierzone z filtrem pętli masy (pomiary, poz. 2)
   dc_removal = true
   amplitude_balance = 1.0
   phase_correction_deg = 0.0
   fft_size = 8192
   line_rate = 20
   recordings_dir = "/var/lib/remoteg90ctl-server/recordings"
   ```
   Potem `sudo systemctl restart remoteg90ctl-server` (strażnika nie trzeba
   restartować — radio nie mignie).
4. **Sprawdzenie i pomiary** ([pomiary](g90-measurements.md), poz. 1–2c i 9):
   ```sh
   remoteg90ctl iq status                    # działa? poziom, przepełnienia
   remoteg90ctl iq record --seconds 5        # zapis WAV na serwerze, podaje ścieżkę
   remoteg90ctl iq analyze PLIK.wav [--signal-hz +5000]
   ```
   `iq analyze` działa bez serwera: pokazuje poziom i składową stałą obu
   kanałów, ich balans, poziom szumu, najsilniejsze sygnały (przesunięcie od
   środka I/Q) i — dla znanego sygnału — tłumienie lustra. Znak przesunięcia
   pokazuje, czy mapowanie kanałów jest dobre: sygnał powyżej częstotliwości
   z CAT ma wyjść z plusem.

5. **Wodospad w przeglądarce**. W `[server]`:
   ```toml
   listen = ["127.0.0.1:8078"]
   ```
   Po `sudo systemctl restart remoteg90ctl-server` otwórz na tym samym
   komputerze **http://127.0.0.1:8078** (albo `http://localhost:8078`). Serwer
   słucha wyłącznie na pętli zwrotnej (zasada 15) i przyjmuje tylko własne
   strony (`docs/decisions.md` D34).

   **Konta** (`docs/decisions.md` D48). Przy pierwszym otwarciu strony świeżej
   instalacji od razu otwiera się okno **konta administratora** (pierwszego
   konta): znak (to także login), lokator QTH stacji zdalnej i hasło (co
   najmniej 10 znaków). Administrator jako jedyny dodaje i usuwa kolejne konta
   i restartuje serwer ze strony (okno Zasoby). Stację obsługuje naraz jedno
   konto; gdy pracuje inne, strona administratora pokazuje, kto to jest, i
   pozwala zamknąć jego stronę i je wylogować (przejęcie stacji). Menu pod znakiem w
   prawym górnym rogu: zmiana hasła, „Add account”, „Log out”. Bez zalogowania strona nic
   nie pokazuje i niczego nie pozwala. Naraz może być zalogowane jedno konto
   (to samo na kilku urządzeniach — tak). Sesja trwa 30 dni. Konta są w
   `/var/lib/remoteg90ctl-server/accounts.json` (hasła tylko jako skróty).
   Konto pamięta też ustawienia: radia (VFO, częstotliwość, tryb, split,
   PRE/ATT, AGC, NB, RF gain, ATU, moc — przywracane w radiu po zalogowaniu)
   i strony (wodospad, filtr, audio, pamięć pasm itd.).
   Zapomniane hasło albo zbędne konto — w terminalu serwera:
   ```sh
   remoteg90ctl user list
   remoteg90ctl user passwd SP1ABC     # pyta o nowe hasło; sesje konta wygasają
   remoteg90ctl user delete SP2XYZ     # nie dotyczy administratora
   ```

   **Z telefonu przez Tailscale** (`docs/decisions.md` D38). Serwer zostaje na
   pętli zwrotnej, stronę wystawia `tailscale serve` z HTTPS, tylko w Twoim
   tailnecie:
   ```toml
   [server]
   allowed_hosts = ["<komputer>.<twój-tailnet>.ts.net"]   # nazwa komputera w tailnecie
   ```
   ```sh
   sudo systemctl restart remoteg90ctl-server
   sudo tailscale serve --bg 8078          # cofnięcie: sudo tailscale serve reset
   ```
   Na telefonie: **https://<komputer>.<twój-tailnet>.ts.net** — po zalogowaniu
   na konto (wyżej). **Nigdy `tailscale funnel`:** wystawiłby stronę całemu
   internetowi. Serwer odrzuca takie żądania (brak tożsamości Tailscale, D57),
   a kreator ostrzega, gdy Funnel jest włączony.

   Obsługa: kółko myszy — powiększenie wokół kursora; przeciąganie — przesuwanie;
   na ekranie dotykowym przeciąganie jednym palcem i rozciąganie dwoma.
   **Kliknięcie albo stuknięcie** w widmo lub wodospad stroi radio na tę
   częstotliwość, dosuniętą do kroku z pola „krok” (auto: 10 Hz w CW i CW-R,
   100 Hz w pozostałych trybach; wybór zapamiętany w przeglądarce). W polu
   „MHz” wpisz częstotliwość (np. `21.074`) i naciśnij Enter; „tryb” przełącza
   tryb radia. **Szukanie stacji:** „◀ seek” i „seek ▶” obok częstotliwości
   (klawisze `<` i `>`, także `,` i `.` bez Shift albo Alt + PgDn / PgUp) stroją
   na następną stację w dół lub w górę, znalezioną w widmie wodospadu
   (najwyższy poziom z ostatnich 3 s, co najmniej 10 dB nad szumem i 200 Hz
   szerokości), ustawioną w paśmie odbioru trybu. Bez stacji w zasięgu radio
   przesuwa się o 40 kHz i szuka dalej, także poza pasmami, do 0,5 albo 30 MHz;
   ponowne naciśnięcie, przestrojenie gdzie indziej albo start FT8/FT4 je
   zatrzymuje. W czasie odbioru FT8/FT4 szukanie jest wyłączone. To tylko
   odbiór: nadawanie poza pasmami nadal jest odrzucane.
   Strojenie w czasie nadawania jest odrzucane (komunikat obok pól). Skala
   idzie za VFO (odczyt co 0,5 s, także po kręceniu gałką). Czerwona linia to
   częstotliwość z radia. Pomarańczowa plakietka „skala nieskalibrowana” —
   dla bieżącego trybu brak wpisu w `[iq.center_offset_hz]`.
   Jasny pas przy VFO to pasmo odbioru bieżącego trybu z `[iq.passband_hz]`.
   G90 nie podaje i nie przyjmuje swojego filtra przez CAT (`docs/decisions.md`
   D37). Pola filtra „od” i „do” obok widma odbioru przesuwają pas na ekranie tak, żeby
   zgadzał się z filtrem w radiu (zapamiętane w przeglądarce, osobno dla trybu;
   „domyślny” wraca do wartości z konfiguracji).

   **Moc i nadawanie** (`docs/decisions.md` D33): serwer co sekundę odczytuje
   miernik mocy radia (CI-V `15 11`), także bez otwartej strony. Gdy radio
   wysyła moc, pojawia się czerwone „TX”, a linie wodospadu są przyciemnione (także 0,5 s po
   końcu, bo wyjście I/Q trzaska). „TX” gaśnie 3 s po ostatnim odczycie mocy,
   żeby przerwy w mowie nie dzieliły nadawania. **Czerwony pasek „Radio nadaje
   bez dzierżawy”** oznacza moc bez nadawania z programu — PTT na mikrofonie,
   TUNE albo VOX; serwer zapisuje wtedy w logu `tx_without_lease`. To tylko
   informacja, nie zabezpieczenie. Samo PTT w SSB bez dźwięku nie daje mocy,
   więc nie jest widoczne.
   W nagłówku jest **moc ustawiona w radiu** (w watach). Miernik mocy G90
   kończy skalę przy ok. 3 W, więc służy tylko do wykrywania nadawania.
   **Pomarańczowy pasek „Wysoki SWR”** pojawia się, gdy miernik SWR radia
   stoi na końcu skali przez dwa odczyty w czasie nadawania — to SWR ok. 2 lub
   więcej (koniec skali może wypaść już przy ok. 1,7). Zostaje do następnego
   nadawania z niższym SWR. Skali SWR strona nie pokazuje, bo powyżej ok. 1,5
   jest niewiarygodna; także niżej odczyt jest tylko przybliżony (skala
   nieliniowa, sprawdzona jedynie z wyświetlaczem radia, na jednej antenie).

   **Tuner antenowy** (`docs/decisions.md` D43): w ramce „Radio” przełącznik
   **ATU** (wł./wył.) i przycisk **Strojenie**. Strojenie **nadaje nośną** ok.
   1–1,5 s (przy źle dopasowanej antenie dłużej) na bieżącej częstotliwości i nie da się go przerwać, dlatego strona
   pyta o potwierdzenie. Strażnik pozwala na nie tylko przy
   `safety.allow_atu_tune = true` (osobno od `allow_transmit`) i bez blokady;
   strojenie trwa dla niego, dopóki miernik mocy nie pokaże zera (najwyżej
   30 s, dłużej — blokada nadawania do `tx-unlock`), kolejne najwcześniej
   10 s po końcu poprzedniego; zapisuje je w dzienniku TX. Moc strojenia nie
   zależy od ustawionej mocy (pomiar: ponad 2 W przy ustawionym 1 W). Z
   wiersza poleceń: `remoteg90ctl atu tune`.
   **Program nie potwierdza, że tuner znalazł dopasowanie:** radio nie podaje
   wyniku strojenia (13.11.2026: SWR powyżej 3,5 po strojeniu, bez żadnej
   informacji na wyświetlaczu), a pasek „Wysoki SWR” pojawi się dopiero przy
   następnym nadawaniu i dopiero przy SWR ok. 2. Do pracy zdalnej używaj
   dobrej, sprawdzonej anteny, którą tuner G90 łatwo dopasowuje na używanych
   pasmach, i sprawdź ją na miejscu zewnętrznym miernikiem SWR.
   **Przy wygaszonym ekranie G90 nie stroi** — wykona zaległe strojenie
   dopiero po włączeniu ekranu, bez wiedzy programu. Nie stroić zdalnie, gdy
   ekran mógł zgasnąć (najlepiej wyłączyć wygaszanie w menu radia).
   Widmo symetryczne wokół VFO, widoczne podczas mówienia do mikrofonu, to nie
   sygnał radiowy, tylko zakłócenie z toru zasilania lub audio
   ([pomiary](g90-measurements.md), poz. 3).

6. **Odsłuch w przeglądarce**. Karta dźwiękowa DE-19 dostaje stałą
   nazwę `G90AUDIO`:
   ```sh
   sudo cp deploy/udev/92-remoteg90ctl-audio.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   (potem odłącz i podłącz DE-19), a w konfiguracji sekcja `[audio]` jak w
   `config/example.toml` oraz w `[radio]` `ssb_data_mode = true` — G90 wysyła
   dźwięk do DE-19 tylko w U-D/L-D. Potrzebna jest systemowa biblioteka Opus
   (`sudo apt install libopus0`, zwykle już jest) i `amixer` (pakiet
   `alsa-utils`). Na stronie ramka „Receive audio” (pierwszy wiersz): **Listen**, quality (Opus 16–48
   kbit/s albo „raw” na tym samym komputerze), volume. Słuchać może jedna
   przeglądarka naraz. Przeglądarka musi mieć WebCodecs (Chrome, Edge,
   Firefox, nowsze Safari).

7. **Nadawanie fonią z przeglądarki**. Radio w **U-D/L-D**
   (`radio.ssb_data_mode = true`, wybierz USB/LSB na stronie — także przy
   przełączaniu VFO i splicie, `docs/civ-g90.md`, notatka na końcu) i
   `safety.allow_transmit = true`. Na stronie, obok Log i FT8: przycisk
   **PTT (hold)** — nadaje tylko, dopóki go trzymasz. Przy pierwszym
   wciśnięciu w sesji strona pyta o potwierdzenie nadawania na antenę; po
   potwierdzeniu wciśnij PTT ponownie. Przeglądarka poprosi o dostęp do
   mikrofonu. Poziom ogranicza serwer (`audio.tx_peak_dbfs`). VOX w radiu musi
   być wyłączony (AGENTS.md sekcja 3).

## 7. Gdy coś pójdzie nie tak

- **TX zablokowany** — `remoteg90ctl status` pokazuje powód (`lockout`,
  `TX blocked by`). Blokadę po twardym limicie lub awarii zdejmuje
  `remoteg90ctl tx-unlock`.
- **`radio identity` inne niż `confirmed`** — nadawanie zablokowane, CAT
  działa. `not_configured`: wpisz odpowiedź radia do `radio.identity`;
  `mismatch`: na porcie odpowiada inne urządzenie albo radio (sprawdź, czy nie
  jest podłączony drugi adapter CH340); `rejected`: radio nie zna zapytania
  (firmware starsze niż 1.8?); `no_reply`: brak odpowiedzi — najczęściej radio
  było jeszcze wyłączone, gdy strażnik startował (08.11.2026): włącz radio i
  zrestartuj strażnika (radio w SSB); jeśli to nie pomoże — sprawdź adres,
  prędkość i kabel. Po poprawce: restart strażnika (`remoteg90ctl port-open`
  przy otwartym porcie nie sprawdza tożsamości ponownie).
- **`duty cycle` w `TX blocked by`** — w ostatnich 600 s nadawanie trwało już
  300 s (`docs/decisions.md` D31). Nadawanie wróci samo, gdy okno się zwolni;
  `remoteg90ctl status` pokazuje, za ile sekund. Bez `tx-unlock`. Jeśli powodem
  jest nieczytelna albo niezapisana historia (`tx_history.json`), sprawdź
  katalog stanu `/var/lib/remoteg90ctl` (miejsce na dysku, uprawnienia).
- **Port utracony** (odłączony USB, błąd I/O) — strażnik nie otwiera go sam.
  Po sprawdzeniu połączenia: `remoteg90ctl port-open`.
- **Port w stanie `line_fault`** — linia PTT stała się aktywna, choć nikt nie
  nadawał; strażnik ją opuścił. Sprawdź, kto otworzył port
  (`sudo fuser -v /dev/serial/by-id/...`), usuń przyczynę, potem
  `remoteg90ctl port-open`.
- **Natychmiastowe zatrzymanie** — `sudo systemctl stop remoteg90ctl-guard`
  (strażnik opuszcza linie; jądro opuszcza je także przy zabiciu procesu).
  Ostatecznie: odłącz kabel USB DE-19 albo wyłącz radio.
- **Ryzyko, którego program nie usuwa:** jeśli strażnik zawiśnie wewnątrz
  sterownika USB (w jądrze, w stanie nieprzerywalnym), nawet zabicie procesu
  nie zamknie portu i linie mogą zostać aktywne. G90 nie ma TOT (punkt 2),
  więc radio samo nie przerwie nadawania — znaj drogę do szybkiego odłączenia
  kabla USB DE-19 albo zasilania radia.

## 8. Przykład: mały komputer przy radiu — Fujitsu Futro S740 z DietPi

Tak wygląda komputer przy radiu w stacji zdalnej, dla której powstał ten
projekt; inny mały komputer z Debianem przygotujesz tak samo. Futro S740: Intel
Celeron J4105 (4 rdzenie, 1,5–2,5 GHz), 4 GB RAM DDR4, **dysk SSD M.2 8 GB**.
System: **DietPi na Debianie 13 (Trixie)** — mało miejsca na dysku, logi w RAM
(RAMlog), Python 3.13 w systemie, bez środowiska graficznego i bez PipeWire
(karty przez ALSA bezpośrednio). DietPi nie ma D-Bus, więc zegar sprawdza
**chrony** (D53). Internet: **modem Huawei E3276 w
trybie HiLink** (karta SIM, LTE).

Dysk 8 GB wystarcza, ale bez zapasu: bez swapu na dysku, nagrania I/Q
kasowane. Dysk M.2 SATA można wymienić na większy (format złącza sprawdź w
swoim egzemplarzu).

### 8.1. Instalacja krok po kroku

Instalator `install.sh` (sekcja 0) robi wszystko, co nie wymaga Ciebie (8.2).
Zostaje: zainstalować DietPi, przy przenosinach zrobić kopię stanu na starym
komputerze, zalogować Tailscale i sprawdzić sprzęt.

1. **Modem** (raz, na dowolnym komputerze z przeglądarką): wepnij E3276,
   otwórz `http://192.168.8.1`, ustaw PIN karty SIM (albo go wyłącz) i APN
   operatora. Modem je zapamiętuje.
2. **DietPi:** obraz `DietPi_NativePC-UEFI-x86_64-Trixie_Installer.iso`
   (dietpi.com → Downloads → Native PC) nagraj na pendrive (np. balenaEtcher
   albo `dd`), uruchom z niego S740 i zainstaluj na wewnętrzny SSD. Gdyby S740
   nie wystartował w trybie UEFI — wersja `BIOS`. Do instalacji podłącz kabel
   Ethernet (internet), a modem wepnij od razu. Pierwsze uruchomienie DietPi
   samo się aktualizuje; zaloguj się jako `dietpi` (domyślne hasło
   zmień, gdy DietPi o to poprosi).
3. **Kod i system:** polecenie z sekcji 0.
   Instalator pobiera kod do `~/remoteG90ctl` i przygotowuje system.
4. **Tylko przy przenosinach — kopia stanu na starym komputerze** (konta, ustawienia, dziennik łączności,
   blokada i historia strażnika, dziennik TX, konfiguracja — 8.4):
   ```sh
   sudo deploy/services.sh --stop
   sudo deploy/backup.sh
   f=$(ls remoteg90ctl-backup-*.tgz | tail -1)
   ssh dietpi@<adres-S740> "cat > $f" < "$f"   # scp na czystym DietPi nie działa (brak sftp-server)
   ```
5. **Stan ze starego komputera** na S740:
   ```sh
   sudo ~/remoteG90ctl/install.sh --restore ~/remoteg90ctl-backup-*.tgz
   ```
   Bez `--restore` zostaje czysta konfiguracja (symulator). Instalator można
   uruchomić ponownie, np. po wpięciu modemu. Usług nie uruchamia.
6. **Dalej:** `sudo remoteg90ctl setup` (sekcja 0) albo ręcznie, po kolei:
   - `sudo tailscale up` i logowanie linkiem;
   - w `/etc/remoteg90ctl/config.toml` `allowed_hosts` z nazwą S740 w
     tailnecie (`tailscale status`; po przeniesieniu jest tam jeszcze nazwa
     starego komputera), potem `sudo tailscale serve --bg 8078`;
   - nazwy portu i kart (`ls /dev/serial/by-id/`, `arecord -l`) zgodne z
     konfiguracją;
   - **lista kontrolna sprzętu od nowa na S740** (sekcja 5; 8.3);
   - `sudo deploy/services.sh --start`;
   - w logu serwera `clock_synchronized synchronized=True`.
7. **Na starym komputerze** usługi zostają wyłączone (`deploy/services.sh --stop`
   w pkt 4 wyłącza też autostart): radio podłączone jest tylko do jednego
   komputera.

### 8.2. Co robi instalator (`install.sh`)

- Sprawdza system (Debian 12/13, Ubuntu 22.04+, Raspberry Pi OS, DietPi;
  x86_64 albo aarch64) — inny system tylko ostrzega, 32-bitowy ARM odmawia.
- **Pakiety:** `git curl ca-certificates libportaudio2 libopus0 alsa-utils
  sysstat chrony`.
- **brltty** — jeśli jest, zatrzymuje go i maskuje (na Ubuntu przejmuje
  CH340). **ModemManager** — na komputerze bez pulpitu maskuje, na komputerze
  z pulpitem zostawia (może obsługiwać modem laptopa), a reguła udev trzyma go
  z dala od CH340. Sondując porty szeregowe, mogłyby podnieść RTS na CH340 i
  **zakluczować radio** (sekcja 5). Na DietPi zwykle ich nie ma.
- **Reguły udev:** `90-remoteg90ctl-serial.rules` (ModemManager pomija CH340),
  `91-…-iq.rules` (karta I/Q jako `G90IQ`), `92-…-audio.rules` (karta DE-19 jako
  `G90AUDIO`). Inny egzemplarz kart — popraw identyfikatory w regułach
  (`lsusb`).
- **Zegar:** chrony (zastępuje systemd-timesyncd); na DietPi w
  `/boot/dietpi.txt` `CONFIG_NTP_MODE=0` (*Custom* — DietPi nie synchronizuje
  sam). Serwer
  odmawia nadawania FT8/FT4, gdy błąd zegara według chrony przekracza 0,2 s
  (D53); zaraz po starcie, zanim chrony zbierze pomiary, nadawanie FT8/FT4
  jest przez kilka minut zablokowane — tak ma być.
- **Usypianie** zamaskowane; na DietPi także **swap** na dysku wyłączony
  (`dietpi-set_swapfile 0`).
- **Internetu nie konfiguruje** — połączenie (Ethernet, Wi-Fi, modem LTE) to
  Twoja sprawa; dla modemu Huawei E3276 — uwagi niżej.
- **uv** (dla Twojego użytkownika) i **Tailscale** (skrypt instalacyjny z
  tailscale.com), jeśli ich nie ma.
- **Kod:** klon do `~/remoteG90ctl` (albo aktualizacja), gdy instalator
  przyszedł przez `curl`; uruchomiony z katalogu z kodem — ten katalog.
- **Program:** `deploy/install.sh` (na Debianie 13 systemowy Python 3.13; na
  Debianie 12 i Ubuntu 22.04 uv pobiera Pythona 3.12+), bez pamięci podręcznej
  pakietów; z `--restore` — `deploy/restore.sh` i ponowne sprawdzenie
  konfiguracji. Istniejąca instalacja: po aktualizacji restart usług.

DietPi trzyma logi w RAM (RAMlog, czyszczone co godzinę) — oszczędza dysk;
dziennik TX jest w `/var/lib` i nie ginie. Gdy coś trzeba zbadać, zapisz
`journalctl` od razu. Poziom logów: `INFO`; `DEBUG` tylko na chwilę.

**Internet przez LTE (Huawei E3276, HiLink) — uwagi:**

- **Podłączenie** robisz sam. W trybie HiLink modem jest kartą sieciową USB
  (`ip -br link`: nowy interfejs, np. `enx…` albo `usb0`), adres dostaje przez
  DHCP. Z NetworkManagerem (Ubuntu z pulpitem) działa od razu; na DietPi i
  Debianie bez pulpitu (ifupdown):
  ```sh
  printf 'allow-hotplug <interfejs>\niface <interfejs> inet dhcp\n' | sudo tee /etc/network/interfaces.d/lte
  sudo ifup <interfejs>
  ```
  (`/etc/network/interfaces` musi zawierać `source /etc/network/interfaces.d/*`.)
  Jeśli `lsusb` pokazuje modem tylko jako napęd CD — `sudo apt install
  usb-modeswitch` i ponowne wpięcie. **ModemManagera nie instaluj** — HiLink
  go nie potrzebuje, a sondując porty szeregowe mógłby zakluczować radio.
- **Tailscale** działa przez LTE także za NAT-em operatora; po `tailscale up`
  S740 jest widoczny w tailnecie. `tailscale serve` jak w sekcji 6.
- **Transfer:** odsłuch Opus 48 kbit/s to ok. 21 MB na godzinę, wodospad przy
  20 liniach/s dużo więcej — okienko „resources” pokazuje ruch i szacunek na
  miesiąc; przy małym pakiecie zmniejsz prędkość wodospadu albo go wyłącz.
- **Zakłócenia od nadajnika KF:** modem na krótkim przedłużaczu USB, z dala
  od anteny i kabla antenowego, z ferrytem na kablu. Przy pierwszych
  nadawaniach obserwuj, czy połączenie LTE i Tailscale wytrzymują (zerwanie
  połączenia przeglądarki kończy dzierżawę — zasada 5 — więc radio przestanie
  nadawać, ale łączność się urwie).

### 8.3. Program

1. Kod pobiera instalator (8.1 pkt 3).
2. `deploy/install.sh` (uruchamia go instalator) korzysta z
   systemowego Pythona 3.13; `ft8lib` ma gotowy pakiet dla x86_64 — kompilator
   nie jest potrzebny. Aktualizacja programu później: ponownie
   `sudo ~/remoteG90ctl/install.sh` (albo polecenie z sekcji 0) — przechodzi na
   najnowsze wydanie i aktualizuje `uv`, gdy jest starszy niż 0.13.4. System,
   Tailscale i biblioteki systemowe instalator tylko instaluje, nie aktualizuje:
   rób to sam (`sudo apt update && sudo apt upgrade`, na DietPi też
   `dietpi-update`), także przy włączonych automatycznych aktualizacjach.
3. Sekcje 1–6 obowiązują jak na każdym komputerze, ze sprawdzeniem nazw: port
   (`/dev/serial/by-id/`), karty (`arecord -l`, `/dev/snd/by-id/`), reguły udev.
   Nie podłączaj drugiego adaptera CH340 (nazwa portu radia nie jest unikalna,
   sekcja 5). Modem LTE ma własne nazwy i nie przeszkadza.
4. **Lista kontrolna sprzętu (sekcja 5) od nowa na S740**, na sztucznym
   obciążeniu albo z małą mocą: brak nadawania przy rozruchu komputera, przy
   wpięciu i wypięciu DE-19 **i modemu LTE**, mignięcie tylko przy starcie
   strażnika (start w SSB). Wyniki z innego komputera nie przenoszą się same — to inny
   kontroler USB i inny system.
5. **Dziennik TX** — domyślnie w katalogu stanu strażnika
   (`/var/lib/remoteg90ctl/tx.jsonl`). Konfiguracja ze starszej instalacji może
   mieć jeszcze starą ścieżkę w `/var/log`; popraw ją:
   ```toml
   [guard]
   tx_log_path = "/var/lib/remoteg90ctl/tx.jsonl"
   ```
   (`deploy/restore.sh` robi to sam, gdy stara ścieżka jest w RAM.)
6. **Czy serwer widzi chrony** w swoich ograniczeniach (usługa ma dostęp
   tylko do localhost): w logu serwera `clock_synchronized synchronized=True`,
   w okienku „resources” zegar bez ostrzeżenia. Ręcznie:
   ```sh
   sudo systemd-run --pipe --wait -p User=remoteg90ctl -p IPAddressDeny=any -p IPAddressAllow=localhost chronyc -c -n tracking
   ```
   Ma wypisać jeden wiersz z `Normal` na końcu.

### 8.4. Przeniesienie stanu z innego komputera

`deploy/backup.sh` zbiera w jedno archiwum: konfigurację, konta, sesje,
ustawienia, dziennik łączności (spójna kopia SQLite), stan strażnika (blokada,
historia cyklu pracy) i dziennik TX — bez nagrań I/Q. Archiwum ma hasła (jako
skróty) i sesje, więc powstaje z prawami tylko dla Ciebie (0600).
`deploy/restore.sh` rozkłada je na nowej maszynie do katalogów stanu usług
(`/var/lib/remoteg90ctl`, `/var/lib/remoteg90ctl-server`; innych nie przyjmuje),
bierze tylko znane, zwykłe pliki (dowiązania odrzuca), niczego istniejącego nie
nadpisuje, a ścieżkę dziennika TX w RAM (tmpfs) sam zmienia na katalog stanu
strażnika. **Odtwarzaj tylko własne kopie:** archiwum z obcych rąk to jak
cudzy skrypt uruchomiony jako root (zawiera też konfigurację radia i konta).

Na starym komputerze:
```sh
sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard
sudo deploy/backup.sh
f=$(ls remoteg90ctl-backup-*.tgz | tail -1)
ssh dietpi@<futro> "cat > $f" < "$f"   # scp na czystym DietPi nie działa (brak sftp-server)
```
Na S740 robi to `install.sh --restore` (8.1). Ręcznie: **po**
`sudo deploy/install.sh` (tworzy użytkownika usługi), z zatrzymanymi usługami:
```sh
sudo deploy/restore.sh ~/remoteg90ctl-backup-*.tgz
```
Potem w `/etc/remoteg90ctl/config.toml`: `server.allowed_hosts` (nazwa S740 w
tailnecie), port radia i nazwy kart, jeśli inne; jeszcze raz
`sudo deploy/install.sh` (sprawdza konfigurację) i dopiero
`sudo systemctl enable --now remoteg90ctl-guard remoteg90ctl-server`. Na
starym komputerze: `sudo systemctl disable remoteg90ctl-server remoteg90ctl-guard`
(radio jest podłączone tylko do jednego komputera). Archiwum po udanym
przeniesieniu usuń albo schowaj (kopia zapasowa).

Kopia zapasowa w trakcie pracy: `sudo deploy/backup.sh` działa też przy
włączonych usługach.

### 8.5. Pomiar obciążenia

Po uruchomieniu, przy włączonym i wyłączonym wodospadzie, z odsłuchem i bez:
```sh
pidstat -t -u -p $(systemctl show -p MainPID --value remoteg90ctl-server) 5 3
sudo "$(command -v uvx)" py-spy top --gil --pid $(systemctl show -p MainPID --value remoteg90ctl-server)
uv cache clean      # py-spy z uvx zostawia pamięć podręczną na małym dysku
```
`%usr` to praca programu, `%system` — jądra (odczyt kart małymi blokami,
wybudzenia). Na T470 serwer zajmował 15–18% rdzenia (09.11.2026); J4105 jest
wolniejszy od i5 w T470, ale ma 4 rdzenie — zapas powinien być duży.
Dekodowanie FT8/FT4 nie obciąża S740 — robi je przeglądarka (D52).
