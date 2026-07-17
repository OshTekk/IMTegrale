from __future__ import annotations

import html as html_lib
import logging
import math
import re
import time
import unicodedata
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, SoupStrainer

logger = logging.getLogger(__name__)

CAS_LOGIN_URL = "https://cas.imt-atlantique.fr/cas/login"
PASS_LOGIN_URL = "https://pass.imt-atlantique.fr/OpDotNet/Noyau/Login.aspx"
PASS_REPORT_URL = "https://pass.imt-atlantique.fr/OpDotNet/Commun/Assistant/Reporting/MSReportViewer.aspx"
PASS_PROFILE_URL = (
    "https://pass.imt-atlantique.fr/opdotnet/eplug/annuaire/accueil.aspx"
    "?IdApplication=142&TypeAcces=MaFiche&IdLien=190"
)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) IMTegrale/3.2"

Origin = tuple[str, str, int]
PASS_ORIGIN: Origin = ("https", "pass.imt-atlantique.fr", 443)
CAS_ORIGIN: Origin = ("https", "cas.imt-atlantique.fr", 443)
IDP_ORIGIN: Origin = ("https", "idp.imt-atlantique.fr", 443)
TRUSTED_IMT_ORIGINS = frozenset({PASS_ORIGIN, CAS_ORIGIN, IDP_ORIGIN})
CREDENTIAL_ORIGINS = frozenset({CAS_ORIGIN, IDP_ORIGIN})

MAX_URL_LENGTH = 4096
MAX_HTML_BYTES = 2_000_000
MAX_EXPORT_BYTES = 10_000_000
MAX_OPERATION_BYTES = 24_000_000
MAX_REQUESTS_PER_OPERATION = 24
MAX_REDIRECTS = 8
MAX_PASS_ROWS = 10_000
MAX_PASS_ENTRIES = 2_000
MAX_UE_CODE_LENGTH = 32
MAX_NOTE_LABEL_LENGTH = 240
MAX_NOTE_COEFFICIENT = 100.0
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class ImtError(RuntimeError):
    pass


class ImtAuthenticationError(ImtError):
    pass


class ImtFetchError(ImtError):
    pass


class ImtNetworkError(ImtFetchError):
    pass


class ImtUpstreamError(ImtFetchError):
    def __init__(self, status_code: int, retry_after_seconds: int | None = None) -> None:
        super().__init__("Le service IMT est temporairement indisponible")
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class PassEntry:
    ue_code: str
    label: str
    score: float
    coefficient: float
    is_resit: bool


@dataclass(frozen=True, slots=True)
class PassProfile:
    campus: str | None = None
    program: str | None = None
    promotion_year: int | None = None
    first_name: str | None = None
    last_name: str | None = None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value or "")).strip()


def _decimal(value: str) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _is_resit(label: str) -> bool:
    return bool(re.match(r"^\s*(?:RAT\d*|RATTRAP(?:AGE)?)\b", label or "", re.IGNORECASE))


def _url_origin(url: str) -> Origin:
    if not url or len(url) > MAX_URL_LENGTH or any(char in url for char in "\r\n\t"):
        raise ImtFetchError("L'IMT a fourni une URL invalide")
    parsed = urlsplit(url)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ImtFetchError("L'IMT a fourni une URL avec un port invalide") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port != 443
    ):
        raise ImtFetchError("Une destination IMT non sécurisée a été refusée")
    return ("https", hostname.lower(), port)


def validate_imt_url(url: str, allowed_origins: Collection[Origin] = TRUSTED_IMT_ORIGINS) -> str:
    if _url_origin(url) not in allowed_origins:
        raise ImtFetchError("Une destination extérieure aux services IMT autorisés a été refusée")
    return url


def _form_action(base_url: str, raw_action: object, allowed_origins: Collection[Origin]) -> str:
    action = raw_action if isinstance(raw_action, str) else ""
    return validate_imt_url(urljoin(base_url, action), allowed_origins)


def _with_export_format(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "format"
    ]
    query.append(("Format", "HTML4.0"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class ImtPassClient:
    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._operation_depth = 0
        self._deadline = 0.0
        self._operation_bytes = 0
        self._request_count = 0
        self.last_profile: PassProfile | None = None
        self.authenticated = False
        self.include_profile_on_fetch = True

    @property
    def request_count(self) -> int:
        return self._request_count

    @contextmanager
    def _operation(self) -> Iterator[None]:
        root_operation = self._operation_depth == 0
        if root_operation:
            self._deadline = time.monotonic() + max(45, self.timeout_seconds * 3)
            self._operation_bytes = 0
            self._request_count = 0
        self._operation_depth += 1
        try:
            yield
        finally:
            self._operation_depth -= 1
            if root_operation:
                self._deadline = 0.0

    def _request_timeout(self) -> tuple[float, float]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ImtFetchError("Le délai global de communication avec l'IMT est dépassé")
        return max(0.1, min(8.0, remaining)), max(0.1, min(float(self.timeout_seconds), remaining))

    def _ensure_deadline(self) -> None:
        if self._deadline - time.monotonic() <= 0:
            raise ImtFetchError("Le délai global de communication avec l'IMT est dépassé")

    def _read_limited(self, response: requests.Response, max_bytes: int) -> None:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ImtFetchError("La réponse IMT dépasse la taille autorisée")
            except ValueError:
                pass

        content = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                self._ensure_deadline()
                if not chunk:
                    continue
                if len(content) + len(chunk) > max_bytes:
                    raise ImtFetchError("La réponse IMT dépasse la taille autorisée")
                self._operation_bytes += len(chunk)
                if self._operation_bytes > MAX_OPERATION_BYTES:
                    raise ImtFetchError("Le volume total reçu depuis l'IMT dépasse la limite autorisée")
                content.extend(chunk)
            self._ensure_deadline()
        except Exception:
            response.close()
            raise
        response._content = bytes(content)
        response._content_consumed = True
        response.close()

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        allowed_origins: Collection[Origin] = TRUSTED_IMT_ORIGINS,
        max_bytes: int = MAX_HTML_BYTES,
        sensitive: bool = False,
    ) -> requests.Response:
        current_url = validate_imt_url(url, allowed_origins)
        current_method = method.upper()
        current_data = data

        for redirect_count in range(MAX_REDIRECTS + 1):
            self._request_count += 1
            if self._request_count > MAX_REQUESTS_PER_OPERATION:
                raise ImtFetchError("Le parcours d'authentification IMT est anormalement long")
            response = self.session.request(
                current_method,
                current_url,
                data=current_data,
                timeout=self._request_timeout(),
                allow_redirects=False,
                stream=True,
            )
            if response.status_code not in REDIRECT_STATUSES:
                self._read_limited(response, max_bytes)
                return response

            location = response.headers.get("Location")
            if not location:
                response.close()
                raise ImtFetchError("Une redirection IMT incomplète a été refusée")
            if redirect_count >= MAX_REDIRECTS:
                response.close()
                raise ImtFetchError("Le service IMT a envoyé trop de redirections")

            next_url = validate_imt_url(urljoin(current_url, location), allowed_origins)
            if sensitive and current_method != "GET" and response.status_code in {307, 308}:
                response.close()
                raise ImtFetchError("Une redirection susceptible de retransmettre un secret a été refusée")
            response.close()

            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method == "POST"
            ):
                current_method = "GET"
                current_data = None
                sensitive = False
            current_url = next_url

        raise ImtFetchError("Le service IMT a envoyé trop de redirections")

    def _get(
        self,
        url: str,
        *,
        allowed_origins: Collection[Origin] = TRUSTED_IMT_ORIGINS,
        max_bytes: int = MAX_HTML_BYTES,
    ) -> requests.Response:
        return self._request("GET", url, allowed_origins=allowed_origins, max_bytes=max_bytes)

    def _post(
        self,
        url: str,
        *,
        data: Mapping[str, str] | Sequence[tuple[str, str]],
        allowed_origins: Collection[Origin] = TRUSTED_IMT_ORIGINS,
        sensitive: bool = False,
    ) -> requests.Response:
        return self._request(
            "POST",
            url,
            data=data,
            allowed_origins=allowed_origins,
            sensitive=sensitive,
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> int | None:
        value = response.headers.get("Retry-After", "").strip()
        if not value:
            return None
        if value.isdigit():
            return max(0, min(int(value), 86_400))
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0, min(int((parsed - datetime.now(UTC)).total_seconds()), 86_400))
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _ensure_success(cls, response: requests.Response) -> None:
        if response.status_code >= 400:
            raise ImtUpstreamError(response.status_code, cls._retry_after(response))

    def authenticate(self, username: str, password: str) -> None:
        with self._operation():
            self.authenticated = False
            try:
                response = self._get(PASS_LOGIN_URL, allowed_origins={PASS_ORIGIN})
                self._ensure_success(response)

                provider = "SAMLv2ProviderConfiguration"
                match = re.search(
                    r"selectedProvider\s*=\s*JSON\.parse\(\s*'\{[^}]*\"Name\"\s*:\s*\"([^\"]+)\"",
                    response.text,
                )
                if match and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", match.group(1)):
                    provider = match.group(1)

                login_url = f"{PASS_LOGIN_URL}?{urlencode({'auth': provider})}"
                response = self._get(login_url, allowed_origins={PASS_ORIGIN})
                self._ensure_success(response)
                soup = BeautifulSoup(response.text, "html.parser")
                saml_form = None
                saml_url = ""
                for form in soup.find_all("form"):
                    input_names = {item.get("name") for item in form.find_all("input")}
                    try:
                        candidate = _form_action(response.url, form.get("action"), CREDENTIAL_ORIGINS)
                    except ImtFetchError:
                        if input_names & {"SAMLRequest", "SAMLResponse"}:
                            raise
                        continue
                    saml_form = form
                    saml_url = candidate
                    break

                if saml_form is not None:
                    payload = {
                        item.get("name"): item.get("value", "")
                        for item in saml_form.find_all("input")
                        if item.get("name")
                    }
                    response = self._post(saml_url, data=payload, sensitive=True)
                    self._ensure_success(response)
                    self._complete_cas(response, username, password)
                    self.authenticated = True
                    return

                direct_url = re.search(
                    r"https://(?:idp|cas)\.imt-atlantique\.fr(?::\d+)?/[^\s\"'<>]+",
                    response.text,
                )
                if direct_url:
                    target = validate_imt_url(
                        html_lib.unescape(direct_url.group(0)),
                        CREDENTIAL_ORIGINS,
                    )
                    response = self._get(target)
                    self._ensure_success(response)
                    self._complete_cas(response, username, password)
                    self.authenticated = True
                    return
                raise ImtFetchError("Le point d'entrée SSO de PASS est introuvable")
            except ImtError:
                raise
            except requests.RequestException as exc:
                raise ImtNetworkError("Le service IMT ne répond pas pour le moment") from exc

    def _complete_cas(self, response: requests.Response, username: str, password: str) -> None:
        current = response
        soup = BeautifulSoup(current.text, "html.parser")
        login_form = next(
            (
                form
                for form in soup.find_all("form")
                if any((item.get("type") or "").lower() == "password" for item in form.find_all("input"))
            ),
            None,
        )
        if login_form is not None:
            target = _form_action(current.url, login_form.get("action"), CREDENTIAL_ORIGINS)
            payload: list[tuple[str, str]] = []
            for item in login_form.find_all("input"):
                name = item.get("name")
                if not name:
                    continue
                lowered = name.lower()
                item_type = (item.get("type") or "").lower()
                value = item.get("value", "")
                if item_type == "password":
                    value = password
                elif item_type in {"", "email", "text"} and "user" in lowered:
                    value = username
                payload.append((name, value))
            current = self._post(target, data=payload, sensitive=True)
            self._ensure_success(current)
            if self._contains_password_form(current.text):
                raise ImtAuthenticationError("Identifiant ou mot de passe IMT incorrect")

        for _ in range(6):
            soup = BeautifulSoup(current.text, "html.parser")
            saml_form = next(
                (
                    form
                    for form in soup.find_all("form")
                    if (
                        {item.get("name") for item in form.find_all("input")}
                        & {"SAMLResponse", "SAMLRequest"}
                    )
                    or "saml2/post/sso" in (form.get("action") or "").lower()
                ),
                None,
            )
            if saml_form is None:
                break
            target = _form_action(current.url, saml_form.get("action"), TRUSTED_IMT_ORIGINS)
            payload: list[tuple[str, str]] = []
            selected_radios = {
                item.get("name"): item.get("value", "")
                for item in saml_form.find_all("input", type="radio")
                if item.has_attr("checked")
            }
            for item in saml_form.find_all("input"):
                name = item.get("name")
                if not name:
                    continue
                item_type = (item.get("type") or "").lower()
                value = item.get("value", "")
                if item_type == "radio" and selected_radios.get(name) != value:
                    continue
                if item_type == "submit" and "proceed" not in name.lower() and "accept" not in value.lower():
                    continue
                payload.append((name, value))
            current = self._post(target, data=payload, sensitive=True)
            self._ensure_success(current)

        if self._contains_password_form(current.text):
            raise ImtAuthenticationError("Identifiant ou mot de passe IMT incorrect")

    @staticmethod
    def _contains_password_form(content: str) -> bool:
        soup = BeautifulSoup(content, "html.parser")
        return any(
            (item.get("type") or "").lower() == "password"
            for form in soup.find_all("form")
            for item in form.find_all("input")
        )

    def fetch_entries(
        self,
        username: str,
        password: str,
        *,
        include_profile: bool | None = None,
    ) -> list[PassEntry]:
        with self._operation():
            self.authenticate(username, password)
            if include_profile is None:
                include_profile = self.include_profile_on_fetch
            return self.fetch_entries_authenticated(include_profile=include_profile)

    def fetch_entries_authenticated(self, *, include_profile: bool = False) -> list[PassEntry]:
        with self._operation():
            try:
                response = self._get(PASS_REPORT_URL)
                self._ensure_success(response)
                if self._contains_password_form(response.text) or "session perdue" in response.text.lower():
                    self.authenticated = False
                    raise ImtAuthenticationError("La session IMT n'a pas pu être ouverte")

                match = re.search(r'["\']ExportUrlBase["\']\s*:\s*"([^"]+)"', response.text)
                export_path = match.group(1) if match else None
                if not export_path:
                    index = response.text.find("ExportUrlBase")
                    snippet = response.text[index : index + 300] if index >= 0 else ""
                    fallback = re.search(r':\s*"([^"]+)"', snippet)
                    export_path = fallback.group(1) if fallback else None
                if not export_path:
                    raise ImtFetchError("PASS n'a pas fourni l'export des notes")

                raw_export_url = html_lib.unescape(export_path.replace("\\u0026", "&"))
                export_url = validate_imt_url(urljoin(response.url, raw_export_url), {PASS_ORIGIN})
                if not urlsplit(export_url).path.lower().startswith("/opdotnet/"):
                    raise ImtFetchError("Le chemin d'export PASS a été refusé")
                export_url = _with_export_format(export_url)

                exported = self._get(
                    export_url,
                    allowed_origins={PASS_ORIGIN},
                    max_bytes=MAX_EXPORT_BYTES,
                )
                self._ensure_success(exported)
                content_type = exported.headers.get("Content-Type", "text/html").lower()
                if "text/html" not in content_type:
                    raise ImtFetchError("PASS a renvoyé un format d'export inattendu")
                entries = parse_pass_export(exported.text)
            except ImtError:
                raise
            except requests.RequestException as exc:
                raise ImtNetworkError("Impossible de télécharger les notes depuis PASS") from exc

            self.last_profile = None
            if include_profile:
                try:
                    self.last_profile = self.fetch_profile_authenticated()
                except ImtAuthenticationError:
                    raise
                except (ImtError, requests.RequestException) as exc:
                    logger.warning("PASS profile could not be refreshed: %s", type(exc).__name__)
            return entries

    def fetch_profile_authenticated(self) -> PassProfile:
        with self._operation():
            response = self._get(PASS_PROFILE_URL, allowed_origins={PASS_ORIGIN})
            self._ensure_success(response)
            if self._contains_password_form(response.text) or "session perdue" in response.text.lower():
                self.authenticated = False
                raise ImtAuthenticationError("La session IMT n'a pas pu ouvrir la fiche étudiante")
            soup = BeautifulSoup(response.text, "html.parser")
            frame = soup.find("iframe", id="frm0")
            source = frame.get("src") if frame is not None else None
            if not isinstance(source, str) or not source:
                raise ImtFetchError("PASS n'a pas fourni la fiche étudiante")
            profile_url = validate_imt_url(urljoin(response.url, source), {PASS_ORIGIN})
            profile_path = urlsplit(profile_url).path.casefold()
            if not (
                profile_path.startswith("/opdotnet/eplug/annuaire/navigation/dossier/")
                and profile_path.endswith(".opx")
            ):
                raise ImtFetchError("Le chemin de la fiche étudiante PASS a été refusé")
            profile = self._get(profile_url, allowed_origins={PASS_ORIGIN})
            self._ensure_success(profile)
            return parse_pass_profile(profile.text)


class _PassRowCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.rows = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "tr":
            return
        self.rows += 1
        if self.rows > MAX_PASS_ROWS:
            raise ImtFetchError("L'export PASS contient trop de lignes")


def parse_pass_export(content: str) -> list[PassEntry]:
    if len(content.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise ImtFetchError("L'export PASS dépasse la taille autorisée")

    counter = _PassRowCounter()
    counter.feed(content)
    counter.close()

    soup = BeautifulSoup(content, "html.parser", parse_only=SoupStrainer("tr"))
    current_ue = ""
    entries: dict[tuple[str, str, float, bool], PassEntry] = {}
    ue_pattern = re.compile(r"\b[A-Z]{3,4}\d{3}[A-Z0-9]*\b", re.IGNORECASE)

    for row in soup.find_all("tr", limit=MAX_PASS_ROWS):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td", recursive=False)]
        cells = [cell for cell in cells if cell and cell != "\xa0"]
        if not cells:
            continue
        line = _clean(" ".join(cells))
        if "evaluation" in line.lower():
            match = ue_pattern.search(line)
            if match:
                current_ue = match.group(0).upper()
                if len(current_ue) > MAX_UE_CODE_LENGTH:
                    raise ImtFetchError("Un code UE fourni par PASS est trop long")
            continue
        if any(
            "libell" in cell.lower() or "coefficient" in cell.lower() or cell.lower() == "notes"
            for cell in cells
        ):
            continue
        if not current_ue or len(cells) < 4:
            continue

        label, rule, coefficient_text, score_text = cells[:4]
        if "classique" not in rule.lower() and "/20" not in rule:
            continue
        score = _decimal(score_text)
        coefficient = _decimal(coefficient_text)
        if score is None or coefficient is None:
            continue
        if not math.isfinite(score) or not math.isfinite(coefficient):
            raise ImtFetchError("PASS a fourni une valeur numérique non finie")
        if not 0 <= score <= 20 or coefficient <= 0:
            continue
        if coefficient > MAX_NOTE_COEFFICIENT:
            raise ImtFetchError("PASS a fourni un coefficient supérieur à la limite autorisée")
        if len(label) > MAX_NOTE_LABEL_LENGTH:
            raise ImtFetchError("Un libellé de note fourni par PASS est trop long")

        is_resit = _is_resit(label)
        key = (current_ue, label.casefold(), coefficient, is_resit)
        if key not in entries and len(entries) >= MAX_PASS_ENTRIES:
            raise ImtFetchError("L'export PASS contient trop de notes")
        entries[key] = PassEntry(
            ue_code=current_ue,
            label=label,
            score=score,
            coefficient=coefficient,
            is_resit=is_resit,
        )

    logger.info("PASS export parsed: %s notes", len(entries))
    return list(entries.values())


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _profile_field_value(
    soup: BeautifulSoup,
    *,
    id_pattern: str,
    labels: tuple[str, ...],
    exact_label: bool = False,
) -> str | None:
    label = soup.find(id=re.compile(id_pattern, re.IGNORECASE))
    if label is None:
        folded_labels = tuple(_fold(item) for item in labels)
        label = soup.find(
            string=lambda value: bool(
                value
                and (
                    _fold(str(value)).strip().removesuffix(":").strip() in folded_labels
                    if exact_label
                    else any(candidate in _fold(str(value)) for candidate in folded_labels)
                )
            )
        )
    if label is None:
        return None
    node = label if getattr(label, "name", None) else label.parent
    value = node.find_next(
        "div",
        class_=lambda classes: classes
        and "form_fieldValue" in (classes if isinstance(classes, list) else str(classes).split()),
    )
    cleaned = _clean(value.get_text(" ", strip=True)) if value is not None else ""
    if not cleaned or len(cleaned) > 120 or _fold(cleaned) in {"-", "non renseigne", "inconnu"}:
        return None
    return cleaned


def parse_pass_profile(content: str) -> PassProfile:
    if len(content.encode("utf-8")) > MAX_HTML_BYTES:
        raise ImtFetchError("La fiche PASS dépasse la taille autorisée")
    soup = BeautifulSoup(content, "html.parser")
    first_name = _profile_field_value(
        soup,
        id_pattern=r"(?:^|_)PRENOM$",
        labels=("prénom",),
        exact_label=True,
    )
    last_name = _profile_field_value(
        soup,
        id_pattern=r"(?:^|_)NOM$",
        labels=("nom",),
        exact_label=True,
    )
    campus = _profile_field_value(
        soup,
        id_pattern=r"(?:^|_)IMTA_CAMPUS_COUR$",
        labels=("campus courant",),
    )
    program = _profile_field_value(
        soup,
        id_pattern=r"(?:^|_)(?:IMTA_)?CURSUS.*PRIMO.*(?:INSCRIPTION|INSC)$",
        labels=("cursus primo inscription", "cursus de primo-inscription"),
    )
    expected_exit = _profile_field_value(
        soup,
        id_pattern=r"(?:^|_)(?:IMTA_)?DATE.*(?:SORT|SORTIE).*(?:PREV|PREVI)",
        labels=("date prévi. de sortie", "date prevue de sortie", "date prévisionnelle de sortie"),
    )
    promotion_year = None
    if expected_exit:
        match = re.search(r"\b(20\d{2}|21\d{2})\b", expected_exit)
        if match:
            promotion_year = int(match.group(1))
    return PassProfile(
        campus=campus,
        program=program,
        promotion_year=promotion_year,
        first_name=first_name,
        last_name=last_name,
    )
