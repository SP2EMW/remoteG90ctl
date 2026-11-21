# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501  (translations are easier to read on one line)
"""The setup's language: English (the texts in the code) or Polish.

Every text the setup shows goes through ``t``: the English text is the key,
and POLISH holds its translation, with the same ``{placeholders}``. A test
checks that every text has one (tests/test_setup.py). Program names,
commands and paths stay as they are.
"""

LANGUAGES = {"1": "en", "2": "pl"}
_language = "en"


def set_language(language: str) -> None:
    global _language  # noqa: PLW0603  (one language for the whole run)
    _language = language if language in LANGUAGES.values() else "en"


def language() -> str:
    return _language


def t(message: str, /, **values: object) -> str:
    template = POLISH.get(message, message) if _language == "pl" else message
    return template.format(**values)


# Answers in either language: y/yes/t/tak and n/no/nie; skipping needs the
# whole word in capitals.
YES = ("y", "yes", "t", "tak")
NO = ("n", "no", "nie")
SKIP_WORDS = ("SKIP", "POMIŃ", "POMIN")


def is_yes(answer: str) -> bool:
    return answer.strip().lower() in YES


def is_no(answer: str) -> bool:
    return answer.strip().lower() in NO


def is_skip(answer: str) -> bool:
    return answer.strip() in SKIP_WORDS


POLISH: dict[str, str] = {
    # Answers
    "[y/n]": "[t/n]",
    "[Y/n]": "[T/n]",
    "[y/N]": "[t/N]",
    "[y/n, SKIP]": "[t/n, POMIŃ]",
    "Number:": "Numer:",
    "Step {number} of {steps}": "Krok {number} z {steps}",
    "none of them": "żaden z nich",
    "\nStopped.": "\nPrzerwano.",
    # Start
    "remoteG90ctl — setup": "remoteG90ctl — konfiguracja",
    "I will find the radio and the sound cards, save the settings, check the radio with you"
    "\nbefore its first transmission, set up access from your phone and start the program."
    "\nThe radio transmits nothing until I ask you first. Ctrl+C stops at any moment.": (
        "Znajdę radio i karty dźwiękowe, zapiszę ustawienia, razem sprawdzimy radio przed"
        "\npierwszym nadawaniem, ustawimy dostęp z telefonu i uruchomimy program."
        "\nRadio nie nada niczego, dopóki o to nie zapytam. Ctrl+C przerywa w każdej chwili."
    ),
    "Stopped: {reason}": "Przerwano: {reason}",
    "Put it right and start again: sudo remoteg90ctl setup": (
        "Popraw to i uruchom ponownie: sudo remoteg90ctl setup"
    ),
    "Done. Transmitting stays on: the same radio as before.": (
        "Gotowe. Nadawanie zostaje włączone: to samo radio co wcześniej."
    ),
    "Done. Transmitting is off until the radio is checked (step 4).": (
        "Gotowe. Nadawanie jest wyłączone do czasu sprawdzenia radia (krok 4)."
    ),
    # Step 1
    "The radio": "Radio",
    "Looking for the DE-19 interface (the radio's USB connection)…": (
        "Szukam interfejsu DE-19 (połączenia radia z komputerem przez USB)…"
    ),
    "I do not see the DE-19.": "Nie widzę interfejsu DE-19.",
    "Plug its USB cable into this computer and watch the radio while you do:"
    "\nit must not start transmitting. If it does, unplug the cable and stop here.": (
        "Podłącz jego kabel USB do tego komputera i patrz przy tym na radio:"
        "\nnie może zacząć nadawać. Jeśli zacznie, odłącz kabel i przerwij konfigurację."
    ),
    "Plugged in? Look again?": "Podłączone? Szukać ponownie?",
    "the DE-19 was not found": "nie znaleziono interfejsu DE-19",
    "There are several adapters of the DE-19's kind. Which one is the DE-19?"
    "\n(Easier: unplug the others and start the setup again.)": (
        "Widzę kilka adapterów takiego typu jak w DE-19. Który z nich to DE-19?"
        "\n(Łatwiej: odłącz pozostałe i uruchom konfigurację ponownie.)"
    ),
    "no DE-19 chosen": "nie wybrano interfejsu DE-19",
    "DE-19 found": "Znalazłem interfejs DE-19",
    "Its sound card found (the radio's audio, both ways)": (
        "Znalazłem jego kartę dźwiękową (dźwięk z radia i do radia)"
    ),
    "I did not find the DE-19's sound card next to it. Which card is it?": (
        "Nie znalazłem karty dźwiękowej DE-19 tuż obok niego. Która to karta?"
    ),
    # Step 2
    "The waterfall": "Wodospad",
    "The waterfall shows the band around the radio's frequency. It needs a second"
    "\nUSB sound card, connected to the radio's I/Q output.": (
        "Wodospad pokazuje pasmo wokół częstotliwości radia. Potrzebna jest do niego druga"
        "\nkarta dźwiękowa USB, podłączona do wyjścia I/Q radia."
    ),
    "I found: {name}. Is it the one on the I/Q output?": (
        "Znalazłem: {name}. Czy to ta podłączona do wyjścia I/Q?"
    ),
    "Which card is on the radio's I/Q output?": "Która karta jest podłączona do wyjścia I/Q radia?",
    "I have no such card": "nie mam takiej karty",
    "Waterfall card: {name}": "Karta wodospadu: {name}",
    "No waterfall: the page will show the frequency scale only.": (
        "Bez wodospadu: strona pokaże tylko skalę częstotliwości."
    ),
    "To add it later: plug the card in and start the setup again.": (
        "Żeby dodać go później: podłącz kartę i uruchom konfigurację ponownie."
    ),
    # Step 3
    "Saving the settings": "Zapisuję ustawienia",
    "both sound cards are the same model, so the program cannot tell them apart"
    " (docs/install.md, section 6)": (
        "obie karty dźwiękowe to ten sam model, więc program ich nie odróżni"
        " (docs/install.md, sekcja 6)"
    ),
    "The sound cards got fixed names": "Karty dźwiękowe dostały stałe nazwy",
    "Unplug the card “{card}” and plug it in again, so that it takes its new name.": (
        "Odłącz kartę „{card}” i podłącz ją ponownie, żeby dostała nową nazwę."
    ),
    "The computer's own sound leaves the waterfall card alone": (
        "Dźwięk systemu nie będzie zajmował karty wodospadu"
    ),
    "Settings saved ({path})": "Ustawienia zapisane ({path})",
    "the new settings are not valid: {error}": "nowe ustawienia są błędne: {error}",
    "(the previous settings are kept in {path})": "(poprzednie ustawienia zachowane w {path})",
    "Transmitting stays on: the same radio as before": (
        "Nadawanie zostaje włączone: to samo radio co wcześniej"
    ),
    "Transmitting is off for now: it goes on after the radio check (step 4)": (
        "Nadawanie jest na razie wyłączone: włączysz je po sprawdzeniu radia (krok 4)"
    ),
    "The transmission log is in an old place ({log}), and {target} exists too:"
    " join them by hand (docs/install.md 8.3).": (
        "Dziennik nadawania jest w starym miejscu ({log}), a {target} też istnieje:"
        " połącz je ręcznie (docs/install.md 8.3)."
    ),
    "The transmission log (a record of every transmission) is still in its old place:"
    "\n{log}. Its place now is {target}, next to the program's other records.": (
        "Dziennik nadawania (zapis każdego nadawania) jest jeszcze w starym miejscu:"
        "\n{log}. Jego miejsce to teraz {target}, obok innych zapisów programu."
    ),
    "Move it? The program stops for a moment.": "Przenieść go? Program zatrzyma się na chwilę.",
    "The transmission log moved": "Dziennik nadawania przeniesiony",
    # Step 4
    "Checking the radio before it transmits": "Sprawdzenie radia przed nadawaniem",
    "The radio was checked on {time}.": "Radio było już sprawdzone: {time}.",
    "Check it again?": "Sprawdzić je jeszcze raz?",
    "Together we will check that the radio is connected safely. It takes a few minutes"
    "\nand is done once. Answer y (yes) or n (no: I stop, so you can put it right)."
    "\nTo leave something out, type SKIP: the page will then keep showing a warning.": (
        "Razem sprawdzimy, czy radio jest bezpiecznie podłączone. To kilka minut i robi się"
        "\nto raz. Odpowiadaj t (tak) albo n (nie: przerwę, żebyś mógł to poprawić)."
        "\nŻeby coś pominąć, wpisz POMIŃ: strona będzie wtedy pokazywać ostrzeżenie."
    ),
    "A. The computer": "A. Komputer",
    "The computer will not go to sleep": "Komputer nie przejdzie w uśpienie",
    "The computer can go to sleep. Run the installer (install.sh) again.": (
        "Komputer może przejść w uśpienie. Uruchom instalator (install.sh) ponownie."
    ),
    "No other program will take the radio's port": "Żaden inny program nie zajmie portu radia",
    "Another program may take the radio's port and make it transmit."
    " Run the installer (install.sh) again.": (
        "Inny program może zająć port radia i włączyć nadawanie."
        " Uruchom instalator (install.sh) ponownie."
    ),
    "B. The radio": "B. Radio",
    "Is the DE-19's cable in the IO/User socket on the radio's FRONT panel?"
    "\n(Not in COMM at the back: that one is for updating the radio's software.)": (
        "Czy kabel z DE-19 jest w gnieździe IO/User na PRZEDNIM panelu radia?"
        "\n(Nie w gnieździe COMM z tyłu: ono służy do aktualizacji oprogramowania radia.)"
    ),
    "the COMM socket at power-on can put the radio into software update mode": (
        "gniazdo COMM przy włączaniu może przełączyć radio w tryb aktualizacji"
    ),
    "Is VOX switched off? (On the radio: FUNC + VOL, then VOX: OFF.)": (
        "Czy VOX jest wyłączony? (W radiu: FUNC + VOL, potem VOX: OFF.)"
    ),
    "with VOX on, any sound from the computer makes the radio transmit,"
    " past every safeguard of this program": (
        "z włączonym VOX każdy dźwięk z komputera włącza nadawanie,"
        " z pominięciem wszystkich zabezpieczeń programu"
    ),
    "Is SWR THR in the radio's menu 3:1 or lower? (2.5 is recommended.)": (
        "Czy SWR THR w menu radia to 3:1 albo mniej? (Zalecane 2,5.)"
    ),
    "it is the only thing that protects the radio from a bad antenna": (
        "tylko to chroni radio przed złą anteną"
    ),
    "Is the radio on a dummy load or a tuned antenna?": (
        "Czy radio jest podłączone do sztucznego obciążenia albo dostrojonej anteny?"
    ),
    "the test transmissions would go into whatever is connected": (
        "próbne nadawanie poszłoby w to, co jest podłączone"
    ),
    "Is the radio set to 1 W and to SSB (USB or LSB)? (The POW and MODE buttons.)": (
        "Czy radio ma ustawione 1 W i tryb SSB (USB albo LSB)? (Przyciski POW i MODE.)"
    ),
    "Power and mode: {watts} W, {mode}": "Moc i tryb: {watts} W, {mode}",
    "the radio may transmit at more than 1 W": "radio może nadać z mocą większą niż 1 W",
    "power and mode": "moc i tryb",
    "the program's start makes the radio blink to transmit for an instant;"
    " in SSB that sends practically nothing, in CW, AM or FM a carrier": (
        "przy starcie programu radio na chwilę mignie nadawaniem;"
        " w SSB praktycznie nic nie wychodzi, w CW, AM albo FM — nośna"
    ),
    "C. Plugging in (the program is stopped now)": "C. Podłączanie USB (program jest teraz zatrzymany)",
    "Pull the DE-19's USB cable out of the computer and put it back, watching the radio."
    "\nDid the radio stay in receive?": (
        "Wyjmij kabel USB DE-19 z komputera i włóż go z powrotem, patrząc na radio."
        "\nCzy radio zostało w odbiorze?"
    ),
    "the interface itself may make the radio transmit when it is plugged in;"
    " no program can stop that": (
        "sam interfejs może włączyć nadawanie przy podłączaniu; żaden program temu nie zapobiegnie"
    ),
    "Now the same with the other USB devices (the waterfall card, a modem)."
    "\nDid the radio stay in receive?": (
        "Teraz to samo z pozostałymi urządzeniami USB (karta wodospadu, modem)."
        "\nCzy radio zostało w odbiorze?"
    ),
    "the same, for the other devices": "to samo, dla pozostałych urządzeń",
    "D. The program starts (transmitting still off)": "D. Start programu (nadawanie nadal wyłączone)",
    "Starting the program. Watch the radio: it may blink to transmit for a split second;"
    "\nthat is normal.": (
        "Uruchamiam program. Patrz na radio: może na ułamek sekundy mignąć nadawaniem;"
        "\nto normalne."
    ),
    "Did the radio blink at most for an instant and go back to receive?": (
        "Czy radio najwyżej krótko mignęło i wróciło do odbioru?"
    ),
    "a longer transmission at start means the interface's lines key the radio:"
    " stop and look at the interface": (
        "dłuższe nadawanie przy starcie znaczy, że interfejs włącza nadawanie:"
        " przerwij i sprawdź interfejs"
    ),
    "The program is connected to the radio": "Program połączył się z radiem",
    "The program cannot use the radio's port ({state}).": (
        "Program nie może użyć portu radia ({state})."
    ),
    "It is a Xiegu G90": "To jest Xiegu G90",
    "The radio did not say it is a G90 ({detail}).": "Radio nie potwierdziło, że to G90 ({detail}).",
    "the radio does not answer": "radio nie odpowiada",
    "Does the radio show {mhz} MHz, {mode}? (Compare with its display.)": (
        "Czy radio pokazuje {mhz} MHz, {mode}? (Porównaj z wyświetlaczem.)"
    ),
    "the program reads something else than the radio shows": (
        "program odczytuje co innego, niż pokazuje radio"
    ),
    "The computer's clock is not set from the internet yet. FT8/FT4 will not transmit"
    "\nuntil it is; everything else works.": (
        "Zegar komputera nie jest jeszcze ustawiony z internetu. FT8/FT4 nie będzie nadawać,"
        "\ndopóki nie będzie; reszta działa."
    ),
    "E. Test transmissions": "E. Próbne nadawanie",
    "The radio will now transmit twice, briefly, to check that the program switches it"
    "\non and off. What is the radio connected to now?": (
        "Teraz radio dwa razy krótko nada, żeby sprawdzić, czy program je włącza i wyłącza."
        "\nDo czego jest teraz podłączone radio?"
    ),
    "a dummy load": "sztuczne obciążenie",
    "the antenna (set the radio to 1 W, USB, e.g. 14.074 MHz; otherwise the program refuses)": (
        "antena (ustaw w radiu 1 W, USB, np. 14,074 MHz; inaczej program odmówi)"
    ),
    "neither: leave the test transmissions out": "żadne: pomiń próbne nadawanie",
    "not tested": "nie sprawdzono",
    "Restarting the program (an instant's blink, as before)…": (
        "Uruchamiam program ponownie (krótkie mignięcie, jak wcześniej)…"
    ),
    "Transmitting for {seconds} seconds…": "Nadaję przez {seconds} sekundy…",
    "The radio did not transmit. The program says:": "Radio nie nadało. Program podaje:",
    "Put it right and press Enter to try again, or type SKIP: ": (
        "Popraw to i naciśnij Enter, żeby spróbować ponownie, albo wpisz POMIŃ: "
    ),
    "the radio is set to {watts} W: set 1 W (the POW button)": (
        "radio ma ustawione {watts} W: ustaw 1 W (przycisk POW)"
    ),
    "the radio is in {mode}: switch it to USB": "radio jest w trybie {mode}: przełącz na USB",
    "{mhz} MHz is not in a band every country has: tune e.g. 14.074 MHz": (
        "{mhz} MHz nie leży w paśmie wspólnym dla wszystkich krajów: nastaw np. 14,074 MHz"
    ),
    "Set it and press Enter, or type SKIP: ": "Ustaw to i naciśnij Enter, albo wpisz POMIŃ: ",
    "Did the radio transmit, then go back to receive within 2 s of the stop?": (
        "Czy radio nadawało i wróciło do odbioru najpóźniej 2 sekundy po wyłączeniu programu?"
    ),
    "Did the radio transmit for about 2 seconds and go back to receive by itself?": (
        "Czy radio nadawało ok. 2 sekundy i samo wróciło do odbioru?"
    ),
    "the program may not be able to switch the radio to transmit or back": (
        "program może nie umieć włączyć albo wyłączyć nadawania"
    ),
    "Now a transmission that is cut off: I switch the program off after 2 seconds…": (
        "Teraz nadawanie przerwane w połowie: po 2 sekundach wyłączę program…"
    ),
    "a transmission might not end when the browser or the program goes away": (
        "nadawanie mogłoby się nie skończyć po zamknięciu przeglądarki albo programu"
    ),
    "The test transmissions are done: you can set your usual power again.": (
        "Próbne nadawanie zakończone: możesz już ustawić w radiu zwykłą moc."
    ),
    "F. Transmitting from the browser": "F. Nadawanie z przeglądarki",
    "The highest power the page may set, in watts, 1 to {highest}"
    " (0: the page does not change the power):": (
        "Największa moc, jaką może ustawić strona, w watach, od 1 do {highest}"
        " (0: strona nie zmienia mocy):"
    ),
    "May the page start the antenna tuner? (Tuning transmits a carrier, usually for 1-1.5 s,"
    " longer with a poorly matched antenna.)": (
        "Czy strona może uruchamiać strojenie tunera? (Strojenie nadaje nośną, zwykle przez"
        " 1-1,5 s, dłużej przy źle dopasowanej antenie.)"
    ),
    "Switch transmitting from the browser on?": "Włączyć nadawanie z przeglądarki?",
    "Transmitting from the browser: on": "Nadawanie z przeglądarki: włączone",
    "Transmitting from the browser: off": "Nadawanie z przeglądarki: wyłączone",
    "Transmitting from the browser: off (the setup can switch it on later)": (
        "Nadawanie z przeglądarki: wyłączone (konfiguracja może je włączyć później)"
    ),
    "you answered no: {item}": "odpowiedź „nie”: {item}",
    "Type SKIP to go on anyway, or press Enter to stop: ": (
        "Wpisz POMIŃ, żeby iść dalej mimo to, albo naciśnij Enter, żeby przerwać: "
    ),
    "Left out: {item}. Risk: {risk}.": "Pominięte: {item}. Ryzyko: {risk}.",
    "Left out: {items}": "Pominięte: {items}",
    "The page will remind you of it until the whole check is done\n(sudo remoteg90ctl setup).": (
        "Strona będzie o tym przypominać, dopóki nie przejdziesz całego sprawdzenia"
        "\n(sudo remoteg90ctl setup)."
    ),
    "The radio is checked.": "Radio sprawdzone.",
    "One thing for later: the next time the computer starts, watch the radio:"
    "\nit must not start transmitting.": (
        "Jedna rzecz na później: przy najbliższym uruchomieniu komputera patrz na radio:"
        "\nnie może zacząć nadawać."
    ),
    "the program did not start (journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -n 50)": (
        "program się nie uruchomił (journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -n 50)"
    ),
    # Item names (the record keeps the English ones)
    "sleep": "uśpienie komputera",
    "serial port": "port radia",
    "cat socket": "gniazdo kabla",
    "vox": "VOX",
    "swr threshold": "próg SWR",
    "test setup": "obciążenie i moc",
    "plugging the de-19": "podłączanie DE-19",
    "plugging other usb": "podłączanie innych USB",
    "guard start": "start programu",
    "port": "port radia",
    "identity": "model radia",
    "cat": "częstotliwość",
    "ptt test": "próbne nadawanie",
    "interrupted transmission": "przerwane nadawanie",
    # Step 5
    "Access from your phone and other computers": "Dostęp z telefonu i innych komputerów",
    "On this computer the page is at http://127.0.0.1:{port}": (
        "Na tym komputerze strona jest pod adresem http://127.0.0.1:{port}"
    ),
    "Tailscale is not installed: the page works on this computer only.": (
        "Tailscale nie jest zainstalowany: strona działa tylko na tym komputerze."
    ),
    "From a phone or another computer you reach it through Tailscale: a free, private"
    "\nnetwork between your own devices, encrypted.": (
        "Z telefonu albo innego komputera wejdziesz przez Tailscale: bezpłatną, prywatną"
        "\nsieć między Twoimi urządzeniami, szyfrowaną."
    ),
    "Set up access through Tailscale?": "Ustawić dostęp przez Tailscale?",
    "Log this computer in to Tailscale: open the link below and sign in.": (
        "Zaloguj ten komputer do Tailscale: otwórz link poniżej i zaloguj się."
    ),
    "This computer is not logged in to Tailscale.": "Ten komputer nie jest zalogowany do Tailscale.",
    "Later: sudo tailscale up, then start the setup again.": (
        "Później: sudo tailscale up, potem uruchom konfigurację ponownie."
    ),
    "Switching on HTTPS (Tailscale may ask you to allow it first)…": (
        "Włączam HTTPS (Tailscale może najpierw poprosić o zgodę)…"
    ),
    "HTTPS is not on. Later: sudo tailscale serve --bg {port}": (
        "HTTPS nie jest włączone. Później: sudo tailscale serve --bg {port}"
    ),
    "From your devices: https://{name}/": "Z Twoich urządzeń: https://{name}/",
    "Tailscale Funnel is ON: it offers this computer's HTTPS to the whole internet.\nThe page refuses those requests, but switch Funnel off (tailscale funnel status shows it).": (
        "Tailscale Funnel jest WŁĄCZONY: udostępnia HTTPS tego komputera całemu internetowi.\nStrona odrzuca takie żądania, ale wyłącz Funnel (pokazuje go tailscale funnel status)."
    ),
    "On a phone: install the Tailscale app and sign in with the same account.": (
        "Na telefonie: zainstaluj aplikację Tailscale i zaloguj się na to samo konto."
    ),
    # Step 6
    "Starting the program": "Uruchomienie",
    "Start the program now and every time the computer starts?"
    " (The radio may blink to transmit for an instant.)": (
        "Uruchomić program teraz i przy każdym starcie komputera?"
        " (Radio może na chwilę mignąć nadawaniem.)"
    ),
    "The program is running": "Program działa",
    "Not started. Later: sudo ~/remoteG90ctl/deploy/services.sh --start": (
        "Nie uruchomiono. Później: sudo ~/remoteG90ctl/deploy/services.sh --start"
    ),
    "Ready. Open the page in a browser:": "Gotowe. Otwórz stronę w przeglądarce:",
    "from your devices: {page}": "z Twoich urządzeń: {page}",
    "on this computer: {page}": "na tym komputerze: {page}",
    "The first time, create the station's account there (callsign and locator).": (
        "Przy pierwszym wejściu załóż tam konto stacji (znak i lokator)."
    ),
}
