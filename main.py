import os
import re
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path


TZ = ZoneInfo("Europe/Paris")

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

# Sources FF XML (fallback automatique)
FF_XML_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.xml",
]

STATE_FILE = Path.cwd() / "state.json"

# Paramètres
ALLOWED_CURRENCIES = {"USD", "EUR", "GBP"}
WATCHED_ASSETS = {"EURUSD", "GBPUSD", "XAUUSD", "DE30"}

REMINDER_LEAD_MIN = 15
SOURCE_FAIL_ALERT_AFTER = 3  # nb d'échecs consécutifs avant alerte Telegram

CRITICAL_KEYWORDS = ["speaks", "speech", "press conference", "testifies", "hearing"]

FLAGS = {
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
    "GBP": "🇬🇧",
}

MACRO_EXPLAIN = {
    "CPI": "Indice des prix à la consommation. Mesure l'inflation.",
    "Core CPI": "Inflation hors alimentation et énergie.",
    "PPI": "Indice des prix à la production.",
    "GDP": "Produit intérieur brut, mesure la croissance économique.",
    "Retail Sales": "Mesure les ventes au détail, indicateur clé de la consommation.",
    "Non-Farm Payrolls": "Variation mensuelle de l'emploi aux États-Unis.",
    "Unemployment": "Taux de chômage.",
    "Interest Rate": "Décision de politique monétaire.",
    "FOMC": "Décision de politique monétaire de la Réserve fédérale.",
}

MACRO_DIRECTION = {
    # inflation
    "CPI": -1,
    "Core CPI": -1,
    "PPI": -1,
    "Core PPI": -1,
    "PCE": -1,
    "Core PCE": -1,
    # croissance
    "GDP": 1,
    "Retail Sales": 1,
    # emploi
    "Non-Farm": 1,
    "Unemployment": -1,
    "Jobless": -1,
    # sentiment
    "Consumer Sentiment": 1,
}

EXACT_TRANSLATIONS = {
    "fomc statement": "Communiqué du FOMC",
    "fomc economic projections": "Projections économiques du FOMC",
    "federal funds rate": "Décision de taux de la Fed",
    "interest rate decision": "Décision de taux",
    "main refinancing rate": "Taux de refinancement principal",
    "monetary policy statement": "Communiqué de politique monétaire",
    "non-farm payrolls": "Créations d’emplois non agricoles (NFP)",
    "adp non-farm employment change": "Emplois privés ADP",
    "unemployment rate": "Taux de chômage",
    "claimant count change": "Variation des demandeurs d’emploi",
    "average hourly earnings m/m": "Salaire horaire moyen (mensuel)",
    "average earnings index 3m/y": "Salaire moyen sur 3 mois (annuel)",
    "retail sales m/m": "Ventes au détail (mensuelles)",
    "core retail sales m/m": "Ventes au détail hors éléments volatils (mensuelles)",
    "gdp q/q": "PIB trimestriel",
    "gdp m/m": "PIB mensuel",
    "final gdp q/q": "PIB final trimestriel",
    "prelim gdp q/q": "PIB préliminaire trimestriel",
    "flash gdp q/q": "PIB flash trimestriel",
    "trade balance": "Balance commerciale",
    "current account": "Balance des paiements courants",
    "cpi m/m": "Inflation CPI (mensuelle)",
    "cpi y/y": "Inflation CPI (annuelle)",
    "core cpi m/m": "Inflation sous-jacente CPI (mensuelle)",
    "core cpi y/y": "Inflation sous-jacente CPI (annuelle)",
    "trimmed mean cpi y/y": "Inflation CPI moyenne tronquée (annuelle)",
    "ppi m/m": "Inflation à la production PPI (mensuelle)",
    "ppi y/y": "Inflation à la production PPI (annuelle)",
    "core ppi m/m": "Inflation sous-jacente PPI (mensuelle)",
    "core ppi y/y": "Inflation sous-jacente PPI (annuelle)",
    "pce price index m/m": "Indice PCE (mensuel)",
    "pce price index y/y": "Indice PCE (annuel)",
    "core pce price index m/m": "Indice PCE sous-jacent (mensuel)",
    "core pce price index y/y": "Indice PCE sous-jacent (annuel)",
    "ism manufacturing pmi": "ISM manufacturier",
    "ism services pmi": "ISM services",
    "manufacturing pmi": "PMI manufacturier",
    "services pmi": "PMI services",
    "flash manufacturing pmi": "PMI manufacturier flash",
    "flash services pmi": "PMI services flash",
    "consumer confidence": "Confiance des consommateurs",
    "cb consumer confidence": "Confiance des consommateurs Conference Board",
    "final consumer sentiment": "Sentiment final des consommateurs",
    "prelim uom consumer sentiment": "Sentiment préliminaire des consommateurs (Université du Michigan)",
    "building permits": "Permis de construire",
    "housing starts": "Mises en chantier",
    "existing home sales": "Ventes de logements existants",
    "new home sales": "Ventes de logements neufs",
    "pending home sales m/m": "Promesses de ventes immobilières (mensuelles)",
    "durable goods orders m/m": "Commandes de biens durables (mensuelles)",
    "core durable goods orders m/m": "Commandes de biens durables hors transport (mensuelles)",
    "crude oil inventories": "Stocks hebdomadaires de pétrole brut",
    "10-y bond auction": "Adjudication d’obligations à 10 ans",
    "30-y bond auction": "Adjudication d’obligations à 30 ans",
}


def default_state() -> dict:
    return {
        "sent_reminders": {},
        "sent_daily": {},
        "seen_events": [],
        "sent_releases": {},
        "source_failures": 0,
        "last_source_alert": None,
    }


def ensure_state(state: dict) -> dict:
    base = default_state()
    if not isinstance(state, dict):
        return base

    for k, v in base.items():
        state.setdefault(k, v)

    if not isinstance(state["sent_reminders"], dict):
        state["sent_reminders"] = {}
    if not isinstance(state["sent_daily"], dict):
        state["sent_daily"] = {}
    if not isinstance(state["seen_events"], list):
        state["seen_events"] = []
    if not isinstance(state["sent_releases"], dict):
        state["sent_releases"] = {}
    if not isinstance(state["source_failures"], int):
        state["source_failures"] = 0
    if state["last_source_alert"] is not None and not isinstance(state["last_source_alert"], str):
        state["last_source_alert"] = None

    return state


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()

    try:
        content = STATE_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return default_state()
        return ensure_state(json.loads(content))
    except Exception:
        return default_state()


def save_state(state: dict) -> None:
    state = ensure_state(state)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    r.raise_for_status()


def normalize_event_title(title: str) -> str:
    s = (title or "").strip().lower()
    s = s.replace("&amp;", "&")
    s = s.replace("'", "")
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def is_critical_event(title: str) -> bool:
    t = normalize_event_title(title)
    return any(k in t for k in CRITICAL_KEYWORDS)


def is_allowed_event(ev: dict) -> bool:
    impact = (ev.get("impact") or "").strip()
    currency = (ev.get("country") or "").strip().upper()
    title = normalize_event_title(ev.get("title", ""))

    if impact == "High":
        return True

    # Medium USD toujours autorisé
    if impact == "Medium" and currency == "USD":
        return True

    # Medium EUR / GBP : on garde PMI
    if impact == "Medium" and currency in {"EUR", "GBP"} and "pmi" in title:
        return True

    return False


def smart_translate_event(title: str) -> str:
    key = normalize_event_title(title)

    if key in EXACT_TRANSLATIONS:
        return EXACT_TRANSLATIONS[key]

    if "fomc" in key and "statement" in key:
        return "Communiqué du FOMC"
    if "fomc" in key and "projection" in key:
        return "Projections économiques du FOMC"
    if "interest rate" in key or "rate decision" in key:
        return "Décision de taux"
    if "non farm payroll" in key or key == "nfp":
        return "Créations d’emplois non agricoles (NFP)"
    if "unemployment" in key:
        return "Taux de chômage"
    if "retail sales" in key:
        return "Ventes au détail hors éléments volatils" if "core" in key else "Ventes au détail"
    if "gdp" in key:
        if "flash" in key:
            return "PIB flash"
        if "prelim" in key:
            return "PIB préliminaire"
        if "final" in key:
            return "PIB final"
        return "PIB"
    if "consumer confidence" in key:
        return "Confiance des consommateurs"
    if "consumer sentiment" in key:
        return "Sentiment des consommateurs"
    if "pmi" in key:
        if "manufacturing" in key:
            return "PMI manufacturier"
        if "services" in key or "service" in key:
            return "PMI services"
        return "PMI"
    if "cpi" in key:
        if "core" in key:
            if "y/y" in key:
                return "Inflation sous-jacente CPI (annuelle)"
            if "m/m" in key:
                return "Inflation sous-jacente CPI (mensuelle)"
            return "Inflation sous-jacente CPI"
        if "y/y" in key:
            return "Inflation CPI (annuelle)"
        if "m/m" in key:
            return "Inflation CPI (mensuelle)"
        return "Inflation CPI"
    if "ppi" in key:
        if "core" in key:
            if "y/y" in key:
                return "Inflation sous-jacente PPI (annuelle)"
            if "m/m" in key:
                return "Inflation sous-jacente PPI (mensuelle)"
            return "Inflation sous-jacente PPI"
        if "y/y" in key:
            return "Inflation PPI (annuelle)"
        if "m/m" in key:
            return "Inflation PPI (mensuelle)"
        return "Inflation PPI"
    if "pce" in key:
        if "core" in key:
            if "y/y" in key:
                return "Indice PCE sous-jacent (annuel)"
            if "m/m" in key:
                return "Indice PCE sous-jacent (mensuel)"
            return "Indice PCE sous-jacent"
        if "y/y" in key:
            return "Indice PCE (annuel)"
        if "m/m" in key:
            return "Indice PCE (mensuel)"
        return "Indice PCE"
    if "home sales" in key:
        if "pending" in key:
            return "Promesses de ventes immobilières"
        if "existing" in key:
            return "Ventes de logements existants"
        if "new" in key:
            return "Ventes de logements neufs"
        return "Ventes immobilières"
    if "durable goods" in key:
        return "Commandes de biens durables hors transport" if "core" in key else "Commandes de biens durables"
    if "crude oil inventories" in key:
        return "Stocks hebdomadaires de pétrole brut"
    if "building permits" in key:
        return "Permis de construire"
    if "housing starts" in key:
        return "Mises en chantier"
    if "trade balance" in key:
        return "Balance commerciale"
    if "current account" in key:
        return "Balance des paiements courants"

    return (title or "").strip()


def event_priority_icon(title: str, impact: str) -> str:
    t = normalize_event_title(title)

    if (
        "fomc" in t
        or "federal funds rate" in t
        or "interest rate" in t
        or "main refinancing rate" in t
        or "press conference" in t
        or "speech" in t
        or "speaks" in t
        or "testifies" in t
        or "hearing" in t
    ):
        return "🔥"

    if (
        "cpi" in t
        or "ppi" in t
        or "pce" in t
        or "non farm payroll" in t
        or t == "nfp"
        or "unemployment" in t
        or "gdp" in t
        or "retail sales" in t
    ):
        return "🚨"

    if (impact or "").upper() == "HIGH":
        return "⚠️"

    return "📌"


def event_sort_priority(title: str, impact: str) -> int:
    t = normalize_event_title(title)

    if (
        "fomc" in t
        or "federal funds rate" in t
        or "interest rate" in t
        or "main refinancing rate" in t
        or "press conference" in t
        or "speech" in t
        or "speaks" in t
        or "testifies" in t
        or "hearing" in t
    ):
        return 0

    if (
        "cpi" in t
        or "ppi" in t
        or "pce" in t
        or "non farm payroll" in t
        or t == "nfp"
        or "unemployment" in t
        or "gdp" in t
        or "retail sales" in t
    ):
        return 1

    if (impact or "").upper() == "HIGH":
        return 2

    return 3


def fetch_ff_xml() -> str:
    last_err = None
    headers = {"User-Agent": "macro-alerts-telegram/1.0"}

    for url in FF_XML_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=25)
            r.raise_for_status()
            if "<weeklyevents" in r.text or "<event" in r.text:
                return r.text
            last_err = RuntimeError(f"Unexpected content from {url}")
        except Exception as e:
            last_err = e

    raise RuntimeError(f"All FF XML URLs failed. Last error: {last_err}")


def parse_ff_datetime(date_str: str, time_str: str):
    if not date_str or not time_str:
        return None

    date_str = date_str.strip()
    time_str = time_str.strip()

    if time_str.lower() in ("all day", "day", "tentative"):
        return None

    try:
        d = datetime.strptime(date_str, "%m-%d-%Y").date()
    except ValueError:
        return None

    try:
        t = datetime.strptime(time_str.lower(), "%I:%M%p").time()
    except ValueError:
        return None

    dt_utc = datetime.combine(d, t).replace(tzinfo=ZoneInfo("UTC"))
    return dt_utc.astimezone(TZ)


def fetch_events() -> list[tuple[datetime, dict]]:
    xml_text = fetch_ff_xml()
    root = ET.fromstring(xml_text)

    events = []

    for ev in root.findall(".//event"):
        title = (ev.findtext("title") or "").strip()
        country = (ev.findtext("country") or "").strip().upper()
        impact = (ev.findtext("impact") or "").strip()
        date_s = (ev.findtext("date") or "").strip()
        time_s = (ev.findtext("time") or "").strip()
        forecast = (ev.findtext("forecast") or "").strip()
        previous = (ev.findtext("previous") or "").strip()
        actual = (ev.findtext("actual") or "").strip()

        if country not in ALLOWED_CURRENCIES:
            continue

        event_data = {
            "title": title,
            "country": country,
            "impact": impact,
            "forecast": forecast,
            "previous": previous,
            "actual": actual,
        }

        if not is_allowed_event(event_data):
            continue

        dt = parse_ff_datetime(date_s, time_s)
        if dt is None:
            continue

        events.append((dt, event_data))

    events.sort(key=lambda x: x[0])
    return events


def flag_for_currency(cur: str) -> str:
    return FLAGS.get(cur, "🏳️")


def impacted_assets(currency: str) -> list[str]:
    c = (currency or "").upper()

    if c == "USD":
        return ["EURUSD", "GBPUSD", "XAUUSD"]
    if c == "EUR":
        return ["EURUSD", "DE30"]
    if c == "GBP":
        return ["GBPUSD"]

    return []


def relevant_assets_for_event(ev: dict) -> list[str]:
    assets = impacted_assets(ev["country"])
    return [a for a in assets if a in WATCHED_ASSETS]


def is_relevant_event(ev: dict) -> bool:
    return len(relevant_assets_for_event(ev)) > 0


def event_key(dt: datetime, ev: dict) -> str:
    title = normalize_event_title(ev["title"])
    return f"{dt.isoformat()}::{ev['country']}::{title}"


def format_macro_alert(dt_local: datetime, ev: dict, minutes_left: int) -> str:
    cur = ev["country"]
    impact = ev["impact"]
    title = ev["title"]

    title_fr = smart_translate_event(title)
    icon = event_priority_icon(title, impact)

    forecast = ev.get("forecast") or ""
    previous = ev.get("previous") or ""

    values = ""
    if forecast:
        values += f"\n📊 Prévision : {forecast}"
    if previous:
        values += f"\n📊 Précédent : {previous}"

    assets = relevant_assets_for_event(ev)
    assets_block = "\n".join(f"• {a}" for a in assets) if assets else "• (aucun)"

    return (
        f"{icon} ALERTE MACRO\n\n"
        f"⏰ Dans {minutes_left} min — {dt_local.strftime('%H:%M')} (Paris)\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title_fr}\n"
        f"({title})"
        f"{values}\n\n"
        f"Actifs concernés\n{assets_block}"
    )


def parse_ff_number(value):
    if not value:
        return None

    try:
        v = str(value).strip().replace(" ", "")
        v = v.replace(",", "")

        multiplier = 1

        if v.endswith("%"):
            v = v[:-1]
        if v.endswith("K"):
            multiplier = 1_000
            v = v[:-1]
        elif v.endswith("M"):
            multiplier = 1_000_000
            v = v[:-1]
        elif v.endswith("B"):
            multiplier = 1_000_000_000
            v = v[:-1]

        if v in {"", "-", "—", "N/A", "n/a"}:
            return None

        return float(v) * multiplier
    except Exception:
        return None


def compute_surprise(actual, forecast):
    a = parse_ff_number(actual)
    f = parse_ff_number(forecast)

    if a is None or f is None or f == 0:
        return None

    try:
        return (a - f) / abs(f)
    except Exception:
        return None


def format_release_alert(dt_local: datetime, ev: dict) -> str:
    cur = ev["country"]
    title = ev["title"]
    title_fr = smart_translate_event(title)

    actual = ev.get("actual") or "-"
    forecast = ev.get("forecast") or "-"
    previous = ev.get("previous") or "-"

    surprise = compute_surprise(actual, forecast)

    if surprise is not None and abs(surprise) < 0.02:
        surprise = 0

    surprise_text = ""
    impact_text = "➖ Impact macro : neutre"

    if surprise is not None:
        surprise_text = f"\n⚡ Surprise : {surprise * 100:+.2f}%"

        direction = 1
        for key in MACRO_DIRECTION:
            if key.lower() in title.lower():
                direction = MACRO_DIRECTION[key]
                break

        macro_effect = surprise * direction

        if macro_effect > 0:
            impact_text = f"📈 Impact macro : {cur} bullish"
        elif macro_effect < 0:
            impact_text = f"📉 Impact macro : {cur} bearish"

    return (
        "🚨 DONNÉE MACRO PUBLIÉE\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title_fr}\n"
        f"({title})\n\n"
        f"Réel : {actual}\n"
        f"Prévision : {forecast}\n"
        f"Précédent : {previous}"
        f"{surprise_text}\n\n"
        f"{impact_text}\n\n"
        f"🕒 {dt_local.strftime('%H:%M')} (Paris)"
    )


def format_daily_summary(day, events: list[tuple[datetime, dict]]) -> str:
    header = f"🗓️ Macro de demain — {day.strftime('%d/%m/%Y')}\n\n"

    day_events = [(dt, ev) for dt, ev in events if dt.date() == day]

    if not day_events:
        return header + "Aucune annonce pertinente."

    day_events.sort(key=lambda x: (x[0], event_sort_priority(x[1]["title"], x[1]["impact"])))

    lines = []
    for dt_local, ev in day_events:
        cur = ev["country"]
        impact = ev["impact"]
        title = ev["title"]

        title_fr = smart_translate_event(title)
        icon = event_priority_icon(title, impact)

        assets = relevant_assets_for_event(ev)
        assets_str = ", ".join(assets) if assets else "-"

        lines.append(
            f"{icon} {dt_local.strftime('%H:%M')} {cur} "
            f"{title_fr} ({title}) ({assets_str})"
        )

    legend = "Légende : 🔥 priorité max | 🚨 très important | ⚠️ impact élevé | 📌 secondaire\n\n"
    return header + legend + "\n".join(lines)


def format_new_event_alert(dt_local: datetime, ev: dict) -> str:
    impact_label = "🚨 HIGH IMPACT" if ev["impact"] == "High" else "⚠️ NOUVEL ÉVÉNEMENT"
    title_fr = smart_translate_event(ev["title"])

    msg = (
        "🆕 ANNONCE AJOUTÉE EN COURS DE JOURNÉE\n\n"
        f"{impact_label}\n\n"
        f"{flag_for_currency(ev['country'])} {ev['country']}\n"
        f"{title_fr}\n"
        f"({ev['title']})\n\n"
        f"📅 {dt_local.strftime('%d/%m')}\n"
        f"🕒 {dt_local.strftime('%H:%M')} (Paris)"
    )

    if is_critical_event(ev["title"]):
        msg += "\n\n🔥 Événement potentiellement très volatil."

    return msg


def should_send_new_event_alert(now: datetime, dt: datetime, ev: dict) -> bool:
    if dt < now - timedelta(minutes=10):
        return False
    if dt > now + timedelta(hours=12):
        return False
    if ev["impact"] == "High":
        return True
    if is_critical_event(ev["title"]):
        return True
    return False


def main():
    state = load_state()
    state = ensure_state(state)

    print("CWD:", os.getcwd())
    print("STATE PATH:", STATE_FILE.resolve())

    now = datetime.now(TZ)

    # 1) Récupération des events
    try:
        events = fetch_events()

        seen = set(state.get("seen_events", []))

        for dt, ev in events:
            key = event_key(dt, ev)

            if key not in seen:
                print(
                    "NEW EVENT CHECK |",
                    dt.strftime("%Y-%m-%d %H:%M"),
                    "|",
                    ev["country"],
                    "|",
                    ev["impact"],
                    "|",
                    ev["title"],
                    "| key_seen =",
                    key in seen,
                )

                if should_send_new_event_alert(now, dt, ev):
                    tg_send(format_new_event_alert(dt, ev))

                seen.add(key)

        state["seen_events"] = list(seen)[-300:]
        state["source_failures"] = 0

    except Exception as e:
        state["source_failures"] = int(state.get("source_failures", 0)) + 1

        if state["source_failures"] >= SOURCE_FAIL_ALERT_AFTER:
            last = state.get("last_source_alert")
            allow = True

            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=TZ)
                    if (now - last_dt) < timedelta(hours=6):
                        allow = False
                except Exception:
                    pass

            if allow:
                tg_send(
                    "⚠️ Calendrier macro indisponible.\n"
                    "Les URLs ForexFactory XML semblent avoir changé ou être bloquées.\n"
                    f"Détail: {type(e).__name__}: {e}"
                )
                state["last_source_alert"] = now.isoformat()

        save_state(state)
        return

    # 2) Résumé à 22:00 Paris pour le lendemain
    tomorrow = (now + timedelta(days=1)).date()
    tomorrow_key = tomorrow.isoformat()

    if tomorrow_key not in state["sent_daily"] and now.hour == 22 and now.minute <= 15:
        tomorrow_events = [(dt, ev) for dt, ev in events if dt.date() == tomorrow]

        print("SUMMARY CHECK | tomorrow =", tomorrow.isoformat())
        for dt, ev in tomorrow_events:
            print(
                "SUMMARY EVENT |",
                dt.strftime("%Y-%m-%d %H:%M"),
                "|",
                ev["country"],
                "|",
                ev["impact"],
                "|",
                ev["title"],
            )

        tg_send(format_daily_summary(tomorrow, tomorrow_events))
        state["sent_daily"][tomorrow_key] = now.isoformat()

    # 3) Rappels T-15 + releases
    for dt, ev in events:
        key = event_key(dt, ev)

        # ----- REMINDER -----
        if is_relevant_event(ev) or ev["impact"] == "High":
            reminder_time = dt - timedelta(minutes=REMINDER_LEAD_MIN)

            if reminder_time <= now < dt:
                if key not in state["sent_reminders"]:
                    minutes_left = max(0, int((dt - now).total_seconds() / 60))
                    tg_send(format_macro_alert(dt, ev, minutes_left))
                    state["sent_reminders"][key] = now.isoformat()

        # ----- RELEASE -----
        if now < dt:
            continue

        if (now - dt).total_seconds() > 3600:
            continue

        if key in state["sent_releases"]:
            continue

        actual = (ev.get("actual") or "").strip()

        print(
            "RELEASE CHECK |",
            dt.strftime("%H:%M"),
            "|",
            ev["country"],
            "|",
            ev["title"],
            "| actual =", repr(actual),
            "| forecast =", repr(ev.get("forecast")),
            "| previous =", repr(ev.get("previous")),
        )

        if actual and actual not in {"-", "—"}:
            tg_send(format_release_alert(dt, ev))
            state["sent_releases"][key] = now.isoformat()
            save_state(state)

    save_state(state)


if __name__ == "__main__":
    main()
